"""Trusted capture, materialization, and human resolution of T05 suggestions."""

from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import exists, func, select, text
from sqlalchemy.orm import Session

from tc_backend.db.audit import AuditContext, append_audit
from tc_backend.db.cases import CaseAggregate, CaseRepository, TestCaseContent, TestStepInput
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.errors import ResourceNotFoundError
from tc_backend.db.models import (
    GenerationApply,
    GenerationApplyOutcome,
    GenerationChangeAssessment,
    GenerationJob,
    GenerationJobStatus,
    GenerationSuggestion,
    GenerationSuggestionDecision,
    GenerationSuggestionMaterialization,
    GenerationSuggestionResolution,
    GenerationSuggestionTarget,
    SlackWorkItem,
    Tag,
    TestCase,
    TestCasePriority,
    TestCaseSourceType,
    TestCaseStatus,
    TestCaseVersion,
    TestCaseVersionTag,
    TestStep,
    WorkspaceSettings,
)
from tc_backend.generation.reconstruction import canonical_input_digest, seal_generation_input
from tc_backend.repositories.generation_repository import (
    GenerationRepository,
    generation_context_digest,
)

SUGGESTION_SCHEMA_VERSION = "generation-suggestion/1.0.0"
DIFF_SCHEMA_VERSION = "generation-suggestion-diff/1.0.0"
_DIFF_FIELDS = (
    "summary",
    "precondition",
    "steps",
    "expectedResultMode",
    "caseExpectedResult",
    "priority",
    "labels",
    "generationReason",
    "evidenceNodeIds",
    "openQuestions",
    "inferenceOnly",
)
_CANDIDATE_FIELDS = {
    "summary",
    "steps",
    "caseExpectedResult",
    "priority",
    "labels",
    "evidenceNodeIds",
    "generationReason",
    "inferenceOnly",
    "openQuestions",
}


class GenerationSuggestionConflict(RuntimeError):
    def __init__(self, reason: str, suggestion_id: uuid.UUID | None = None) -> None:
        self.reason = reason
        self.suggestion_id = suggestion_id
        super().__init__(f"generation suggestion conflict: {reason}")


def canonical_sha256(value: object) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_suggestion_input(job: GenerationJob) -> dict[str, Any]:
    """Strictly reconstruct a sealed suggestion envelope without live reads."""

    payload = job.generation_input
    if (
        job.generation_input_capture_state != "SEALED"
        or job.generation_input_schema_version != SUGGESTION_SCHEMA_VERSION
        or job.schema_version != SUGGESTION_SCHEMA_VERSION
        or not isinstance(payload, dict)
        or not isinstance(job.generation_input_digest, str)
        or canonical_input_digest(payload).value != job.generation_input_digest
    ):
        raise ValueError("suggestion input seal is invalid")
    required = {
        "inputSchemaVersion",
        "requestedOutputSchemaVersion",
        "generationJobId",
        "generationChangeAssessmentId",
        "baseGenerationInput",
        "suggestionTargets",
    }
    if set(payload) != required:
        raise ValueError("suggestion input fields are invalid")
    if (
        payload["inputSchemaVersion"] != SUGGESTION_SCHEMA_VERSION
        or payload["requestedOutputSchemaVersion"] != SUGGESTION_SCHEMA_VERSION
        or payload["generationJobId"] != _identifier("generation_job", job.id)
    ):
        raise ValueError("suggestion input identity is invalid")
    base = payload["baseGenerationInput"]
    if not isinstance(base, dict):
        raise ValueError("suggestion base input is invalid")
    seal_generation_input(base)
    targets = payload["suggestionTargets"]
    if not isinstance(targets, list) or not targets:
        raise ValueError("suggestion targets are missing")
    refs: set[str] = set()
    lineages: set[str] = set()
    for target in targets:
        if not isinstance(target, dict) or set(target) != {
            "suggestionTargetRef",
            "testCaseId",
            "testCaseVersionId",
            "generationLineageId",
            "targetDigest",
        }:
            raise ValueError("suggestion target shape is invalid")
        ref = target["suggestionTargetRef"]
        lineage = target["generationLineageId"]
        digest = target["targetDigest"]
        if (
            not isinstance(ref, str)
            or not ref.startswith("generation_suggestion_target_")
            or ref in refs
            or not isinstance(lineage, str)
            or lineage in lineages
            or not isinstance(digest, str)
            or len(digest) != 64
        ):
            raise ValueError("suggestion target seal is invalid")
        refs.add(ref)
        lineages.add(lineage)
    return copy.deepcopy(payload)


class GenerationSuggestionService:
    """Transaction-scoped T05 authority; callers own commit/rollback."""

    def __init__(
        self,
        session: Session,
        *,
        workspace_id: uuid.UUID = DEFAULT_WORKSPACE_ID,
        public_web_base_url: str | None = None,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.public_web_base_url = public_web_base_url

    def seal_targets_for_assessment(
        self, assessment: GenerationChangeAssessment
    ) -> tuple[GenerationSuggestionTarget, ...]:
        if assessment.outcome != "REGENERATE" or assessment.selected_generation_job_id is None:
            return ()
        self._lock_workspace()
        work_item = self.session.scalar(
            select(SlackWorkItem)
            .where(SlackWorkItem.id == assessment.slack_work_item_id)
            .with_for_update()
        )
        job = self.session.scalar(
            select(GenerationJob)
            .where(GenerationJob.id == assessment.selected_generation_job_id)
            .with_for_update()
        )
        if (
            job is None
            or work_item is None
            or job.root_job_id != job.id
            or job.slack_work_item_id != assessment.slack_work_item_id
            or job.generation_trigger != "AUTOMATIC"
            or job.status != GenerationJobStatus.QUEUED.value
            or job.schema_version != "1.0.0"
            or job.generation_input_schema_version != "1.0.0"
            or not isinstance(job.generation_input, dict)
        ):
            return ()

        cases = tuple(
            self.session.scalars(
                select(TestCase)
                .join(
                    GenerationApplyOutcome,
                    GenerationApplyOutcome.id == TestCase.generation_created_outcome_id,
                )
                .join(
                    GenerationApply,
                    GenerationApply.id == GenerationApplyOutcome.generation_apply_id,
                )
                .join(GenerationJob, GenerationJob.id == GenerationApply.generation_job_id)
                .where(
                    TestCase.workspace_id == work_item.workspace_id,
                    TestCase.generation_managed.is_(False),
                    TestCase.generation_lineage_id.is_not(None),
                    TestCase.generation_released_at.is_not(None),
                    TestCase.generation_release_reason.is_not(None),
                    TestCase.generation_release_actor.is_not(None),
                    TestCase.generation_release_mutation.is_not(None),
                    TestCase.generation_released_from_version_id.is_not(None),
                    TestCase.deleted_at.is_(None),
                    TestCase.status == "ACTIVE",
                    GenerationApplyOutcome.outcome == "CREATED",
                    GenerationApplyOutcome.test_case_id == TestCase.id,
                    GenerationJob.slack_work_item_id == assessment.slack_work_item_id,
                )
                .order_by(TestCase.id)
                .with_for_update(of=TestCase)
            )
        )
        if not cases:
            return ()

        base_input = copy.deepcopy(job.generation_input)
        existing = base_input.get("existingCases")
        if not isinstance(existing, list):
            return ()
        targets: list[GenerationSuggestionTarget] = []
        exposed: list[dict[str, Any]] = []
        present_case_ids = {
            row.get("testCaseId") for row in existing if isinstance(row, dict)
        }
        for case in cases:
            if (
                case.generation_lineage_id is None
                or case.generation_created_outcome_id is None
            ):
                continue
            version = self.session.get(TestCaseVersion, case.current_version_id)
            if version is None or version.test_case_id != case.id:
                continue
            baseline = self._semantic_snapshot(version)
            target_seal = {
                "workspaceId": _identifier("workspace", case.workspace_id),
                "slackWorkItemId": _identifier("slack_work_item", job.slack_work_item_id),
                "generationChangeAssessmentId": _identifier(
                    "generation_change_assessment", assessment.id
                ),
                "selectedRootJobId": _identifier("generation_job", job.id),
                "testCaseId": _identifier("case", case.id),
                "testCaseVersionId": _identifier("case_version", version.id),
                "caseVersion": version.version_number,
                "caseResourceVersion": case.version,
                "generationLineageId": _identifier(
                    "generation_lineage", case.generation_lineage_id
                ),
                "generationCreatedOutcomeId": _identifier(
                    "generation_apply_outcome", case.generation_created_outcome_id
                ),
                "snapshot": baseline,
            }
            target_digest = canonical_sha256(target_seal)
            target = GenerationSuggestionTarget(
                workspace_id=case.workspace_id,
                slack_work_item_id=job.slack_work_item_id,
                suggestion_target_ref=f"generation_suggestion_target_{uuid.uuid4().hex}",
                source_change_assessment_id=assessment.id,
                selected_root_job_id=job.id,
                test_case_id=case.id,
                generation_lineage_id=case.generation_lineage_id,
                generation_created_outcome_id=case.generation_created_outcome_id,
                baseline_case_version_id=version.id,
                baseline_case_version=version.version_number,
                baseline_case_resource_version=case.version,
                target_snapshot=target_seal,
                target_digest=target_digest,
            )
            self.session.add(target)
            targets.append(target)
            exposed.append(
                {
                    "suggestionTargetRef": target.suggestion_target_ref,
                    "testCaseId": _identifier("case", case.id),
                    "testCaseVersionId": _identifier("case_version", version.id),
                    "generationLineageId": _identifier(
                        "generation_lineage", case.generation_lineage_id
                    ),
                    "targetDigest": target_digest,
                }
            )
            case_id = _identifier("case", case.id)
            if case_id not in present_case_ids:
                existing.append(self._existing_case_input(case, version, len(existing) + 1))
                present_case_ids.add(case_id)

        if not targets:
            return ()
        normalized_base, _ = seal_generation_input(base_input)
        envelope = {
            "inputSchemaVersion": SUGGESTION_SCHEMA_VERSION,
            "requestedOutputSchemaVersion": SUGGESTION_SCHEMA_VERSION,
            "generationJobId": _identifier("generation_job", job.id),
            "generationChangeAssessmentId": _identifier(
                "generation_change_assessment", assessment.id
            ),
            "baseGenerationInput": normalized_base,
            "suggestionTargets": exposed,
        }
        sealed_context = GenerationRepository(self.session).load_context(job.id)
        self.session.execute(
            text("SELECT set_config('tc.generation_suggestion_seal', 'on', true)")
        )
        job.schema_version = SUGGESTION_SCHEMA_VERSION
        job.generation_input_schema_version = SUGGESTION_SCHEMA_VERSION
        job.generation_input = envelope
        job.generation_input_digest = canonical_input_digest(envelope).value
        job.context_digest = generation_context_digest(
            job, cast(Any, sealed_context.entries)
        )
        self.session.flush()
        return tuple(targets)

    def materialize_succeeded_job(
        self, generation_job_id: uuid.UUID
    ) -> tuple[GenerationSuggestion, ...]:
        unlocked_job = self.session.get(GenerationJob, generation_job_id)
        if unlocked_job is None:
            raise ResourceNotFoundError("GENERATION_JOB", generation_job_id)
        self._lock_workspace()
        work_item = self.session.scalar(
            select(SlackWorkItem)
            .where(SlackWorkItem.id == unlocked_job.slack_work_item_id)
            .with_for_update()
        )
        job = self.session.scalar(
            select(GenerationJob)
            .where(GenerationJob.id == generation_job_id)
            .with_for_update()
        )
        if job is None or work_item is None or work_item.workspace_id != self.workspace_id:
            raise ResourceNotFoundError("GENERATION_JOB", generation_job_id)
        if (
            job.status != GenerationJobStatus.SUCCEEDED.value
            or job.schema_version != SUGGESTION_SCHEMA_VERSION
            or not isinstance(job.result, dict)
            or job.context_digest is None
        ):
            raise GenerationSuggestionConflict("SOURCE_PROVENANCE_UNPROVEN")
        envelope = validate_suggestion_input(job)
        assessment_id = _decode_identifier(
            cast(str, envelope["generationChangeAssessmentId"]),
            "generation_change_assessment",
        )
        assessment = self.session.get(GenerationChangeAssessment, assessment_id)
        head = self.session.scalar(
            select(GenerationChangeAssessment)
            .where(GenerationChangeAssessment.slack_work_item_id == job.slack_work_item_id)
            .order_by(GenerationChangeAssessment.current_source_version.desc())
            .limit(1)
            .with_for_update()
        )
        harmless_skip_job = (
            self.session.get(GenerationJob, head.reused_generation_job_id)
            if head is not None
            and head.outcome == "SKIP"
            and head.reused_generation_job_id is not None
            else None
        )
        source_head_is_valid = head is not None and (
            head.id == assessment_id
            or (
                assessment is not None
                and harmless_skip_job is not None
                and harmless_skip_job.root_job_id == job.root_job_id
                and harmless_skip_job.status == GenerationJobStatus.SUCCEEDED.value
                and head.current_fingerprint == assessment.current_fingerprint
                and head.current_contract_config_digest
                == assessment.current_contract_config_digest
            )
        )
        if (
            assessment is None
            or assessment.outcome != "REGENERATE"
            or assessment.selected_generation_job_id != job.root_job_id
            or not source_head_is_valid
        ):
            raise GenerationSuggestionConflict("SOURCE_PROVENANCE_UNPROVEN")
        succeeded = self.session.scalar(
            select(func.count())
            .select_from(GenerationJob)
            .where(
                GenerationJob.root_job_id == job.root_job_id,
                GenerationJob.status == GenerationJobStatus.SUCCEEDED.value,
            )
        )
        if succeeded != 1:
            raise GenerationSuggestionConflict("SOURCE_PROVENANCE_UNPROVEN")
        result = job.result
        if set(result) != {"outputSchemaVersion", "suggestions"} or result.get(
            "outputSchemaVersion"
        ) != SUGGESTION_SCHEMA_VERSION:
            raise GenerationSuggestionConflict("PROVIDER_OUTPUT_INVALID")
        raw_suggestions = result.get("suggestions")
        if not isinstance(raw_suggestions, list) or not raw_suggestions:
            raise GenerationSuggestionConflict("PROVIDER_OUTPUT_INVALID")
        targets = {
            row.suggestion_target_ref: row
            for row in self.session.scalars(
                select(GenerationSuggestionTarget)
                .where(GenerationSuggestionTarget.selected_root_job_id == job.root_job_id)
                .order_by(GenerationSuggestionTarget.id)
                .with_for_update()
            )
        }
        if len(raw_suggestions) != len(targets):
            raise GenerationSuggestionConflict("PROVIDER_OUTPUT_INVALID")
        result_digest = canonical_sha256(result)
        committed_materialization = self.session.scalar(
            select(GenerationSuggestionMaterialization).where(
                GenerationSuggestionMaterialization.generation_job_id == job.id
            )
        )
        if committed_materialization is not None:
            return tuple(
                self.session.scalars(
                    select(GenerationSuggestion)
                    .where(GenerationSuggestion.source_generation_job_id == job.id)
                    .order_by(GenerationSuggestion.generated_case_index)
                )
            )
        created: list[GenerationSuggestion] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_suggestions):
            if not isinstance(raw, dict) or set(raw) != {
                "suggestionTargetRef",
                "candidate",
            }:
                raise GenerationSuggestionConflict("PROVIDER_OUTPUT_INVALID")
            ref = raw.get("suggestionTargetRef")
            if not isinstance(ref, str) or ref in seen or ref not in targets:
                raise GenerationSuggestionConflict("PROVIDER_OUTPUT_INVALID")
            seen.add(ref)
            target = targets[ref]
            replay = self.session.scalar(
                select(GenerationSuggestion).where(
                    GenerationSuggestion.suggestion_target_id == target.id
                )
            )
            if replay is not None:
                created.append(replay)
                continue
            candidate = self._validated_candidate(raw.get("candidate"), envelope)
            case = self.session.scalar(
                select(TestCase)
                .where(TestCase.id == target.test_case_id)
                .with_for_update()
            )
            if case is None:
                raise GenerationSuggestionConflict("SOURCE_PROVENANCE_UNPROVEN")
            if (
                case.current_version_id != target.baseline_case_version_id
                or case.version != target.baseline_case_resource_version
                or case.generation_lineage_id != target.generation_lineage_id
                or case.generation_managed
                or case.deleted_at is not None
            ):
                raise GenerationSuggestionConflict("CASE_VERSION_STALE")
            baseline = cast(dict[str, Any], target.target_snapshot["snapshot"])
            proposed = self._proposal_snapshot(candidate, job, result_digest)
            diff = _diff_snapshot(baseline, proposed)
            if not diff["changedFields"]:
                continue
            previous = self._open_suggestion(case.id, target.generation_lineage_id)
            if previous is not None:
                previous_job = self.session.get(GenerationJob, previous.source_generation_job_id)
                if previous_job is None or (
                    job.generation_version,
                    job.attempt_number,
                ) <= (
                    previous_job.generation_version,
                    previous_job.attempt_number,
                ):
                    continue
            suggestion = GenerationSuggestion(
                workspace_id=target.workspace_id,
                test_case_id=target.test_case_id,
                generation_lineage_id=target.generation_lineage_id,
                suggestion_target_id=target.id,
                source_generation_job_id=job.id,
                source_root_job_id=job.root_job_id,
                source_change_assessment_id=target.source_change_assessment_id,
                generated_case_index=index,
                exact_content_key=canonical_sha256(
                    {
                        "summary": proposed["summary"],
                        "steps": proposed["steps"],
                        "caseExpectedResult": proposed["caseExpectedResult"],
                    }
                ),
                baseline_case_version_id=target.baseline_case_version_id,
                baseline_case_version=target.baseline_case_version,
                baseline_case_resource_version=target.baseline_case_resource_version,
                context_digest=job.context_digest,
                result_digest=result_digest,
                diff_schema_version=DIFF_SCHEMA_VERSION,
                baseline_snapshot=baseline,
                proposed_snapshot=proposed,
                diff_snapshot=diff,
                baseline_digest=canonical_sha256(baseline),
                proposed_digest=canonical_sha256(proposed),
                diff_digest=canonical_sha256(diff),
                supersedes_suggestion_id=previous.id if previous is not None else None,
            )
            self.session.add(suggestion)
            self.session.flush()
            created.append(suggestion)
        materialization_id = uuid.uuid4()
        materialized_at = datetime.now(UTC)
        materialization = GenerationSuggestionMaterialization(
            id=materialization_id,
            workspace_id=self.workspace_id,
            generation_job_id=job.id,
            generation_root_job_id=job.root_job_id,
            generation_change_assessment_id=assessment.id,
            candidate_count=len(targets),
            suggestion_count=len(created),
            no_change_count=len(targets) - len(created),
            result_digest=result_digest,
            receipt={
                "id": _identifier("generation_suggestion_materialization", materialization_id),
                "generationJobId": _identifier("generation_job", job.id),
                "generationRootJobId": _identifier("generation_job", job.root_job_id),
                "generationChangeAssessmentId": _identifier(
                    "generation_change_assessment", assessment.id
                ),
                "candidateCount": len(targets),
                "suggestionCount": len(created),
                "noChangeCount": len(targets) - len(created),
                "automaticOverwriteCount": 0,
                "resultDigest": result_digest,
                "materializedAt": materialized_at.isoformat(),
            },
            materialized_at=materialized_at,
        )
        self.session.add(materialization)
        self.session.flush()
        from tc_backend.generation.slack_notifications import (
            GenerationSlackNotificationService,
        )

        GenerationSlackNotificationService(
            self.session,
            workspace_id=self.workspace_id,
            public_web_base_url=self.public_web_base_url,
        ).capture_suggestion_success(materialization.id)
        self.session.flush()
        return tuple(created)

    def get(self, suggestion_id: uuid.UUID, *, for_update: bool = False) -> GenerationSuggestion:
        statement = select(GenerationSuggestion).where(
            GenerationSuggestion.id == suggestion_id,
            GenerationSuggestion.workspace_id == self.workspace_id,
        )
        if for_update:
            statement = statement.with_for_update()
        suggestion = self.session.scalar(statement)
        if suggestion is None:
            raise ResourceNotFoundError("GENERATION_SUGGESTION", suggestion_id)
        return suggestion

    def list_for_case(self, test_case_id: uuid.UUID) -> tuple[GenerationSuggestion, ...]:
        exists_case = self.session.scalar(
            select(TestCase.id).where(
                TestCase.id == test_case_id,
                TestCase.workspace_id == self.workspace_id,
            )
        )
        if exists_case is None:
            raise ResourceNotFoundError("TEST_CASE", test_case_id)
        return tuple(
            self.session.scalars(
                select(GenerationSuggestion)
                .where(
                    GenerationSuggestion.test_case_id == test_case_id,
                    GenerationSuggestion.workspace_id == self.workspace_id,
                )
                .order_by(GenerationSuggestion.created_at.desc(), GenerationSuggestion.id.desc())
            )
        )

    def state(self, suggestion: GenerationSuggestion) -> str:
        resolution = self.resolution(suggestion.id)
        if resolution is not None:
            return resolution.decision
        superseder = self.superseder(suggestion.id)
        return "SUPERSEDED" if superseder is not None else "OPEN"

    def resolution(
        self, suggestion_id: uuid.UUID
    ) -> GenerationSuggestionResolution | None:
        return self.session.scalar(
            select(GenerationSuggestionResolution).where(
                GenerationSuggestionResolution.suggestion_id == suggestion_id
            )
        )

    def superseder(self, suggestion_id: uuid.UUID) -> GenerationSuggestion | None:
        return self.session.scalar(
            select(GenerationSuggestion).where(
                GenerationSuggestion.supersedes_suggestion_id == suggestion_id
            )
        )

    def apply(
        self,
        suggestion_id: uuid.UUID,
        *,
        expected_case_resource_version: int,
        expected_case_version_id: uuid.UUID,
        change_reason: str,
        audit: AuditContext,
    ) -> tuple[GenerationSuggestion, GenerationSuggestionResolution, CaseAggregate]:
        self._lock_workspace()
        suggestion = self.get(suggestion_id, for_update=True)
        self._require_open(suggestion)
        case = self.session.scalar(
            select(TestCase).where(TestCase.id == suggestion.test_case_id).with_for_update()
        )
        if case is None:
            raise GenerationSuggestionConflict("SOURCE_PROVENANCE_UNPROVEN", suggestion.id)
        if case.deleted_at is not None:
            raise GenerationSuggestionConflict("CASE_DELETED", suggestion.id)
        if case.generation_lineage_id != suggestion.generation_lineage_id or case.generation_managed:
            raise GenerationSuggestionConflict("LINEAGE_MISMATCH", suggestion.id)
        if (
            case.version != suggestion.baseline_case_resource_version
            or case.current_version_id != suggestion.baseline_case_version_id
            or expected_case_resource_version != suggestion.baseline_case_resource_version
            or expected_case_version_id != suggestion.baseline_case_version_id
        ):
            raise GenerationSuggestionConflict("CASE_VERSION_STALE", suggestion.id)
        if (
            canonical_sha256(suggestion.baseline_snapshot) != suggestion.baseline_digest
            or canonical_sha256(suggestion.proposed_snapshot) != suggestion.proposed_digest
            or canonical_sha256(suggestion.diff_snapshot) != suggestion.diff_digest
        ):
            raise GenerationSuggestionConflict("DIFF_SEAL_INVALID", suggestion.id)
        current = self.session.get(TestCaseVersion, case.current_version_id)
        if current is None:
            raise GenerationSuggestionConflict("SOURCE_PROVENANCE_UNPROVEN", suggestion.id)
        proposed = suggestion.proposed_snapshot
        content = TestCaseContent(
            summary=cast(str, proposed["summary"]),
            precondition=cast(str, proposed["precondition"]),
            steps=tuple(
                TestStepInput(
                    precondition=cast(str | None, row.get("precondition")),
                    action=cast(str, row["action"]),
                    expected_result=cast(str | None, row.get("expectedResult")),
                )
                for row in cast(list[dict[str, Any]], proposed["steps"])
            ),
            expected_result_mode=cast(Any, "CASE"),
            expected_result=cast(str, proposed["caseExpectedResult"]),
            status=TestCaseStatus(case.status),
            priority=TestCasePriority(cast(str, proposed["priority"])),
            assignee_label=current.assignee_label,
            tag_names=tuple(cast(list[str], proposed["labels"])),
            source_type=TestCaseSourceType.SLACK_AI,
            source_metadata={
                "type": "SLACK_AI",
                "generationSuggestionId": _identifier(
                    "generation_suggestion", suggestion.id
                ),
                "generationJobId": _identifier(
                    "generation_job", suggestion.source_generation_job_id
                ),
                "contextDigest": suggestion.context_digest,
                "resultDigest": suggestion.result_digest,
                "evidenceNodeIds": proposed["evidenceNodeIds"],
                "sourceRevisionIds": proposed["sourceRevisionIds"],
                "inferenceOnly": proposed["inferenceOnly"],
                "openQuestions": proposed["openQuestions"],
            },
            generation_reason=cast(str, proposed["generationReason"]),
        )
        aggregate = CaseRepository(self.session, workspace_id=self.workspace_id).update_case(
            test_case_id=case.id,
            base_version=case.version,
            content=content,
            change_reason=change_reason,
            audit=audit,
        )
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="GENERATION_SUGGESTION",
            entity_id=suggestion.id,
            action="APPLY_GENERATION_SUGGESTION",
            before={"state": "OPEN"},
            after={"state": "APPLIED", "testCaseId": _identifier("case", case.id)},
            context=audit,
        )
        resolution = self._resolution(
            suggestion,
            decision=GenerationSuggestionDecision.APPLIED,
            actor=audit,
            reason=change_reason,
            observed_resource_version=suggestion.baseline_case_resource_version,
            observed_version_id=suggestion.baseline_case_version_id,
            applied_version_id=aggregate.test_case.current_version_id,
            audit_id=audit_id,
        )
        self.session.flush()
        return suggestion, resolution, aggregate

    def ignore(
        self,
        suggestion_id: uuid.UUID,
        *,
        reason: str,
        audit: AuditContext,
    ) -> tuple[GenerationSuggestion, GenerationSuggestionResolution]:
        self._lock_workspace()
        suggestion = self.get(suggestion_id, for_update=True)
        self._require_open(suggestion)
        case = self.session.scalar(
            select(TestCase).where(TestCase.id == suggestion.test_case_id).with_for_update()
        )
        if case is None:
            raise GenerationSuggestionConflict("SOURCE_PROVENANCE_UNPROVEN", suggestion.id)
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="GENERATION_SUGGESTION",
            entity_id=suggestion.id,
            action="IGNORE_GENERATION_SUGGESTION",
            before={"state": "OPEN"},
            after={"state": "IGNORED"},
            context=audit,
        )
        resolution = self._resolution(
            suggestion,
            decision=GenerationSuggestionDecision.IGNORED,
            actor=audit,
            reason=reason,
            observed_resource_version=case.version,
            observed_version_id=case.current_version_id,
            applied_version_id=None,
            audit_id=audit_id,
        )
        self.session.flush()
        return suggestion, resolution

    def _require_open(self, suggestion: GenerationSuggestion) -> None:
        if self.resolution(suggestion.id) is not None:
            raise GenerationSuggestionConflict("NOT_OPEN", suggestion.id)
        if self.superseder(suggestion.id) is not None:
            raise GenerationSuggestionConflict("SUPERSEDED", suggestion.id)

    def _lock_workspace(self) -> None:
        workspace = self.session.scalar(
            select(WorkspaceSettings.id)
            .where(WorkspaceSettings.id == self.workspace_id)
            .with_for_update()
        )
        if workspace is None:
            raise ResourceNotFoundError("WORKSPACE", self.workspace_id)

    def _resolution(
        self,
        suggestion: GenerationSuggestion,
        *,
        decision: GenerationSuggestionDecision,
        actor: AuditContext,
        reason: str,
        observed_resource_version: int,
        observed_version_id: uuid.UUID,
        applied_version_id: uuid.UUID | None,
        audit_id: uuid.UUID,
    ) -> GenerationSuggestionResolution:
        actor_snapshot = {
            "type": actor.source_type.value,
            "actorLabel": actor.actor_label,
            "externalActorId": actor.external_actor_id,
        }
        resolution = GenerationSuggestionResolution(
            workspace_id=self.workspace_id,
            suggestion_id=suggestion.id,
            decision=decision.value,
            actor=actor_snapshot,
            reason=reason,
            observed_case_resource_version=observed_resource_version,
            observed_case_version_id=observed_version_id,
            applied_case_version_id=applied_version_id,
            audit_id=audit_id,
            receipt={
                "suggestionId": _identifier("generation_suggestion", suggestion.id),
                "decision": decision.value,
            },
            decided_at=datetime.now(UTC),
        )
        self.session.add(resolution)
        return resolution

    def _open_suggestion(
        self, test_case_id: uuid.UUID, lineage_id: uuid.UUID
    ) -> GenerationSuggestion | None:
        Resolution = GenerationSuggestionResolution
        Superseder = GenerationSuggestion
        return self.session.scalar(
            select(GenerationSuggestion)
            .where(
                GenerationSuggestion.test_case_id == test_case_id,
                GenerationSuggestion.generation_lineage_id == lineage_id,
                ~exists().where(Resolution.suggestion_id == GenerationSuggestion.id),
                ~exists().where(
                    Superseder.supersedes_suggestion_id == GenerationSuggestion.id
                ),
            )
            .order_by(GenerationSuggestion.created_at.desc(), GenerationSuggestion.id.desc())
            .limit(1)
            .with_for_update()
        )

    def _semantic_snapshot(self, version: TestCaseVersion) -> dict[str, Any]:
        steps = tuple(
            self.session.scalars(
                select(TestStep)
                .where(TestStep.test_case_version_id == version.id)
                .order_by(TestStep.sequence)
            )
        )
        labels = list(
            self.session.scalars(
                select(Tag.name)
                .join(TestCaseVersionTag, TestCaseVersionTag.tag_id == Tag.id)
                .where(TestCaseVersionTag.test_case_version_id == version.id)
                .order_by(Tag.normalized_name, Tag.id)
            )
        )
        return {
            "summary": version.summary,
            "precondition": version.precondition,
            "steps": [
                {
                    "sequence": row.sequence,
                    "precondition": row.precondition,
                    "action": row.action,
                    "expectedResult": row.expected_result,
                }
                for row in steps
            ],
            "expectedResultMode": version.expected_result_mode,
            "caseExpectedResult": version.expected_result,
            "priority": version.priority,
            "labels": _normalized_set(labels),
            "generationReason": version.generation_reason,
            "evidenceNodeIds": _normalized_set(
                cast(list[str], version.source_metadata.get("evidenceNodeIds", []))
            ),
            "openQuestions": _normalized_set(
                cast(list[str], version.source_metadata.get("openQuestions", []))
            ),
            "inferenceOnly": bool(version.source_metadata.get("inferenceOnly", False)),
        }

    def _existing_case_input(
        self, case: TestCase, version: TestCaseVersion, rank: int
    ) -> dict[str, Any]:
        snapshot = self._semantic_snapshot(version)
        return {
            "nodeId": _identifier("knowledge_node", case.id),
            "testCaseId": _identifier("case", case.id),
            "testCaseVersionId": _identifier("case_version", version.id),
            "caseKey": case.case_key,
            "status": "ACTIVE",
            "summary": snapshot["summary"],
            "precondition": snapshot["precondition"],
            "steps": snapshot["steps"],
            "expectedResultMode": snapshot["expectedResultMode"],
            "caseExpectedResult": snapshot["caseExpectedResult"],
            "priority": snapshot["priority"],
            "labels": snapshot["labels"],
            "rank": rank,
            "selectionReason": "RELEASED_GENERATED_SUGGESTION_TARGET",
        }

    @staticmethod
    def _validated_candidate(value: object, envelope: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != _CANDIDATE_FIELDS:
            raise GenerationSuggestionConflict("PROVIDER_OUTPUT_INVALID")
        if (
            not isinstance(value.get("summary"), str)
            or not str(value["summary"]).strip()
            or len(str(value["summary"])) > 200
            or value.get("priority") not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
            or not isinstance(value.get("caseExpectedResult"), str)
            or not str(value["caseExpectedResult"]).strip()
            or not isinstance(value.get("generationReason"), str)
            or not str(value["generationReason"]).strip()
            or not isinstance(value.get("inferenceOnly"), bool)
        ):
            raise GenerationSuggestionConflict("PROVIDER_OUTPUT_INVALID")
        steps = value.get("steps")
        if not isinstance(steps, list) or not steps:
            raise GenerationSuggestionConflict("PROVIDER_OUTPUT_INVALID")
        for index, step in enumerate(steps, 1):
            if (
                not isinstance(step, dict)
                or set(step) - {"sequence", "precondition", "action"}
                or set(step) < {"sequence", "action"}
                or step.get("sequence") != index
                or not isinstance(step.get("action"), str)
                or not str(step["action"]).strip()
                or ("precondition" in step and not isinstance(step["precondition"], str))
            ):
                raise GenerationSuggestionConflict("PROVIDER_OUTPUT_INVALID")
        for field in ("labels", "evidenceNodeIds", "openQuestions"):
            values = value.get(field)
            if (
                not isinstance(values, list)
                or any(not isinstance(item, str) or not item.strip() for item in values)
                or len(values) != len(set(values))
            ):
                raise GenerationSuggestionConflict("PROVIDER_OUTPUT_INVALID")
        if not cast(list[str], value["evidenceNodeIds"]):
            raise GenerationSuggestionConflict("PROVIDER_OUTPUT_INVALID")
        base = cast(dict[str, Any], envelope["baseGenerationInput"])
        active_ids = {
            row.get("nodeId")
            for row in cast(list[dict[str, Any]], base.get("activeKnowledge", []))
        }
        if any(
            evidence not in active_ids
            for evidence in cast(list[str], value["evidenceNodeIds"])
        ):
            raise GenerationSuggestionConflict("PROVIDER_OUTPUT_INVALID")
        return copy.deepcopy(value)

    @staticmethod
    def _proposal_snapshot(
        candidate: Mapping[str, Any], job: GenerationJob, result_digest: str
    ) -> dict[str, Any]:
        preconditions: list[str] = []
        seen: set[str] = set()
        steps: list[dict[str, Any]] = []
        for row in cast(list[dict[str, Any]], candidate["steps"]):
            precondition = cast(str | None, row.get("precondition"))
            if precondition is not None:
                key = unicodedata.normalize("NFKC", precondition.strip()).casefold()
                if key not in seen:
                    seen.add(key)
                    preconditions.append(precondition.strip())
            steps.append(
                {
                    "sequence": row["sequence"],
                    "precondition": precondition,
                    "action": row["action"],
                    "expectedResult": None,
                }
            )
        base = cast(dict[str, Any], cast(dict[str, Any], job.generation_input)["baseGenerationInput"])
        slack = cast(dict[str, Any], base["slackWorkItem"])
        return {
            "summary": candidate["summary"],
            "precondition": "\n".join(preconditions)
            if preconditions
            else "[생성 결과에 명시된 공통 사전조건 없음]",
            "steps": steps,
            "expectedResultMode": "CASE",
            "caseExpectedResult": candidate["caseExpectedResult"],
            "priority": candidate["priority"],
            "labels": _normalized_set(cast(list[str], candidate["labels"])),
            "generationReason": candidate["generationReason"],
            "evidenceNodeIds": _normalized_set(
                cast(list[str], candidate["evidenceNodeIds"])
            ),
            "openQuestions": _normalized_set(cast(list[str], candidate["openQuestions"])),
            "inferenceOnly": candidate["inferenceOnly"],
            "sourceRevisionIds": list(cast(list[str], slack["sourceRevisionIds"])),
            "sourceResultDigest": result_digest,
        }


def _diff_snapshot(
    baseline: Mapping[str, Any], proposed: Mapping[str, Any]
) -> dict[str, Any]:
    changed = [field for field in _DIFF_FIELDS if baseline.get(field) != proposed.get(field)]
    return {
        "schemaVersion": DIFF_SCHEMA_VERSION,
        "changedFields": changed,
        "changes": [
            {"path": field, "before": baseline.get(field), "after": proposed.get(field)}
            for field in changed
        ],
    }


def _normalized_set(values: list[str]) -> list[str]:
    by_key: dict[str, str] = {}
    for value in values:
        display = " ".join(unicodedata.normalize("NFKC", value).strip().split())
        if display:
            by_key.setdefault(display.casefold(), display)
    return [by_key[key] for key in sorted(by_key)]


def _identifier(prefix: str, value: uuid.UUID) -> str:
    return f"{prefix}_{value.hex}"


def _decode_identifier(value: str, prefix: str) -> uuid.UUID:
    expected = f"{prefix}_"
    if not value.startswith(expected):
        raise ValueError("identifier prefix is invalid")
    return uuid.UUID(value.removeprefix(expected))
