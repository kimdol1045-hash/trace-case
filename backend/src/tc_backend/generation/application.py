"""Trusted, transaction-scoped materialization of stored generation results."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from tc_backend.db.audit import AuditContext
from tc_backend.db.cases import CaseRepository, TestCaseContent, TestStepInput
from tc_backend.db.constants import AI_INBOX_SYSTEM_KIND, DEFAULT_WORKSPACE_ID
from tc_backend.db.errors import ResourceNotFoundError, WorkItemKeyConflictError
from tc_backend.db.models import (
    AuditLog,
    Folder,
    FolderAlias,
    GenerationApply,
    GenerationApplyOutcome,
    GenerationJob,
    GenerationJobStatus,
    KnowledgeNode,
    OutboxEvent,
    OutboxStatus,
    SlackWorkItem,
    TestCase,
    TestCasePriority,
    TestCaseSourceType,
    TestCaseStatus,
    TestCaseVersion,
    TestStep,
    WorkItem,
    WorkspaceSettings,
)
from tc_backend.db.models import (
    GenerationContext as StoredGenerationContext,
)
from tc_backend.generation.deduplication import (
    DeduplicationStep,
    ExistingCaseInput,
    GeneratedCaseInput,
)
from tc_backend.generation.errors import GenerationValidationError
from tc_backend.generation.mapping import (
    CategoryMappingCandidate,
    CreateNewParentDecision,
    MappingMethod,
    ParentWorkItemCandidate,
    ReuseParentDecision,
    SlackSourceIdentity,
)
from tc_backend.generation.models import (
    ContextEvidence,
    GeneratedTestCase,
    GenerationContext,
    UnsupportedClaimPolicy,
    ValidatedGeneration,
)
from tc_backend.generation.planning import (
    GenerationPlacementPlan,
    GenerationPlacementPlanner,
    GenerationPlanningContext,
    GenerationPrecomputedVectors,
)
from tc_backend.generation.semantic_duplicates import (
    SealedSemanticDuplicateInput,
    SemanticDuplicateSealError,
    build_sealed_semantic_duplicate_input,
    validate_sealed_semantic_duplicate_input,
    with_semantic_vector,
)
from tc_backend.generation.validator import GenerationResponseValidator
from tc_backend.repositories.generation_repository import (
    GenerationJobStateConflict,
    GenerationRepository,
    UngroundedGenerationContext,
)

ApplyWriteStage = Literal[
    "CREATE_PARENT",
    "CREATE_CASE",
    "CREATE_VERSION",
    "CREATE_STEPS",
    "RECORD_OUTCOME",
    "AUDIT",
    "OUTBOX",
    "NOTIFICATION",
]
GenerationApplyFault = Callable[[ApplyWriteStage, int | None], None]


@dataclass(frozen=True, slots=True)
class CategoryApplyPlacement:
    category_id: uuid.UUID
    expected_resource_version: int
    method: str
    score: float | None


@dataclass(frozen=True, slots=True)
class ReuseParentApplyPlacement:
    work_item_id: uuid.UUID
    expected_resource_version: int
    expected_category_id: uuid.UUID
    method: str
    score: float | None
    decision: Literal["REUSE_EXISTING"] = "REUSE_EXISTING"


@dataclass(frozen=True, slots=True)
class CreateParentApplyPlacement:
    expected_work_item_key: str
    expected_summary: str
    category_id: uuid.UUID
    method: Literal["CREATE_NEW_PARENT"] = "CREATE_NEW_PARENT"
    decision: Literal["CREATE_NEW"] = "CREATE_NEW"


type ParentApplyPlacement = ReuseParentApplyPlacement | CreateParentApplyPlacement


@dataclass(frozen=True, slots=True)
class CreateCaseApplyPlacement:
    generated_case_index: int
    exact_content_key: str
    decision: Literal["CREATE_NEW"] = "CREATE_NEW"


@dataclass(frozen=True, slots=True)
class ReuseCaseApplyPlacement:
    generated_case_index: int
    exact_content_key: str
    test_case_id: uuid.UUID
    expected_resource_version: int
    expected_current_version_id: uuid.UUID
    expected_current_version: int
    match_reason: str
    decision: Literal["REUSE_EXISTING"] = "REUSE_EXISTING"


type CaseApplyPlacement = CreateCaseApplyPlacement | ReuseCaseApplyPlacement


@dataclass(frozen=True, slots=True)
class GenerationApplyCommand:
    job_id: uuid.UUID
    idempotency_key: str
    expected_context_digest: str
    expected_result_digest: str
    category: CategoryApplyPlacement
    parent: ParentApplyPlacement
    cases: tuple[CaseApplyPlacement, ...]
    placement_schema_version: Literal["1.0.0"] = "1.0.0"


class GenerationStoredResultInvalid(RuntimeError):
    def __init__(self, job_id: uuid.UUID, stage: str, reason: str) -> None:
        self.job_id = job_id
        self.stage = stage
        self.reason = reason
        super().__init__("stored generation result failed trusted validation")


class GenerationPlacementConflict(RuntimeError):
    def __init__(
        self,
        job_id: uuid.UUID,
        reason: str,
        *,
        generated_case_index: int | None = None,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
    ) -> None:
        self.job_id = job_id
        self.reason = reason
        self.generated_case_index = generated_case_index
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__("generation placement is stale or cannot be reproduced")


class GenerationJobAlreadyApplied(RuntimeError):
    def __init__(self, job_id: uuid.UUID, apply_id: uuid.UUID) -> None:
        self.job_id = job_id
        self.apply_id = apply_id
        super().__init__("generation job already has a committed apply receipt")


class GenerationApplyIdempotencyConflict(RuntimeError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__("generation apply idempotency key conflicts with an earlier receipt")


class GenerationApplyTransactionFailed(RuntimeError):
    def __init__(self, job_id: uuid.UUID, stage: ApplyWriteStage) -> None:
        self.job_id = job_id
        self.stage = stage
        super().__init__("generation apply transaction failed")


class GenerationSuggestionApplyConflict(RuntimeError):
    def __init__(self, job_id: uuid.UUID) -> None:
        self.job_id = job_id
        self.reason = "SUGGESTION_JOB_NOT_WHOLE_BATCH_APPLICABLE"
        super().__init__("suggestion jobs cannot use whole-batch generation apply")


@dataclass(frozen=True, slots=True)
class _LedgerCase:
    test_case: TestCase
    version: TestCaseVersion
    steps: tuple[TestStep, ...]


@dataclass(frozen=True, slots=True)
class GenerationApplyDraft:
    job_id: uuid.UUID
    context_digest: str
    result_digest: str
    placement: dict[str, object]
    semantic_duplicate_seal: dict[str, object]


def automatic_apply_command(draft: GenerationApplyDraft) -> GenerationApplyCommand:
    """Turn the server-owned plan into an idempotent automatic apply command."""

    placement = draft.placement
    category = cast(dict[str, Any], placement["category"])
    raw_parent = cast(dict[str, Any], placement["parent"])
    raw_cases = cast(list[dict[str, Any]], placement["cases"])
    if raw_parent["decision"] == "REUSE_EXISTING":
        parent: ParentApplyPlacement = ReuseParentApplyPlacement(
            work_item_id=_decode_identifier(cast(str, raw_parent["workItemId"]), "work_item"),
            expected_resource_version=cast(int, raw_parent["expectedResourceVersion"]),
            expected_category_id=_decode_identifier(
                cast(str, raw_parent["expectedCategoryId"]), "folder"
            ),
            method=cast(str, raw_parent["method"]),
            score=cast(float | None, raw_parent.get("score")),
        )
    else:
        parent = CreateParentApplyPlacement(
            expected_work_item_key=cast(str, raw_parent["expectedWorkItemKey"]),
            expected_summary=cast(str, raw_parent["expectedSummary"]),
            category_id=_decode_identifier(cast(str, raw_parent["categoryId"]), "folder"),
        )
    cases: list[CaseApplyPlacement] = []
    for raw in raw_cases:
        if raw["decision"] == "REUSE_EXISTING":
            cases.append(
                ReuseCaseApplyPlacement(
                    generated_case_index=cast(int, raw["generatedCaseIndex"]),
                    exact_content_key=cast(str, raw["exactContentKey"]),
                    test_case_id=_decode_identifier(cast(str, raw["testCaseId"]), "case"),
                    expected_resource_version=cast(int, raw["expectedResourceVersion"]),
                    expected_current_version_id=_decode_identifier(
                        cast(str, raw["expectedCurrentVersionId"]), "case_version"
                    ),
                    expected_current_version=cast(int, raw["expectedCurrentVersion"]),
                    match_reason=cast(str, raw["matchReason"]),
                )
            )
        else:
            cases.append(
                CreateCaseApplyPlacement(
                    generated_case_index=cast(int, raw["generatedCaseIndex"]),
                    exact_content_key=cast(str, raw["exactContentKey"]),
                )
            )
    return GenerationApplyCommand(
        job_id=draft.job_id,
        idempotency_key=f"automatic-generation-apply:{draft.job_id}",
        expected_context_digest=draft.context_digest,
        expected_result_digest=draft.result_digest,
        category=CategoryApplyPlacement(
            category_id=_decode_identifier(cast(str, category["categoryId"]), "folder"),
            expected_resource_version=cast(int, category["expectedResourceVersion"]),
            method=cast(str, category["method"]),
            score=cast(float | None, category.get("score")),
        ),
        parent=parent,
        cases=tuple(cases),
    )


@dataclass(frozen=True, slots=True)
class _PreparedApplyPlacement:
    entries: tuple[StoredGenerationContext, ...]
    generation: ValidatedGeneration
    categories: tuple[Folder, ...]
    work_items: tuple[WorkItem, ...]
    ledger_cases: dict[uuid.UUID, _LedgerCase]
    planning_context: GenerationPlanningContext
    plan: GenerationPlacementPlan
    workspace: WorkspaceSettings
    semantic_duplicate_input: SealedSemanticDuplicateInput


class GenerationApplicationService:
    """Apply one immutable result; the caller owns the surrounding transaction."""

    def __init__(
        self,
        session: Session,
        *,
        workspace_id: uuid.UUID = DEFAULT_WORKSPACE_ID,
        fault: GenerationApplyFault | None = None,
        public_web_base_url: str | None = None,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self._fault = fault
        self.public_web_base_url = public_web_base_url

    def validate_result(
        self,
        job_id: uuid.UUID,
        result: Mapping[str, object],
    ) -> None:
        """Validate an automatic candidate before it may become terminal success."""

        job = self.session.scalar(
            select(GenerationJob).where(GenerationJob.id == job_id).with_for_update()
        )
        if job is None:
            raise ResourceNotFoundError("GENERATION_JOB", job_id)
        if job.status != GenerationJobStatus.RUNNING.value:
            raise GenerationJobStateConflict(
                job.id,
                "VALIDATE",
                job.status,
                (GenerationJobStatus.RUNNING.value,),
            )
        if job.schema_version != "1.0.0" or job.context_digest is None:
            raise GenerationStoredResultInvalid(job.id, "SCHEMA", "OUTPUT_VERSION_MISMATCH")
        _validate_stored_shape(job.id, result)
        try:
            persisted = GenerationRepository(self.session).load_context(job.id)
        except UngroundedGenerationContext as error:
            raise GenerationStoredResultInvalid(
                job.id, "GROUNDING", "EVIDENCE_NOT_IN_SEALED_CONTEXT"
            ) from error
        self._lock_workspace()
        categories = self._locked_categories()
        context = self._trusted_context(
            persisted.entries,
            categories,
            job.id,
            ontology_schema_id=job.ontology_schema_id,
        )
        try:
            generation = GenerationResponseValidator(
                unsupported_claim_policy=UnsupportedClaimPolicy.REJECT
            ).validate(result, context)
        except GenerationValidationError as error:
            raise GenerationStoredResultInvalid(
                job.id, "GROUNDING", _grounding_reason(str(error))
            ) from error
        _validate_materializable(job.id, generation)
        result_digest = canonical_sha256(result)
        for generated in generation.cases:
            _source_provenance(
                job,
                generated,
                entries=persisted.entries,
                result_digest=result_digest,
            )

    def plan(self, job_id: uuid.UUID) -> GenerationApplyDraft:
        """Return a current server-owned draft; apply remains the write authority."""

        job = self.session.scalar(
            select(GenerationJob).where(GenerationJob.id == job_id).with_for_update()
        )
        if job is None:
            raise ResourceNotFoundError("GENERATION_JOB", job_id)
        committed = self.session.scalar(
            select(GenerationApply).where(GenerationApply.generation_job_id == job.id)
        )
        if committed is not None:
            raise GenerationJobAlreadyApplied(job.id, committed.id)
        if job.status != GenerationJobStatus.SUCCEEDED.value:
            raise GenerationJobStateConflict(
                job.id,
                "APPLY",
                job.status,
                (GenerationJobStatus.SUCCEEDED.value,),
            )
        if job.result is None or job.context_digest is None:
            raise GenerationStoredResultInvalid(job.id, "SCHEMA", "SCHEMA_INVALID")
        if job.schema_version == "generation-suggestion/1.0.0":
            raise GenerationSuggestionApplyConflict(job.id)
        if job.schema_version != "1.0.0":
            raise GenerationStoredResultInvalid(job.id, "SCHEMA", "OUTPUT_VERSION_MISMATCH")
        try:
            persisted = GenerationRepository(self.session).load_context(job.id)
        except UngroundedGenerationContext as error:
            raise GenerationStoredResultInvalid(
                job.id, "GROUNDING", "EVIDENCE_NOT_IN_SEALED_CONTEXT"
            ) from error
        result_digest = canonical_sha256(job.result)
        prepared = self._prepare_placement(
            job,
            persisted.entries,
            result_digest=result_digest,
        )
        for generated in prepared.generation.cases:
            _source_provenance(
                job,
                generated,
                entries=prepared.entries,
                result_digest=result_digest,
            )
        return GenerationApplyDraft(
            job_id=job.id,
            context_digest=job.context_digest,
            result_digest=result_digest,
            placement=self._authoritative_placement(prepared),
            semantic_duplicate_seal=prepared.semantic_duplicate_input.public_seal(),
        )

    def apply(self, command: GenerationApplyCommand, *, audit: AuditContext) -> GenerationApply:
        placement = _normalized_placement(command)
        request_digest = canonical_sha256(
            {
                "idempotencyKey": command.idempotency_key,
                "jobId": _identifier("generation_job", command.job_id),
                "expectedContextDigest": command.expected_context_digest,
                "expectedResultDigest": command.expected_result_digest,
                "placement": placement,
            }
        )
        job = self.session.scalar(
            select(GenerationJob).where(GenerationJob.id == command.job_id).with_for_update()
        )
        if job is None:
            raise ResourceNotFoundError("GENERATION_JOB", command.job_id)
        if job.schema_version == "generation-suggestion/1.0.0":
            raise GenerationSuggestionApplyConflict(job.id)

        replay = self.session.scalar(
            select(GenerationApply).where(GenerationApply.generation_job_id == job.id)
        )
        if replay is not None:
            if (
                replay.idempotency_key == command.idempotency_key
                and replay.request_digest == request_digest
            ):
                return replay
            raise GenerationJobAlreadyApplied(job.id, replay.id)
        key_owner = self.session.scalar(
            select(GenerationApply).where(
                GenerationApply.idempotency_key == command.idempotency_key
            )
        )
        if key_owner is not None:
            raise GenerationApplyIdempotencyConflict(command.idempotency_key)
        if job.status != GenerationJobStatus.SUCCEEDED.value:
            raise GenerationJobStateConflict(
                job.id,
                "APPLY",
                job.status,
                (GenerationJobStatus.SUCCEEDED.value,),
            )
        if job.result is None or job.context_digest is None:
            raise GenerationStoredResultInvalid(job.id, "SCHEMA", "SCHEMA_INVALID")
        if job.schema_version != "1.0.0":
            raise GenerationStoredResultInvalid(job.id, "SCHEMA", "OUTPUT_VERSION_MISMATCH")

        try:
            persisted = GenerationRepository(self.session).load_context(job.id)
        except UngroundedGenerationContext as error:
            raise GenerationStoredResultInvalid(
                job.id, "GROUNDING", "EVIDENCE_NOT_IN_SEALED_CONTEXT"
            ) from error

        result_digest = canonical_sha256(job.result)
        if command.expected_context_digest != job.context_digest:
            raise GenerationPlacementConflict(job.id, "CONTEXT_DIGEST_MISMATCH")
        if command.expected_result_digest != result_digest:
            raise GenerationPlacementConflict(job.id, "RESULT_DIGEST_MISMATCH")

        prepared = self._prepare_placement(
            job,
            persisted.entries,
            result_digest=result_digest,
        )
        try:
            validate_sealed_semantic_duplicate_input(
                prepared.semantic_duplicate_input,
                source_workspace_id=_identifier("workspace", prepared.workspace.id),
                source_workspace_version=prepared.workspace.version,
                context_digest=job.context_digest,
                result_digest=result_digest,
            )
        except SemanticDuplicateSealError as error:
            raise GenerationPlacementConflict(
                job.id,
                "SEMANTIC_DUPLICATE_SEAL_MISMATCH",
            ) from error
        placement = {
            **placement,
            "semanticDuplicateSeal": prepared.semantic_duplicate_input.public_seal(),
        }
        placement_digest = canonical_sha256(placement)
        self._validate_placement(
            command,
            plan=prepared.plan,
            generation=prepared.generation,
            categories=prepared.categories,
            work_items=prepared.work_items,
            ledger_cases=prepared.ledger_cases,
            planning_context=prepared.planning_context,
        )
        for generated in prepared.generation.cases:
            _source_provenance(
                job,
                generated,
                entries=prepared.entries,
                result_digest=result_digest,
            )

        try:
            return self._materialize(
                command,
                job=job,
                entries=prepared.entries,
                generation=prepared.generation,
                ledger_cases=prepared.ledger_cases,
                placement=placement,
                placement_digest=placement_digest,
                request_digest=request_digest,
                result_digest=result_digest,
                audit=audit,
            )
        except GenerationApplyTransactionFailed:
            raise
        except WorkItemKeyConflictError as error:
            raise GenerationPlacementConflict(job.id, "PARENT_CONFLICT") from error

    def _prepare_placement(
        self,
        job: GenerationJob,
        entries: tuple[StoredGenerationContext, ...],
        *,
        result_digest: str,
    ) -> _PreparedApplyPlacement:
        if job.result is None or job.context_digest is None:
            raise GenerationStoredResultInvalid(job.id, "SCHEMA", "SCHEMA_INVALID")
        _validate_stored_shape(job.id, job.result)
        workspace = self._lock_workspace()
        categories = self._locked_categories()
        category_aliases = self._category_aliases(categories)
        validation_context = self._trusted_context(
            entries,
            categories,
            job.id,
            ontology_schema_id=job.ontology_schema_id,
        )
        try:
            generation = GenerationResponseValidator(
                unsupported_claim_policy=UnsupportedClaimPolicy.REJECT
            ).validate(job.result, validation_context)
        except GenerationValidationError as error:
            raise GenerationStoredResultInvalid(
                job.id,
                "GROUNDING",
                _grounding_reason(str(error)),
            ) from error
        _validate_materializable(job.id, generation)

        slack_item = self.session.scalar(
            select(SlackWorkItem)
            .where(SlackWorkItem.id == job.slack_work_item_id)
            .with_for_update()
        )
        if slack_item is None:
            raise GenerationStoredResultInvalid(job.id, "MATERIALIZATION", "NOT_MATERIALIZABLE")
        work_items = tuple(
            self.session.scalars(
                select(WorkItem)
                .where(WorkItem.workspace_id == self.workspace_id)
                .order_by(WorkItem.id)
                .with_for_update()
            )
        )
        ledger_cases = self._locked_cases()
        planning_context = _planning_context(slack_item, entries)
        generated_inputs = tuple(_generated_case_input(value) for value in generation.cases)
        existing_inputs = tuple(_existing_case(value) for value in ledger_cases.values())
        try:
            semantic_input = build_sealed_semantic_duplicate_input(
                generated_inputs,
                existing_inputs,
                source_workspace_id=_identifier("workspace", workspace.id),
                source_workspace_version=workspace.version,
                context_digest=job.context_digest,
                result_digest=result_digest,
            )
            existing_vector_by_id = dict(semantic_input.existing_vectors)
            sealed_existing_inputs = tuple(
                with_semantic_vector(value, existing_vector_by_id[value.case_id])
                for value in existing_inputs
            )
            plan = GenerationPlacementPlanner(
                case_semantic_threshold=semantic_input.policy.threshold
            ).plan(
                generation,
                context=planning_context,
                existing_cases=sealed_existing_inputs,
                category_candidates=tuple(
                    CategoryMappingCandidate(
                        category_id=_identifier("folder", category.id),
                        canonical_name=category.name,
                        aliases=category_aliases.get(category.id, ()),
                        active=True,
                        is_system_uncategorized=(category.system_kind == AI_INBOX_SYSTEM_KIND),
                    )
                    for category in categories
                ),
                parent_work_item_candidates=self._parent_candidates(work_items, slack_item),
                vectors=GenerationPrecomputedVectors(
                    case_vectors=semantic_input.generated_vectors,
                ),
            )
            validate_sealed_semantic_duplicate_input(
                semantic_input,
                source_workspace_id=_identifier("workspace", workspace.id),
                source_workspace_version=workspace.version,
                context_digest=job.context_digest,
                result_digest=result_digest,
            )
        except (ValueError, SemanticDuplicateSealError) as error:
            raise GenerationPlacementConflict(job.id, "PLACEMENT_NOT_REPRODUCIBLE") from error
        return _PreparedApplyPlacement(
            entries=entries,
            generation=generation,
            categories=categories,
            work_items=work_items,
            ledger_cases=ledger_cases,
            planning_context=planning_context,
            plan=plan,
            workspace=workspace,
            semantic_duplicate_input=semantic_input,
        )

    def _authoritative_placement(
        self,
        prepared: _PreparedApplyPlacement,
    ) -> dict[str, object]:
        category_by_id = {_identifier("folder", row.id): row for row in prepared.categories}
        category_decision = prepared.plan.placement.category
        category = category_by_id[category_decision.category_id]
        category_value: dict[str, object] = {
            "categoryId": category_decision.category_id,
            "expectedResourceVersion": category.version,
            "method": category_decision.method.value,
            "score": category_decision.score,
        }

        work_item_by_id = {_identifier("work_item", row.id): row for row in prepared.work_items}
        parent_decision = prepared.plan.placement.parent
        if isinstance(parent_decision, ReuseParentDecision):
            parent = work_item_by_id[parent_decision.work_item_id]
            parent_value: dict[str, object] = {
                "decision": "REUSE_EXISTING",
                "workItemId": parent_decision.work_item_id,
                "expectedResourceVersion": parent.version,
                "expectedCategoryId": _identifier("folder", parent.category_id),
                "method": parent_decision.method.value,
                "score": parent_decision.score,
            }
        else:
            parent_value = {
                "decision": "CREATE_NEW",
                "expectedWorkItemKey": parent_decision.source_key,
                "expectedSummary": parent_decision.title,
                "categoryId": parent_decision.category_id,
                "method": parent_decision.method.value,
            }

        ledger_by_id = {
            _identifier("case", row.test_case.id): row for row in prepared.ledger_cases.values()
        }
        case_values: list[dict[str, object]] = []
        for planned in prepared.plan.cases:
            base: dict[str, object] = {
                "generatedCaseIndex": planned.generated_case_index,
                "exactContentKey": planned.exact_content_key,
            }
            if not planned.reuse_candidates:
                case_values.append({**base, "decision": "CREATE_NEW"})
                continue
            candidate = planned.reuse_candidates[0]
            current = ledger_by_id[candidate.case_id]
            case_values.append(
                {
                    **base,
                    "decision": "REUSE_EXISTING",
                    "testCaseId": candidate.case_id,
                    "expectedResourceVersion": current.test_case.version,
                    "expectedCurrentVersionId": _identifier("case_version", current.version.id),
                    "expectedCurrentVersion": current.version.version_number,
                    "matchReason": candidate.reason.value,
                }
            )
        return {
            "placementSchemaVersion": "1.0.0",
            "category": category_value,
            "parent": parent_value,
            "cases": case_values,
        }

    def _lock_workspace(self) -> WorkspaceSettings:
        workspace = self.session.scalar(
            select(WorkspaceSettings)
            .where(WorkspaceSettings.id == self.workspace_id)
            .with_for_update()
        )
        if workspace is None:
            raise ResourceNotFoundError("WORKSPACE", self.workspace_id)
        return workspace

    def _locked_categories(self) -> tuple[Folder, ...]:
        return tuple(
            self.session.scalars(
                select(Folder)
                .where(
                    Folder.workspace_id == self.workspace_id,
                    Folder.parent_id.is_(None),
                    Folder.deleted_at.is_(None),
                )
                .order_by(Folder.id)
                .with_for_update()
            )
        )

    def _category_aliases(self, categories: tuple[Folder, ...]) -> dict[uuid.UUID, tuple[str, ...]]:
        active_ids = tuple(category.id for category in categories)
        if not active_ids:
            return {}
        aliases: dict[uuid.UUID, list[str]] = defaultdict(list)
        for row in self.session.scalars(
            select(FolderAlias)
            .where(
                FolderAlias.workspace_id == self.workspace_id,
                FolderAlias.folder_id.in_(active_ids),
            )
            .order_by(FolderAlias.folder_id, FolderAlias.normalized_alias, FolderAlias.id)
        ):
            aliases[row.folder_id].append(row.alias)
        return {key: tuple(values) for key, values in aliases.items()}

    def _trusted_context(
        self,
        entries: tuple[StoredGenerationContext, ...],
        categories: tuple[Folder, ...],
        job_id: uuid.UUID,
        *,
        ontology_schema_id: uuid.UUID,
    ) -> GenerationContext:
        node_ids = tuple(dict.fromkeys(row.node_id for row in entries if row.node_id is not None))
        current_nodes = {
            row.id: row
            for row in self.session.scalars(
                select(KnowledgeNode).where(KnowledgeNode.id.in_(node_ids)).with_for_update()
            )
        }
        evidence: list[ContextEvidence] = []
        seen: set[uuid.UUID] = set()
        for entry in entries:
            if entry.node_id is None or entry.node_id in seen:
                continue
            seen.add(entry.node_id)
            node = current_nodes.get(entry.node_id)
            if (
                node is None
                or node.status != "ACTIVE"
                or node.ontology_schema_id != ontology_schema_id
                or (node.valid_from is not None and node.valid_from > datetime.now(UTC))
                or node.valid_to is not None
            ):
                raise GenerationStoredResultInvalid(job_id, "GROUNDING", "EVIDENCE_NOT_ACTIVE")
            evidence.append(
                ContextEvidence(
                    node_id=_identifier("knowledge_node", entry.node_id),
                    status=node.status,
                    content=json.dumps(
                        entry.node_snapshot or {},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    active=True,
                )
            )
        if not evidence:
            raise GenerationStoredResultInvalid(
                job_id, "GROUNDING", "EVIDENCE_NOT_IN_SEALED_CONTEXT"
            )
        return GenerationContext(
            evidence=tuple(evidence),
            category_ids=frozenset(_identifier("folder", row.id) for row in categories),
        )

    def _locked_cases(self) -> dict[uuid.UUID, _LedgerCase]:
        cases = tuple(
            self.session.scalars(
                select(TestCase)
                .where(
                    TestCase.workspace_id == self.workspace_id,
                    TestCase.deleted_at.is_(None),
                    TestCase.status == TestCaseStatus.ACTIVE.value,
                )
                .order_by(TestCase.id)
                .with_for_update()
            )
        )
        version_ids = tuple(row.current_version_id for row in cases)
        versions = {
            row.id: row
            for row in self.session.scalars(
                select(TestCaseVersion).where(TestCaseVersion.id.in_(version_ids)).with_for_update()
            )
        }
        steps: dict[uuid.UUID, list[TestStep]] = defaultdict(list)
        if version_ids:
            for row in self.session.scalars(
                select(TestStep)
                .where(TestStep.test_case_version_id.in_(version_ids))
                .order_by(TestStep.test_case_version_id, TestStep.sequence)
            ):
                steps[row.test_case_version_id].append(row)
        result: dict[uuid.UUID, _LedgerCase] = {}
        for test_case in cases:
            version = versions.get(test_case.current_version_id)
            if (
                version is None
                or version.test_case_id != test_case.id
                or version.status != TestCaseStatus.ACTIVE.value
            ):
                continue
            result[test_case.id] = _LedgerCase(
                test_case=test_case,
                version=version,
                steps=tuple(steps[version.id]),
            )
        return result

    def _parent_candidates(
        self,
        work_items: tuple[WorkItem, ...],
        slack_item: SlackWorkItem,
    ) -> tuple[ParentWorkItemCandidate, ...]:
        linked = set(
            self.session.scalars(
                select(GenerationApply.parent_work_item_id)
                .join(GenerationJob, GenerationJob.id == GenerationApply.generation_job_id)
                .where(GenerationJob.slack_work_item_id == slack_item.id)
            )
        )
        identity = _slack_identity(slack_item)
        return tuple(
            ParentWorkItemCandidate(
                work_item_id=_identifier("work_item", row.id),
                work_item_key=row.work_item_key,
                title=row.summary,
                category_id=_identifier("folder", row.category_id),
                slack_source_identity=identity if row.id in linked else None,
            )
            for row in work_items
        )

    def _validate_placement(
        self,
        command: GenerationApplyCommand,
        *,
        plan: GenerationPlacementPlan,
        generation: ValidatedGeneration,
        categories: tuple[Folder, ...],
        work_items: tuple[WorkItem, ...],
        ledger_cases: dict[uuid.UUID, _LedgerCase],
        planning_context: GenerationPlanningContext,
    ) -> None:
        if len(command.cases) != len(generation.cases):
            raise GenerationPlacementConflict(command.job_id, "CASE_COUNT_MISMATCH")
        expected_indexes = tuple(range(len(generation.cases)))
        if tuple(row.generated_case_index for row in command.cases) != expected_indexes:
            raise GenerationPlacementConflict(command.job_id, "CASE_ORDER_MISMATCH")
        for requested, planned in zip(command.cases, plan.cases, strict=True):
            if requested.exact_content_key != planned.exact_content_key:
                raise GenerationPlacementConflict(
                    command.job_id,
                    "EXACT_CONTENT_KEY_MISMATCH",
                    generated_case_index=requested.generated_case_index,
                )

        category_by_id = {row.id: row for row in categories}
        category = category_by_id.get(command.category.category_id)
        if category is None:
            raise ResourceNotFoundError("FOLDER", command.category.category_id)
        category_decision = plan.placement.category
        if (
            category.version != command.category.expected_resource_version
            or category_decision.category_id != _identifier("folder", category.id)
            or category_decision.method.value != command.category.method
            or not _same_score(category_decision.score, command.category.score)
        ):
            raise GenerationPlacementConflict(
                command.job_id,
                "CATEGORY_STALE",
                resource_type="CATEGORY",
                resource_id=category.id,
            )

        work_item_by_id = {row.id: row for row in work_items}
        planned_parent = plan.placement.parent
        if isinstance(command.parent, ReuseParentApplyPlacement):
            parent = work_item_by_id.get(command.parent.work_item_id)
            if parent is None:
                raise ResourceNotFoundError("WORK_ITEM", command.parent.work_item_id)
            if not isinstance(planned_parent, ReuseParentDecision):
                raise GenerationPlacementConflict(command.job_id, "PARENT_STALE")
            if (
                parent.version != command.parent.expected_resource_version
                or parent.category_id != command.parent.expected_category_id
                or planned_parent.work_item_id != _identifier("work_item", parent.id)
                or planned_parent.category_id != _identifier("folder", parent.category_id)
                or planned_parent.method.value != command.parent.method
                or not _same_score(planned_parent.score, command.parent.score)
            ):
                raise GenerationPlacementConflict(
                    command.job_id,
                    "PARENT_STALE",
                    resource_type="WORK_ITEM",
                    resource_id=parent.id,
                )
        else:
            if not isinstance(planned_parent, CreateNewParentDecision):
                raise GenerationPlacementConflict(command.job_id, "PARENT_CONFLICT")
            if (
                command.parent.expected_work_item_key != planning_context.parent_source_key
                or command.parent.expected_summary != planning_context.parent_title
                or command.parent.category_id != command.category.category_id
                or planned_parent.category_id != _identifier("folder", command.parent.category_id)
                or planned_parent.method is not MappingMethod.CREATE_NEW_PARENT
            ):
                raise GenerationPlacementConflict(command.job_id, "PARENT_STALE")

        for requested, planned in zip(command.cases, plan.cases, strict=True):
            if not isinstance(requested, ReuseCaseApplyPlacement):
                continue
            current = ledger_cases.get(requested.test_case_id)
            if current is None:
                raise ResourceNotFoundError("TEST_CASE", requested.test_case_id)
            case = current.test_case
            version = current.version
            if (
                case.version != requested.expected_resource_version
                or case.current_version_id != requested.expected_current_version_id
                or version.version_number != requested.expected_current_version
            ):
                raise GenerationPlacementConflict(
                    command.job_id,
                    "REUSE_CANDIDATE_STALE",
                    generated_case_index=requested.generated_case_index,
                    resource_type="TEST_CASE",
                    resource_id=case.id,
                )
            reasons = {
                candidate.case_id: candidate.reason.value for candidate in planned.reuse_candidates
            }
            encoded_id = _identifier("case", case.id)
            exact_reason = reasons.get(encoded_id)
            eligible = exact_reason == requested.match_reason
            if not eligible:
                raise GenerationPlacementConflict(
                    command.job_id,
                    "REUSE_CANDIDATE_NOT_ELIGIBLE",
                    generated_case_index=requested.generated_case_index,
                    resource_type="TEST_CASE",
                    resource_id=case.id,
                )

    def _materialize(
        self,
        command: GenerationApplyCommand,
        *,
        job: GenerationJob,
        entries: tuple[StoredGenerationContext, ...],
        generation: ValidatedGeneration,
        ledger_cases: dict[uuid.UUID, _LedgerCase],
        placement: dict[str, object],
        placement_digest: str,
        request_digest: str,
        result_digest: str,
        audit: AuditContext,
    ) -> GenerationApply:
        stage: ApplyWriteStage = "CREATE_PARENT"
        case_repository = CaseRepository(self.session, workspace_id=self.workspace_id)
        try:
            self._inject(stage, None)
            if isinstance(command.parent, CreateParentApplyPlacement):
                parent = case_repository.create_work_item(
                    work_item_key=command.parent.expected_work_item_key,
                    category_id=command.parent.category_id,
                    summary=command.parent.expected_summary,
                    audit=audit,
                )
                parent_response: dict[str, object] = {
                    "outcome": "CREATED",
                    "ledgerMutation": "CREATED_WORK_ITEM",
                    "workItemId": _identifier("work_item", parent.id),
                    "key": parent.work_item_key,
                    "categoryId": _identifier("folder", parent.category_id),
                    "resourceVersion": parent.version,
                }
            else:
                parent = case_repository.get_work_item(command.parent.work_item_id, for_update=True)
                parent_response = {
                    "outcome": "REUSED",
                    "ledgerMutation": "NONE",
                    "workItemId": _identifier("work_item", parent.id),
                    "key": parent.work_item_key,
                    "categoryId": _identifier("folder", parent.category_id),
                    "resourceVersion": parent.version,
                }

            case_responses: list[dict[str, object]] = []
            outcome_values: list[tuple[uuid.UUID, uuid.UUID, str, bool, str | None]] = []
            for requested, generated in zip(command.cases, generation.cases, strict=True):
                index = requested.generated_case_index
                if isinstance(requested, ReuseCaseApplyPlacement):
                    existing = ledger_cases[requested.test_case_id]
                    response = _reused_case_response(requested, existing)
                    case_responses.append(response)
                    outcome_values.append(
                        (
                            existing.test_case.id,
                            existing.version.id,
                            "REUSED",
                            True,
                            requested.match_reason,
                        )
                    )
                    continue

                stage = "CREATE_CASE"
                self._inject(stage, index)
                stage = "CREATE_VERSION"
                self._inject(stage, index)
                source = _source_provenance(
                    job,
                    generated,
                    entries=entries,
                    result_digest=result_digest,
                )
                aggregate = case_repository.create_case(
                    work_item_id=parent.id,
                    content=TestCaseContent(
                        summary=generated.summary,
                        precondition=_aggregate_precondition(generated),
                        steps=tuple(
                            TestStepInput(
                                action=step.action,
                                precondition=step.precondition,
                            )
                            for step in generated.steps
                        ),
                        expected_result_mode="CASE",  # type: ignore[arg-type]
                        expected_result=generated.case_expected_result,
                        status=TestCaseStatus.ACTIVE,
                        priority=TestCasePriority(generated.priority.value),
                        tag_names=generated.labels,
                        source_type=TestCaseSourceType.SLACK_AI,
                        source_metadata=source,
                        generation_reason=generated.generation_reason,
                    ),
                    change_reason="Applied from trusted generation result.",
                    audit=audit,
                )
                stage = "CREATE_STEPS"
                self._inject(stage, index)
                response = _created_case_response(
                    requested,
                    generated,
                    aggregate.test_case,
                    aggregate.current_version.id,
                    aggregate.current_version.created_at,
                    source,
                )
                case_responses.append(response)
                outcome_values.append(
                    (
                        aggregate.test_case.id,
                        aggregate.current_version.id,
                        "CREATED",
                        False,
                        None,
                    )
                )

            apply_id = uuid.uuid4()
            audit_id = uuid.uuid4()
            applied_at = datetime.now(UTC)
            created_count = sum(value[2] == "CREATED" for value in outcome_values)
            receipt = {
                "id": _identifier("generation_apply", apply_id),
                "generationJobId": _identifier("generation_job", job.id),
                "jobStatus": "SUCCEEDED",
                "contextDigest": job.context_digest,
                "resultDigest": result_digest,
                "placementDigest": placement_digest,
                "transactionStatus": "COMMITTED",
                "parent": parent_response,
                "cases": case_responses,
                "counts": {
                    "generated": len(case_responses),
                    "created": created_count,
                    "reused": len(case_responses) - created_count,
                },
                "auditId": _identifier("audit", audit_id),
                "appliedAt": applied_at.isoformat(),
            }
            apply_row = GenerationApply(
                id=apply_id,
                generation_job_id=job.id,
                parent_work_item_id=parent.id,
                idempotency_key=command.idempotency_key,
                request_digest=request_digest,
                context_digest=job.context_digest,
                result_digest=result_digest,
                placement_digest=placement_digest,
                audit_id=audit_id,
                receipt=receipt,
                applied_at=applied_at,
            )
            self.session.add(apply_row)
            stage = "RECORD_OUTCOME"
            self._inject(stage, None)
            outcome_rows = [
                GenerationApplyOutcome(
                    id=uuid.uuid4(),
                    generation_apply_id=apply_id,
                    generated_case_index=requested.generated_case_index,
                    exact_content_key=requested.exact_content_key,
                    outcome=outcome,
                    test_case_id=test_case_id,
                    test_case_version_id=version_id,
                    match_reason=match_reason,
                    preserved=preserved,
                    snapshot=response,
                    created_at=applied_at,
                )
                for requested, response, (
                    test_case_id,
                    version_id,
                    outcome,
                    preserved,
                    match_reason,
                ) in zip(command.cases, case_responses, outcome_values, strict=True)
            ]
            self.session.add_all(outcome_rows)
            # The CREATED outcome is the authority boundary. Persist it first,
            # then initialize management through the repository's transaction-local
            # capability. REUSED rows never enter this path.
            self.session.flush()
            for outcome_row in outcome_rows:
                if outcome_row.outcome == "CREATED":
                    case_repository.initialize_generation_management(
                        test_case_id=outcome_row.test_case_id,
                        created_outcome_id=outcome_row.id,
                    )
            stage = "AUDIT"
            self._inject(stage, None)
            self.session.add(
                AuditLog(
                    id=audit_id,
                    workspace_id=self.workspace_id,
                    entity_type="GENERATION_APPLY",
                    entity_id=apply_id,
                    action="APPLY_GENERATION_JOB",
                    before=None,
                    after={
                        "generationJobId": receipt["generationJobId"],
                        "contextDigest": job.context_digest,
                        "resultDigest": result_digest,
                        "placementDigest": placement_digest,
                        "semanticDuplicateSeal": placement["semanticDuplicateSeal"],
                        "counts": receipt["counts"],
                        "transactionStatus": "COMMITTED",
                    },
                    source_type=audit.source_type.value,
                    actor_label=audit.actor_label,
                    external_actor_id=audit.external_actor_id,
                    reason=audit.reason,
                    trace_id=audit.trace_id or f"trace_{uuid.uuid4().hex}",
                    idempotency_key=command.idempotency_key,
                    occurred_at=applied_at,
                )
            )
            stage = "OUTBOX"
            self._inject(stage, None)
            self.session.add(
                OutboxEvent(
                    id=uuid.uuid4(),
                    workspace_id=self.workspace_id,
                    aggregate_type="GENERATION_APPLY",
                    aggregate_id=apply_id,
                    event_type="GENERATION_APPLY_COMMITTED",
                    aggregate_version=1,
                    payload={
                        "generationApplyId": receipt["id"],
                        "generationJobId": receipt["generationJobId"],
                        "parentWorkItemId": parent_response["workItemId"],
                        "counts": receipt["counts"],
                    },
                    status=OutboxStatus.READY.value,
                    attempts=0,
                    occurred_at=applied_at,
                )
            )
            # Surface every deferred INSERT owned by apply while the named
            # write stage is still available for the stable rollback error.
            self.session.flush()
            if job.generation_trigger == "AUTOMATIC":
                stage = "NOTIFICATION"
                from tc_backend.generation.slack_notifications import (
                    GenerationSlackNotificationService,
                )

                GenerationSlackNotificationService(
                    self.session,
                    workspace_id=self.workspace_id,
                    public_web_base_url=self.public_web_base_url,
                ).capture_applied_success(apply_row.id)
                self.session.flush()
                self._inject(stage, None)
            return apply_row
        except GenerationApplyTransactionFailed:
            raise
        except Exception as error:
            raise GenerationApplyTransactionFailed(command.job_id, stage) from error

    def _inject(self, stage: ApplyWriteStage, index: int | None) -> None:
        if self._fault is not None:
            self._fault(stage, index)


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonicalize(value).encode("utf-8")).hexdigest()


def _canonicalize(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON accepts only finite numbers")
        if value == 0:
            return "0"
        if value.is_integer() and abs(value) < 1e21:
            return str(int(value))
        rendered = json.dumps(value, allow_nan=False, separators=(",", ":"))
        if "e" in rendered:
            mantissa, exponent = rendered.split("e")
            sign = "+" if not exponent.startswith("-") else "-"
            digits = exponent.lstrip("+-0") or "0"
            return f"{mantissa}e{sign}{digits}"
        return rendered
    if isinstance(value, list | tuple):
        return "[" + ",".join(_canonicalize(item) for item in value) + "]"
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        return (
            "{"
            + ",".join(f"{_canonicalize(key)}:{_canonicalize(value[key])}" for key in sorted(value))
            + "}"
        )
    raise TypeError(f"value is not JSON-canonicalizable: {type(value).__name__}")


def _normalized_placement(command: GenerationApplyCommand) -> dict[str, object]:
    category: dict[str, object] = {
        "categoryId": _identifier("folder", command.category.category_id),
        "expectedResourceVersion": command.category.expected_resource_version,
        "method": command.category.method,
        "score": command.category.score,
    }
    if isinstance(command.parent, ReuseParentApplyPlacement):
        parent: dict[str, object] = {
            "decision": command.parent.decision,
            "workItemId": _identifier("work_item", command.parent.work_item_id),
            "expectedResourceVersion": command.parent.expected_resource_version,
            "expectedCategoryId": _identifier("folder", command.parent.expected_category_id),
            "method": command.parent.method,
            "score": command.parent.score,
        }
    else:
        parent = {
            "decision": command.parent.decision,
            "expectedWorkItemKey": command.parent.expected_work_item_key,
            "expectedSummary": command.parent.expected_summary,
            "categoryId": _identifier("folder", command.parent.category_id),
            "method": command.parent.method,
        }
    cases: list[dict[str, object]] = []
    for row in command.cases:
        value: dict[str, object] = {
            "generatedCaseIndex": row.generated_case_index,
            "exactContentKey": row.exact_content_key,
            "decision": row.decision,
        }
        if isinstance(row, ReuseCaseApplyPlacement):
            value.update(
                {
                    "testCaseId": _identifier("case", row.test_case_id),
                    "expectedResourceVersion": row.expected_resource_version,
                    "expectedCurrentVersionId": _identifier(
                        "case_version", row.expected_current_version_id
                    ),
                    "expectedCurrentVersion": row.expected_current_version,
                    "matchReason": row.match_reason,
                }
            )
        cases.append(value)
    return {
        "placementSchemaVersion": command.placement_schema_version,
        "category": category,
        "parent": parent,
        "cases": cases,
    }


def _validate_stored_shape(job_id: uuid.UUID, payload: Mapping[str, object]) -> None:
    try:
        raw_cases = payload.get("cases")
        raw_category = payload.get("categoryCandidate")
        case_values = raw_cases if isinstance(raw_cases, list) else []
        evidence_ids = {
            value
            for case in case_values
            if isinstance(case, dict)
            for value in case.get("evidenceNodeIds", [])
            if isinstance(value, str)
        }
        category_ids: set[str] = set()
        if isinstance(raw_category, dict):
            selected = raw_category.get("categoryId")
            if isinstance(selected, str):
                category_ids.add(selected)
            alternatives = raw_category.get("alternatives")
            if isinstance(alternatives, list):
                category_ids.update(
                    value["categoryId"]
                    for value in alternatives
                    if isinstance(value, dict) and isinstance(value.get("categoryId"), str)
                )
        synthetic = GenerationContext(
            evidence=tuple(
                ContextEvidence(value, "ACTIVE", json.dumps(payload, ensure_ascii=False), True)
                for value in sorted(evidence_ids)
            ),
            category_ids=frozenset(category_ids),
        )
        GenerationResponseValidator().validate(payload, synthetic)
    except (GenerationValidationError, TypeError, ValueError) as error:
        message = str(error)
        if "outputSchemaVersion" in message:
            reason = "OUTPUT_VERSION_MISMATCH"
        elif "contiguous" in message:
            reason = "NON_CONTIGUOUS_STEPS"
        elif "unique after normalization" in message or "summaries" in message:
            reason = "DUPLICATE_NORMALIZED_CONTENT"
        else:
            reason = "SCHEMA_INVALID"
        raise GenerationStoredResultInvalid(job_id, "SCHEMA", reason) from error


def _grounding_reason(message: str) -> str:
    if "outputSchemaVersion" in message:
        return "OUTPUT_VERSION_MISMATCH"
    if "missing context evidence" in message:
        return "EVIDENCE_NOT_IN_SEALED_CONTEXT"
    if "inactive context evidence" in message:
        return "EVIDENCE_NOT_ACTIVE"
    if "categoryCandidate" in message:
        return "CATEGORY_NOT_IN_SEALED_CONTEXT"
    if "contiguous" in message:
        return "NON_CONTIGUOUS_STEPS"
    if "unique after normalization" in message or "summaries" in message:
        return "DUPLICATE_NORMALIZED_CONTENT"
    if "unsupported" in message:
        return "UNSUPPORTED_CLAIM_REJECTED"
    return "NOT_MATERIALIZABLE"


def _validate_materializable(job_id: uuid.UUID, generation: ValidatedGeneration) -> None:
    for generated in generation.cases:
        values = [
            generated.case_expected_result,
            *generated.open_questions,
            *(step.action for step in generated.steps),
            *(step.precondition for step in generated.steps if step.precondition is not None),
        ]
        if any(len(value) > 20_000 for value in values):
            raise GenerationStoredResultInvalid(job_id, "MATERIALIZATION", "NOT_MATERIALIZABLE")
        if len(_aggregate_precondition(generated)) > 20_000:
            raise GenerationStoredResultInvalid(job_id, "MATERIALIZATION", "NOT_MATERIALIZABLE")


def _planning_context(
    slack_item: SlackWorkItem,
    entries: tuple[StoredGenerationContext, ...],
) -> GenerationPlanningContext:
    root = next(
        (
            row.node_snapshot
            for row in entries
            if row.node_snapshot is not None
            and row.node_snapshot.get("nodeType") == "WORK_ITEM"
            and row.node_snapshot.get("entityType") == "SLACK_WORK_ITEM"
        ),
        None,
    )
    root = root or {}
    properties = root.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    title_value = properties.get("developmentItem") or root.get("label")
    title = str(title_value).strip() if title_value is not None else ""
    if not title:
        title = f"Slack work item {slack_item.id.hex[:12]}"
    requested = properties.get("requestedMenu") or properties.get("categoryName")
    requested_category = str(requested).strip() if requested is not None else None
    return GenerationPlanningContext(
        source_identity=_slack_identity(slack_item),
        parent_source_key=f"SLACK-{slack_item.id.hex}",
        parent_title=title[:200],
        requested_category_name=requested_category or None,
    )


def _slack_identity(slack_item: SlackWorkItem) -> SlackSourceIdentity:
    return SlackSourceIdentity(
        workspace_id=slack_item.slack_workspace_id,
        channel_id=slack_item.channel_id,
        message_ts=slack_item.message_ts,
    )


def _existing_case(value: _LedgerCase) -> ExistingCaseInput:
    return ExistingCaseInput(
        case_id=_identifier("case", value.test_case.id),
        case_key=value.test_case.case_key,
        summary=value.version.summary,
        steps=tuple(
            DeduplicationStep(action=row.action, precondition=row.precondition)
            for row in value.steps
        ),
        case_expected_result=value.version.expected_result or "",
        status=value.test_case.status,
        deleted=False,
        deleted_at=value.test_case.deleted_at,
    )


def _generated_case_input(value: GeneratedTestCase) -> GeneratedCaseInput:
    return GeneratedCaseInput(
        summary=value.summary,
        steps=tuple(
            DeduplicationStep(action=row.action, precondition=row.precondition)
            for row in value.steps
        ),
        case_expected_result=value.case_expected_result,
    )


def _aggregate_precondition(generated: GeneratedTestCase) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for step in generated.steps:
        if step.precondition is None:
            continue
        value = step.precondition.strip()
        normalized = unicodedata.normalize("NFKC", value).casefold()
        if normalized not in seen:
            seen.add(normalized)
            values.append(value)
    return "\n".join(values) if values else "[생성 결과에 명시된 공통 사전조건 없음]"


def _source_provenance(
    job: GenerationJob,
    generated: GeneratedTestCase,
    *,
    entries: tuple[StoredGenerationContext, ...],
    result_digest: str,
) -> dict[str, object]:
    evidence = list(generated.evidence_node_ids)
    source_revisions: list[str] = []
    seen: set[str] = set()
    for evidence_id in evidence:
        for row in entries:
            if row.node_id is None or _identifier("knowledge_node", row.node_id) != evidence_id:
                continue
            revision_id = _identifier(row.source_entity_type.lower(), row.source_entity_id)
            if revision_id not in seen:
                seen.add(revision_id)
                source_revisions.append(revision_id)
    if not source_revisions:
        raise GenerationStoredResultInvalid(job.id, "MATERIALIZATION", "NOT_MATERIALIZABLE")
    return {
        "sourceType": "SLACK_AI",
        "slackWorkItemId": _identifier("slack_work_item", job.slack_work_item_id),
        "generationJobId": _identifier("generation_job", job.id),
        "contextDigest": job.context_digest,
        "resultDigest": result_digest,
        "outputSchemaVersion": job.schema_version,
        "generationVersion": job.generation_version,
        "evidenceNodeIds": evidence,
        "sourceRevisionIds": source_revisions,
        "inferenceOnly": generated.inference_only,
        "openQuestions": list(generated.open_questions),
    }


def _created_case_response(
    requested: CreateCaseApplyPlacement,
    generated: GeneratedTestCase,
    test_case: TestCase,
    version_id: uuid.UUID,
    created_at: datetime,
    source: dict[str, object],
) -> dict[str, object]:
    return {
        "generatedCaseIndex": requested.generated_case_index,
        "exactContentKey": requested.exact_content_key,
        "outcome": "CREATED",
        "ledgerMutation": "CREATED_CASE_AND_VERSION",
        "case": {
            "id": _identifier("case", test_case.id),
            "key": test_case.case_key,
            "workItemId": _identifier("work_item", test_case.work_item_id),
            "categoryId": _identifier("folder", test_case.category_id),
            "status": "ACTIVE",
            "resourceVersion": 1,
            "currentVersion": {
                "id": _identifier("case_version", version_id),
                "version": 1,
                "summary": generated.summary,
                "precondition": _aggregate_precondition(generated),
                "expectedResultMode": "CASE",
                "caseExpectedResult": generated.case_expected_result,
                "priority": generated.priority.value,
                "labels": list(generated.labels),
                "steps": [
                    {
                        "sequence": row.sequence,
                        "precondition": row.precondition,
                        "action": row.action,
                    }
                    for row in generated.steps
                ],
                "generationReason": generated.generation_reason,
                "source": source,
                "createdAt": created_at.isoformat(),
            },
        },
    }


def _reused_case_response(
    requested: ReuseCaseApplyPlacement,
    existing: _LedgerCase,
) -> dict[str, object]:
    case = existing.test_case
    version = existing.version
    return {
        "generatedCaseIndex": requested.generated_case_index,
        "exactContentKey": requested.exact_content_key,
        "outcome": "REUSED",
        "ledgerMutation": "NONE",
        "preserved": True,
        "matchReason": requested.match_reason,
        "case": {
            "id": _identifier("case", case.id),
            "key": case.case_key,
            "workItemId": _identifier("work_item", case.work_item_id),
            "categoryId": _identifier("folder", case.category_id),
            "status": case.status,
            "resourceVersion": case.version,
            "currentVersionId": _identifier("case_version", version.id),
            "currentVersion": version.version_number,
        },
    }


def _same_score(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-12)


def _identifier(prefix: str, value: uuid.UUID) -> str:
    return f"{prefix}_{value.hex}"


def _decode_identifier(value: str, prefix: str) -> uuid.UUID:
    marker = f"{prefix}_"
    if not value.startswith(marker):
        raise ValueError(f"invalid {prefix} identifier")
    try:
        return uuid.UUID(value[len(marker) :])
    except ValueError as error:
        raise ValueError(f"invalid {prefix} identifier") from error
