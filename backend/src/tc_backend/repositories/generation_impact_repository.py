"""Durable automatic-generation impact classification and assessment ledger."""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased

from tc_backend.db.models import (
    GenerationApply,
    GenerationChangeAssessment,
    GenerationChangeOutcome,
    GenerationJob,
    GenerationSuggestionMaterialization,
    GenerationTrigger,
    KnowledgeNode,
    OntologySchema,
)
from tc_backend.generation.impact import (
    CONFIG_SCHEMA_VERSION,
    IMPACT_SCHEMA_VERSION,
    canonical_contract_config,
    canonical_generation_impact,
    changed_fields,
)
from tc_backend.repositories.generation_repository import (
    AutomaticGenerationRootConflict,
    GenerationRepository,
    PreparedAutomaticGeneration,
    UngroundedGenerationContext,
)


@dataclass(frozen=True, slots=True)
class GenerationImpactResolution:
    assessment: GenerationChangeAssessment
    selected_job: GenerationJob | None
    created_job: bool


class GenerationImpactRepository:
    """Classify one locked current source and append exactly one assessment."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_assessment(
        self, slack_work_item_id: uuid.UUID, source_version: int
    ) -> GenerationChangeAssessment | None:
        return self.session.scalar(
            select(GenerationChangeAssessment).where(
                GenerationChangeAssessment.slack_work_item_id == slack_work_item_id,
                GenerationChangeAssessment.current_source_version == source_version,
            )
        )

    def assess_current(
        self,
        *,
        prepared: PreparedAutomaticGeneration,
        extraction: dict[str, Any],
    ) -> GenerationImpactResolution:
        work_item_id = prepared.context.slack_work_item_id
        source_version = prepared.context.source_version
        existing = self.get_assessment(work_item_id, source_version)
        if existing is not None:
            return GenerationImpactResolution(
                assessment=existing,
                selected_job=self._receipt_job(existing),
                created_job=False,
            )

        predecessor = self.session.scalar(
            select(GenerationChangeAssessment)
            .where(
                GenerationChangeAssessment.slack_work_item_id == work_item_id,
                GenerationChangeAssessment.current_source_version < source_version,
            )
            .order_by(GenerationChangeAssessment.current_source_version.desc())
            .limit(1)
        )
        materialized_attempt = aliased(GenerationJob)
        materialization = self.session.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.slack_work_item_id == work_item_id,
                GenerationJob.source_version < source_version,
                GenerationJob.generation_trigger == GenerationTrigger.AUTOMATIC.value,
                GenerationJob.retry_of_job_id.is_(None),
                (
                    exists(
                        select(GenerationApply.id)
                        .join(
                            materialized_attempt,
                            materialized_attempt.id == GenerationApply.generation_job_id,
                        )
                        .where(materialized_attempt.root_job_id == GenerationJob.id)
                        .correlate(GenerationJob)
                    )
                    | exists(
                        select(GenerationSuggestionMaterialization.id)
                        .join(
                            materialized_attempt,
                            materialized_attempt.id
                            == GenerationSuggestionMaterialization.generation_job_id,
                        )
                        .where(materialized_attempt.root_job_id == GenerationJob.id)
                        .correlate(GenerationJob)
                    )
                ),
            )
            .order_by(
                GenerationJob.source_version.desc(),
                GenerationJob.generation_version.desc(),
            )
            .limit(1)
        )
        ontology = self.session.get(OntologySchema, prepared.context.ontology_schema_id)
        if ontology is None:
            raise UngroundedGenerationContext("impact projection has no ontology schema")
        current_impact = canonical_generation_impact(
            prepared.generation_input, prepared.context.entries
        )
        current_config = canonical_contract_config(
            generation_input=prepared.generation_input,
            ontology_version=ontology.version,
            prompt_version=prepared.prompt_version,
            extraction=extraction,
        )

        outcome: str
        reason: str
        fields: list[str]
        selected: GenerationJob | None = None
        reused: GenerationJob | None = None
        created_job = False

        unsafe_reason = self._unsafe_current_knowledge_reason(
            work_item_id, prepared.context.ontology_schema_id
        )
        if unsafe_reason is not None:
            outcome = GenerationChangeOutcome.REVIEW_REQUIRED.value
            reason = unsafe_reason
            fields = ["$REVIEW_REQUIRED"]
        elif predecessor is None and materialization is not None:
            outcome = GenerationChangeOutcome.REVIEW_REQUIRED.value
            reason = "LEGACY_INPUT_INCOMPATIBLE"
            fields = ["$REVIEW_REQUIRED"]
        elif predecessor is None:
            outcome = GenerationChangeOutcome.INITIAL_GENERATE.value
            reason = "FIRST_AUTOMATIC_SOURCE"
            fields = ["$INITIAL_SOURCE"]
            selected, created_job = self._create_job(prepared)
        elif not self._supported_predecessor(predecessor):
            outcome = GenerationChangeOutcome.REVIEW_REQUIRED.value
            reason = "UNSUPPORTED_COMPARISON_SCHEMA"
            fields = ["$REVIEW_REQUIRED"]
        elif (
            predecessor.current_fingerprint == current_impact.fingerprint
            and predecessor.current_contract_config_digest == current_config.digest
        ):
            if materialization is None:
                outcome = GenerationChangeOutcome.REVIEW_REQUIRED.value
                reason = "IMPACT_COMPARISON_UNSAFE"
                fields = ["$REVIEW_REQUIRED"]
            else:
                outcome = GenerationChangeOutcome.SKIP.value
                reason = "NO_GENERATION_RELEVANT_CHANGE"
                fields = []
                reused = materialization
        elif materialization is None:
            outcome = GenerationChangeOutcome.REVIEW_REQUIRED.value
            reason = "IMPACT_COMPARISON_UNSAFE"
            fields = ["$REVIEW_REQUIRED"]
        else:
            baseline_projection = self._baseline_projection(materialization)
            if baseline_projection is None:
                outcome = GenerationChangeOutcome.REVIEW_REQUIRED.value
                reason = "CORRUPT_SEAL"
                fields = ["$REVIEW_REQUIRED"]
            elif baseline_projection.fingerprint != predecessor.current_fingerprint:
                outcome = GenerationChangeOutcome.REVIEW_REQUIRED.value
                reason = "IMPACT_COMPARISON_UNSAFE"
                fields = ["$REVIEW_REQUIRED"]
            else:
                fields = changed_fields(baseline_projection.value, current_impact.value)
                config_changed = predecessor.current_contract_config_digest != current_config.digest
                if config_changed:
                    fields.append("contractConfig")
                if not fields:
                    outcome = GenerationChangeOutcome.REVIEW_REQUIRED.value
                    reason = "IMPACT_COMPARISON_UNSAFE"
                    fields = ["$REVIEW_REQUIRED"]
                else:
                    outcome = GenerationChangeOutcome.REGENERATE.value
                    reason = (
                        "GENERATION_CONTRACT_CHANGED"
                        if fields == ["contractConfig"]
                        else "GENERATION_RELEVANT_FIELDS_CHANGED"
                    )
                    selected, created_job = self._create_job(prepared)

        assessment = GenerationChangeAssessment(
            slack_work_item_id=work_item_id,
            predecessor_assessment_id=(predecessor.id if predecessor is not None else None),
            baseline_source_version=(
                predecessor.current_source_version
                if predecessor is not None
                else materialization.source_version
                if materialization is not None
                else None
            ),
            current_source_version=source_version,
            materialization_baseline_generation_job_id=(
                materialization.id if materialization is not None else None
            ),
            selected_generation_job_id=selected.id if selected is not None else None,
            reused_generation_job_id=reused.id if reused is not None else None,
            outcome=outcome,
            reason_code=reason,
            changed_fields=fields,
            fingerprint_schema_version=IMPACT_SCHEMA_VERSION,
            baseline_fingerprint=(
                predecessor.current_fingerprint if predecessor is not None else None
            ),
            current_fingerprint=current_impact.fingerprint,
            contract_config_schema_version=CONFIG_SCHEMA_VERSION,
            baseline_contract_config_digest=(
                predecessor.current_contract_config_digest if predecessor is not None else None
            ),
            current_contract_config_digest=current_config.digest,
        )
        self.session.add(assessment)
        self.session.flush()
        if outcome == GenerationChangeOutcome.REGENERATE.value:
            # T05 wraps only the selected REGENERATE root after its assessment exists.
            from tc_backend.generation.suggestions import GenerationSuggestionService

            GenerationSuggestionService(self.session).seal_targets_for_assessment(assessment)
        if created_job and selected is not None:
            from tc_backend.generation.retry_history import RetryHistoryService

            RetryHistoryService(self.session).schedule_root(selected.id)
        return GenerationImpactResolution(
            assessment=assessment,
            selected_job=selected or reused,
            created_job=created_job,
        )

    def _create_job(self, prepared: PreparedAutomaticGeneration) -> tuple[GenerationJob, bool]:
        try:
            resolution = GenerationRepository(
                self.session
            ).get_or_create_automatic_job_with_context(
                slack_work_item_id=prepared.context.slack_work_item_id,
                source_version=prepared.context.source_version,
                prompt_version=prepared.prompt_version,
                schema_version=prepared.schema_version,
                prepared=prepared,
            )
        except AutomaticGenerationRootConflict as error:
            raise UngroundedGenerationContext(
                "automatic generation root conflicts with impact assessment"
            ) from error
        return resolution.persisted.job, resolution.created

    def _baseline_projection(self, job: GenerationJob):  # type: ignore[no-untyped-def]
        try:
            persisted = GenerationRepository(self.session).load_context(job.id)
            provider_input = persisted.provider_input
            if provider_input.get("inputSchemaVersion") == "generation-suggestion/1.0.0":
                base = provider_input.get("baseGenerationInput")
                if not isinstance(base, dict):
                    return None
                base_input = copy.deepcopy(base)
                existing = base_input.get("existingCases")
                if isinstance(existing, list):
                    base_input["existingCases"] = [
                        row
                        for row in existing
                        if not isinstance(row, dict)
                        or row.get("selectionReason") != "RELEASED_GENERATED_SUGGESTION_TARGET"
                    ]
                provider_input = base_input
            return canonical_generation_impact(provider_input, persisted.entries)
        except (LookupError, TypeError, ValueError, UngroundedGenerationContext):
            return None

    def _unsafe_current_knowledge_reason(
        self, work_item_id: uuid.UUID, ontology_schema_id: uuid.UUID
    ) -> str | None:
        statuses = set(
            self.session.scalars(
                select(KnowledgeNode.status).where(
                    KnowledgeNode.ontology_schema_id == ontology_schema_id,
                    KnowledgeNode.entity_type == "SLACK_KNOWLEDGE_CANDIDATE",
                    KnowledgeNode.properties["slackWorkItemId"].as_string() == str(work_item_id),
                    KnowledgeNode.status.in_(("AMBIGUOUS", "RETRACTED")),
                )
            )
        )
        if "RETRACTED" in statuses:
            return "DELETED_OR_RETRACTED_EVIDENCE"
        if "AMBIGUOUS" in statuses:
            return "AMBIGUOUS_KNOWLEDGE"
        return None

    @staticmethod
    def _supported_predecessor(predecessor: GenerationChangeAssessment) -> bool:
        return (
            predecessor.fingerprint_schema_version == IMPACT_SCHEMA_VERSION
            and predecessor.contract_config_schema_version == CONFIG_SCHEMA_VERSION
            and predecessor.current_fingerprint is not None
            and predecessor.current_contract_config_digest is not None
        )

    def _receipt_job(self, assessment: GenerationChangeAssessment) -> GenerationJob | None:
        job_id = assessment.selected_generation_job_id or assessment.reused_generation_job_id
        return self.session.get(GenerationJob, job_id) if job_id is not None else None
