"""Deterministic grounded context construction and immutable snapshot persistence.

The controlled PostgreSQL ontology is authoritative.  A generation context is
built from exactly one current Slack work item, traverses only current ACTIVE
nodes and edges in the active ontology schema, and admits ledger resources only
when their current state is eligible for generation.  Every selected resource
must have a matching immutable source record; any missing or inconsistent
grounding fails the entire build instead of silently weakening the context.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from tc_backend.db.constants import AI_INBOX_SYSTEM_KIND
from tc_backend.db.models import (
    CycleItem,
    ExecutionAttempt,
    Folder,
    GenerationApply,
    GenerationApplyOutcome,
    GenerationContext,
    GenerationJob,
    GenerationJobStatus,
    GenerationTrigger,
    Issue,
    IssueLink,
    KnowledgeEdge,
    KnowledgeEvidence,
    KnowledgeNode,
    KnowledgeSourceRecord,
    OntologySchema,
    SlackWorkItem,
    Tag,
    TestCase,
    TestCaseVersion,
    TestCaseVersionTag,
    TestStep,
)
from tc_backend.generation.models import GENERATION_INPUT_SCHEMA_VERSION
from tc_backend.generation.reconstruction import (
    GenerationInputCaptureError,
    GenerationInputCaptureState,
    GenerationReconstruction,
    canonical_input_digest,
    reconstruct_generation_input,
    seal_generation_input,
)

_OPEN_ISSUE_STATUSES = frozenset({"OPEN", "IN_PROGRESS", "REOPENED"})
_RISK_EXECUTION_STATUSES = frozenset({"FAIL", "BLOCKED"})
_GENERIC_KNOWLEDGE_TYPES = frozenset(
    {
        "MENU",
        "FEATURE",
        "REQUIREMENT",
        "DECISION",
        "CONSTRAINT",
        "OPEN_QUESTION",
        "RISK",
        "RELEASE",
    }
)
_NODE_PRIORITY: dict[str, tuple[int, str]] = {
    "DECISION": (10, "ACTIVE_DECISION"),
    "REQUIREMENT": (20, "ACTIVE_REQUIREMENT"),
    "CONSTRAINT": (30, "ACTIVE_CONSTRAINT"),
    "FEATURE": (35, "ACTIVE_FEATURE"),
    "MENU": (36, "ACTIVE_MENU"),
    "RELEASE": (37, "ACTIVE_RELEASE"),
    "RISK": (40, "ACTIVE_RISK"),
    "OPEN_QUESTION": (41, "ACTIVE_OPEN_QUESTION"),
    "TEST_CASE": (50, "ACTIVE_TEST_CASE"),
    "EXECUTION": (60, "REGRESSION_EXECUTION"),
    "ISSUE": (70, "OPEN_ISSUE"),
}


class UngroundedGenerationContext(ValueError):
    """A generation context cannot be proven from current immutable evidence."""


class AutomaticGenerationRootConflict(RuntimeError):
    """The canonical automatic root exists but is not a trusted sealed receipt."""


class GenerationJobStateConflict(RuntimeError):
    """A lifecycle operation was requested from an illegal persisted state."""

    def __init__(
        self,
        job_id: uuid.UUID,
        operation: str,
        actual_status: str,
        allowed_statuses: tuple[str, ...],
    ) -> None:
        self.job_id = job_id
        self.operation = operation
        self.actual_status = actual_status
        self.allowed_statuses = allowed_statuses
        super().__init__(f"generation job {job_id} cannot {operation.lower()} from {actual_status}")


@dataclass(frozen=True, slots=True)
class ContextEntry:
    knowledge_evidence_id: uuid.UUID
    node_id: uuid.UUID | None
    edge_id: uuid.UUID | None
    source_record_id: uuid.UUID
    source_kind: str
    source_entity_type: str
    source_entity_id: uuid.UUID
    source_revision: str
    source_content_hash: str
    source_permalink: str | None
    rank: int
    reason: str
    node_snapshot: dict[str, Any] | None
    edge_snapshot: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class BuiltGenerationContext:
    slack_work_item_id: uuid.UUID
    source_version: int
    ontology_schema_id: uuid.UUID
    as_of: datetime
    entries: tuple[ContextEntry, ...]


@dataclass(frozen=True, slots=True)
class PersistedGenerationContext:
    job: GenerationJob
    entries: tuple[GenerationContext, ...]

    @property
    def provider_input(self) -> Mapping[str, object]:
        """Return only the complete immutable input captured for this exact job."""

        if self.job.generation_input_schema_version == "generation-suggestion/1.0.0":
            from tc_backend.generation.suggestions import validate_suggestion_input

            try:
                return validate_suggestion_input(self.job)
            except (TypeError, ValueError) as error:
                raise UngroundedGenerationContext(
                    "generation suggestion job has no trusted sealed input"
                ) from error

        reconstructed = reconstruct_generation_input(self.job, context_valid=True)
        if reconstructed.availability != "AVAILABLE" or reconstructed.generation_input is None:
            raise UngroundedGenerationContext("generation job has no trusted sealed input")
        return reconstructed.generation_input


@dataclass(frozen=True, slots=True)
class AutomaticGenerationResolution:
    persisted: PersistedGenerationContext
    created: bool


@dataclass(frozen=True, slots=True)
class PreparedAutomaticGeneration:
    """One live-read boundary shared by impact classification and job sealing."""

    job_id: uuid.UUID
    context: BuiltGenerationContext
    generation_input: dict[str, Any]
    generation_input_digest: str
    prompt_version: str
    schema_version: str


@dataclass(frozen=True, slots=True)
class PersistedGenerationComparison:
    """Immutable rows required by the historical comparison read model."""

    job: GenerationJob
    entries: tuple[GenerationContext, ...]
    apply: GenerationApply | None
    apply_outcomes: tuple[GenerationApplyOutcome, ...]


@dataclass(frozen=True, slots=True)
class _GroundedCandidate:
    evidence: KnowledgeEvidence
    source: KnowledgeSourceRecord
    node: KnowledgeNode | None
    edge: KnowledgeEdge | None
    distance: int
    priority: int
    reason: str

    @property
    def resource_id(self) -> uuid.UUID:
        resource = self.node if self.node is not None else self.edge
        if resource is None:  # pragma: no cover - constructed internally
            raise AssertionError("a grounded candidate requires one resource")
        return resource.id

    @property
    def resource_recorded_at(self) -> datetime:
        resource = self.node if self.node is not None else self.edge
        if resource is None:  # pragma: no cover - constructed internally
            raise AssertionError("a grounded candidate requires one resource")
        return resource.recorded_at


class GenerationRepository:
    """Build and seal grounded generation contexts in the caller's transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _lock_work_item(self, slack_work_item_id: uuid.UUID) -> SlackWorkItem:
        work_item = self.session.scalar(
            select(SlackWorkItem)
            .where(SlackWorkItem.id == slack_work_item_id)
            .with_for_update()
        )
        if work_item is None:
            raise UngroundedGenerationContext(
                f"Slack work item not found: {slack_work_item_id}"
            )
        return work_item

    def build_context(
        self,
        slack_work_item_id: uuid.UUID,
        *,
        max_depth: int = 6,
        as_of: datetime | None = None,
    ) -> BuiltGenerationContext:
        if max_depth < 1:
            raise ValueError("max_depth must be at least one")
        effective_at = as_of or datetime.now(UTC)
        if effective_at.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        if effective_at > datetime.now(UTC):
            raise ValueError("as_of cannot be in the future")

        work_item = self._lock_work_item(slack_work_item_id)
        if not work_item.root_seen or work_item.source_version < 1:
            raise UngroundedGenerationContext(
                "Slack work item has no complete current source revision"
            )

        ontology = self.session.scalar(select(OntologySchema).where(OntologySchema.active))
        if ontology is None:
            raise UngroundedGenerationContext("no active ontology schema is available")

        active_nodes = tuple(
            self.session.scalars(
                select(KnowledgeNode).where(
                    KnowledgeNode.ontology_schema_id == ontology.id,
                    KnowledgeNode.status == "ACTIVE",
                    or_(
                        KnowledgeNode.valid_from.is_(None),
                        KnowledgeNode.valid_from <= effective_at,
                    ),
                    KnowledgeNode.valid_to.is_(None),
                )
            )
        )
        nodes_by_id = {node.id: node for node in active_nodes}
        root_matches = tuple(
            node
            for node in active_nodes
            if node.node_type == "WORK_ITEM"
            and node.entity_type == "SLACK_WORK_ITEM"
            and node.entity_id == slack_work_item_id
        )
        if len(root_matches) != 1:
            raise UngroundedGenerationContext(
                "Slack work item requires exactly one current ACTIVE knowledge root"
            )
        root = root_matches[0]
        if _integer_property(root.properties, "sourceVersion") != work_item.source_version:
            raise UngroundedGenerationContext(
                "Slack knowledge projection is stale for the current source version"
            )

        active_edges = tuple(
            self.session.scalars(
                select(KnowledgeEdge).where(
                    KnowledgeEdge.ontology_schema_id == ontology.id,
                    KnowledgeEdge.status == "ACTIVE",
                    or_(
                        KnowledgeEdge.valid_from.is_(None),
                        KnowledgeEdge.valid_from <= effective_at,
                    ),
                    KnowledgeEdge.valid_to.is_(None),
                )
            )
        )
        reachable_distances = _walk_active_graph(
            root.id,
            nodes_by_id=nodes_by_id,
            edges=active_edges,
            max_depth=max_depth,
        )
        eligible_ids = {
            node_id
            for node_id in reachable_distances
            if self._eligible_node(nodes_by_id[node_id], root_id=root.id)
        }
        eligible_ids.add(root.id)

        # Filtering PASS/deprecated/closed ledger nodes can create a hole in a
        # path.  Walk the eligible subgraph again so no disconnected resource
        # leaks into a context merely because it was reachable through an
        # ineligible ledger node.
        selected_nodes = {node_id: nodes_by_id[node_id] for node_id in eligible_ids}
        selected_edges = tuple(
            edge
            for edge in active_edges
            if edge.source_node_id in eligible_ids and edge.target_node_id in eligible_ids
        )
        selected_distances = _walk_active_graph(
            root.id,
            nodes_by_id=selected_nodes,
            edges=selected_edges,
            max_depth=max_depth,
        )
        selected_nodes = {node_id: selected_nodes[node_id] for node_id in selected_distances}
        selected_edges = tuple(
            edge
            for edge in selected_edges
            if edge.source_node_id in selected_nodes and edge.target_node_id in selected_nodes
        )

        grounded = self._ground_candidates(
            root_id=root.id,
            nodes=tuple(selected_nodes.values()),
            edges=selected_edges,
            distances=selected_distances,
            ontology_schema_id=ontology.id,
        )
        entries = _rank_context_entries(grounded)
        if not entries:
            raise UngroundedGenerationContext("generation context has no grounded source")
        return BuiltGenerationContext(
            slack_work_item_id=work_item.id,
            source_version=work_item.source_version,
            ontology_schema_id=ontology.id,
            as_of=effective_at,
            entries=entries,
        )

    def create_job_with_context(
        self,
        *,
        slack_work_item_id: uuid.UUID,
        generation_version: int,
        provider: str,
        model: str,
        prompt_version: str,
        schema_version: str,
        max_depth: int = 6,
    ) -> PersistedGenerationContext:
        if generation_version < 1:
            raise ValueError("generation_version must be at least one")
        provenance = {
            "provider": _nonblank(provider, "provider", maximum=100),
            "model": _nonblank(model, "model", maximum=200),
            "prompt_version": _nonblank(prompt_version, "prompt_version", maximum=100),
            "schema_version": _nonblank(schema_version, "schema_version", maximum=100),
        }
        context = self.build_context(slack_work_item_id)
        job_id = uuid.uuid4()
        generation_input, input_digest = self._capture_generation_input(
            job_id=job_id,
            output_schema_version=provenance["schema_version"],
            context=context,
        )
        job = GenerationJob(
            id=job_id,
            slack_work_item_id=slack_work_item_id,
            source_version=context.source_version,
            generation_version=generation_version,
            generation_trigger=GenerationTrigger.MANUAL.value,
            status=GenerationJobStatus.QUEUED.value,
            ontology_schema_id=context.ontology_schema_id,
            root_job_id=job_id,
            retry_of_job_id=None,
            attempt_number=1,
            generation_input_capture_state=GenerationInputCaptureState.SEALED.value,
            generation_input_schema_version=GENERATION_INPUT_SCHEMA_VERSION,
            generation_input=generation_input,
            generation_input_digest=input_digest,
            **provenance,
        )
        self.session.add(job)
        self.session.flush()
        entries = self.persist_context(job.id, context)
        return PersistedGenerationContext(job=job, entries=entries)

    def create_next_job_with_context(
        self,
        *,
        slack_work_item_id: uuid.UUID,
        prompt_version: str = "generation_prompt_v1",
        schema_version: str = "1.0.0",
        max_depth: int = 6,
    ) -> PersistedGenerationContext:
        """Seal the next root context without selecting or calling a provider."""

        context = self.build_context(slack_work_item_id, max_depth=max_depth)
        generation_version = self._next_generation_version(slack_work_item_id)
        return self._create_unconfigured_root(
            context=context,
            generation_version=generation_version,
            generation_trigger=GenerationTrigger.MANUAL.value,
            prompt_version=prompt_version,
            schema_version=schema_version,
        )

    def get_or_create_automatic_job_with_context(
        self,
        *,
        slack_work_item_id: uuid.UUID,
        source_version: int,
        prompt_version: str = "generation_prompt_v1",
        schema_version: str = "1.0.0",
        max_depth: int = 6,
        prepared: PreparedAutomaticGeneration | None = None,
    ) -> AutomaticGenerationResolution:
        """Resolve the one automatic root for a canonical Slack source snapshot."""

        if source_version < 1:
            raise ValueError("source_version must be at least one")
        work_item = self._lock_work_item(slack_work_item_id)
        if not work_item.root_seen or work_item.source_version < 1:
            raise UngroundedGenerationContext(
                "Slack work item has no complete current source revision"
            )
        if work_item.source_version != source_version:
            raise UngroundedGenerationContext(
                "automatic generation source is no longer the current Slack snapshot"
            )
        existing = self.session.scalar(
            select(GenerationJob).where(
                GenerationJob.slack_work_item_id == slack_work_item_id,
                GenerationJob.source_version == source_version,
                GenerationJob.generation_trigger == GenerationTrigger.AUTOMATIC.value,
                GenerationJob.retry_of_job_id.is_(None),
            )
        )
        if existing is not None:
            if (
                existing.root_job_id != existing.id
                or existing.provider != "UNCONFIGURED"
                or existing.model != "UNCONFIGURED"
                or existing.generation_input_capture_state
                != GenerationInputCaptureState.SEALED.value
                or existing.generation_input_schema_version
                not in {GENERATION_INPUT_SCHEMA_VERSION, "generation-suggestion/1.0.0"}
            ):
                raise AutomaticGenerationRootConflict(
                    "automatic generation root provenance is incomplete"
                )
            try:
                persisted = self.load_context(existing.id)
                _ = persisted.provider_input
            except UngroundedGenerationContext as error:
                raise AutomaticGenerationRootConflict(
                    "automatic generation root seal is invalid"
                ) from error
            return AutomaticGenerationResolution(persisted=persisted, created=False)

        if prepared is None:
            prepared = self.prepare_automatic_generation(
                slack_work_item_id=slack_work_item_id,
                source_version=source_version,
                prompt_version=prompt_version,
                schema_version=schema_version,
                max_depth=max_depth,
            )
        elif (
            prepared.context.slack_work_item_id != slack_work_item_id
            or prepared.context.source_version != source_version
            or prepared.prompt_version != prompt_version
            or prepared.schema_version != schema_version
        ):
            raise UngroundedGenerationContext(
                "prepared automatic generation boundary does not match the request"
            )
        persisted = self._create_unconfigured_root(
            context=prepared.context,
            generation_version=self._next_generation_version(slack_work_item_id),
            generation_trigger=GenerationTrigger.AUTOMATIC.value,
            prompt_version=prompt_version,
            schema_version=schema_version,
            prepared=prepared,
        )
        return AutomaticGenerationResolution(persisted=persisted, created=True)

    def prepare_automatic_generation(
        self,
        *,
        slack_work_item_id: uuid.UUID,
        source_version: int,
        prompt_version: str = "generation_prompt_v1",
        schema_version: str = "1.0.0",
        max_depth: int = 6,
    ) -> PreparedAutomaticGeneration:
        """Build and capture the current input once without appending a job."""

        prompt_version = _nonblank(prompt_version, "prompt_version", maximum=100)
        schema_version = _nonblank(schema_version, "schema_version", maximum=100)
        work_item = self._lock_work_item(slack_work_item_id)
        if work_item.source_version != source_version:
            raise UngroundedGenerationContext(
                "automatic generation source is no longer the current Slack snapshot"
            )
        context = self.build_context(slack_work_item_id, max_depth=max_depth)
        job_id = uuid.uuid4()
        generation_input, input_digest = self._capture_generation_input(
            job_id=job_id,
            output_schema_version=schema_version,
            context=context,
        )
        return PreparedAutomaticGeneration(
            job_id=job_id,
            context=context,
            generation_input=generation_input,
            generation_input_digest=input_digest,
            prompt_version=prompt_version,
            schema_version=schema_version,
        )

    def _create_unconfigured_root(
        self,
        *,
        context: BuiltGenerationContext,
        generation_version: int,
        generation_trigger: str,
        prompt_version: str,
        schema_version: str,
        prepared: PreparedAutomaticGeneration | None = None,
    ) -> PersistedGenerationContext:
        prompt_version = _nonblank(prompt_version, "prompt_version", maximum=100)
        schema_version = _nonblank(schema_version, "schema_version", maximum=100)
        if prepared is None:
            job_id = uuid.uuid4()
            generation_input, input_digest = self._capture_generation_input(
                job_id=job_id,
                output_schema_version=schema_version,
                context=context,
            )
        else:
            if prepared.context is not context:
                raise UngroundedGenerationContext(
                    "prepared automatic generation context identity changed"
                )
            job_id = prepared.job_id
            generation_input = copy.deepcopy(prepared.generation_input)
            input_digest = prepared.generation_input_digest
        job = GenerationJob(
            id=job_id,
            slack_work_item_id=context.slack_work_item_id,
            source_version=context.source_version,
            generation_version=generation_version,
            generation_trigger=generation_trigger,
            status=GenerationJobStatus.QUEUED.value,
            provider="UNCONFIGURED",
            model="UNCONFIGURED",
            prompt_version=prompt_version,
            schema_version=schema_version,
            ontology_schema_id=context.ontology_schema_id,
            root_job_id=job_id,
            retry_of_job_id=None,
            attempt_number=1,
            generation_input_capture_state=GenerationInputCaptureState.SEALED.value,
            generation_input_schema_version=GENERATION_INPUT_SCHEMA_VERSION,
            generation_input=generation_input,
            generation_input_digest=input_digest,
        )
        self.session.add(job)
        self.session.flush()
        entries = self.persist_context(job.id, context)
        return PersistedGenerationContext(job=job, entries=entries)

    def _next_generation_version(self, slack_work_item_id: uuid.UUID) -> int:
        """Allocate under the canonical SlackWorkItem lock held by the caller."""

        return (
            self.session.scalar(
                select(func.max(GenerationJob.generation_version)).where(
                    GenerationJob.slack_work_item_id == slack_work_item_id
                )
            )
            or 0
        ) + 1

    def _capture_generation_input(
        self,
        *,
        job_id: uuid.UUID,
        output_schema_version: str,
        context: BuiltGenerationContext,
    ) -> tuple[dict[str, Any], str]:
        try:
            payload = _assemble_generation_input(
                self.session,
                job_id=job_id,
                output_schema_version=output_schema_version,
                context=context,
            )
            normalized, digest = seal_generation_input(payload)
        except (GenerationInputCaptureError, LookupError, TypeError, ValueError) as error:
            raise UngroundedGenerationContext(
                "trusted Generation Input 1.0.0 could not be sealed"
            ) from error
        return normalized, digest.value

    def retry_job_with_context(
        self,
        source_job_id: uuid.UUID,
    ) -> PersistedGenerationContext:
        """Append a QUEUED retry that exactly copies its failed predecessor's seal."""

        source = self.session.scalar(
            select(GenerationJob).where(GenerationJob.id == source_job_id).with_for_update()
        )
        if source is None:
            raise LookupError(f"generation job not found: {source_job_id}")
        if source.status != GenerationJobStatus.FAILED.value:
            raise GenerationJobStateConflict(
                source.id, "RETRY", source.status, (GenerationJobStatus.FAILED.value,)
            )
        existing_retry = self.session.scalar(
            select(GenerationJob.id).where(GenerationJob.retry_of_job_id == source.id)
        )
        if existing_retry is not None:
            raise GenerationJobStateConflict(
                source.id, "RETRY", source.status, (GenerationJobStatus.FAILED.value,)
            )
        source_context = self.load_context(source.id)
        self._lock_work_item(source.slack_work_item_id)
        generation_version = self._next_generation_version(source.slack_work_item_id)
        job_id = uuid.uuid4()
        retry_as_of = datetime.now(UTC)
        capture_state = source.generation_input_capture_state
        input_schema_version: str | None = None
        generation_input: dict[str, Any] | None = None
        input_digest: str | None = None
        if capture_state == GenerationInputCaptureState.SEALED.value:
            if not isinstance(source.generation_input, dict):
                raise UngroundedGenerationContext("retry source input capture is incomplete")
            retry_input = copy.deepcopy(source.generation_input)
            if source.generation_input_schema_version == "generation-suggestion/1.0.0":
                from tc_backend.generation.suggestions import (
                    SUGGESTION_SCHEMA_VERSION,
                    validate_suggestion_input,
                )

                try:
                    validate_suggestion_input(source)
                    base = retry_input["baseGenerationInput"]
                    if not isinstance(base, dict):
                        raise ValueError("base input is invalid")
                    retry_input["generationJobId"] = _identifier("generation_job", job_id)
                    base["requestId"] = _identifier("generation_request", job_id)
                    base["createdAt"] = _isoformat(retry_as_of)
                    generation_input = retry_input
                    input_digest = canonical_input_digest(retry_input).value
                except (TypeError, ValueError) as error:
                    raise UngroundedGenerationContext(
                        "retry suggestion input capture is invalid"
                    ) from error
                input_schema_version = SUGGESTION_SCHEMA_VERSION
            else:
                retry_input["requestId"] = _identifier("generation_request", job_id)
                retry_input["createdAt"] = _isoformat(retry_as_of)
                try:
                    generation_input, sealed_digest = seal_generation_input(retry_input)
                except GenerationInputCaptureError as error:
                    raise UngroundedGenerationContext(
                        "retry source input capture is invalid"
                    ) from error
                input_schema_version = GENERATION_INPUT_SCHEMA_VERSION
                input_digest = sealed_digest.value
        elif not (
            capture_state == GenerationInputCaptureState.LEGACY_NOT_CAPTURED.value
            and source.generation_input_schema_version is None
            and source.generation_input is None
            and source.generation_input_digest is None
        ):
            raise UngroundedGenerationContext("retry source input capture is incomplete")
        job = GenerationJob(
            id=job_id,
            slack_work_item_id=source.slack_work_item_id,
            source_version=source.source_version,
            generation_version=generation_version,
            generation_trigger=source.generation_trigger,
            status=GenerationJobStatus.QUEUED.value,
            provider=source.provider,
            model=source.model,
            prompt_version=source.prompt_version,
            schema_version=source.schema_version,
            ontology_schema_id=source.ontology_schema_id,
            root_job_id=source.root_job_id,
            retry_of_job_id=source.id,
            attempt_number=source.attempt_number + 1,
            generation_input_capture_state=capture_state,
            generation_input_schema_version=input_schema_version,
            generation_input=generation_input,
            generation_input_digest=input_digest,
            version_set_id=source.version_set_id,
            version_set_digest=source.version_set_digest,
        )
        context = BuiltGenerationContext(
            slack_work_item_id=source.slack_work_item_id,
            source_version=source.source_version,
            ontology_schema_id=source.ontology_schema_id,
            as_of=retry_as_of,
            entries=tuple(_entry_from_model(row) for row in source_context.entries),
        )
        self.session.add(job)
        self.session.flush()
        entries = self.persist_context(job.id, context)
        return PersistedGenerationContext(job=job, entries=entries)

    def begin_execution(self, generation_job_id: uuid.UUID) -> GenerationJob:
        job = self._locked_job(generation_job_id)
        if job.status != GenerationJobStatus.QUEUED.value:
            raise GenerationJobStateConflict(
                job.id, "EXECUTE", job.status, (GenerationJobStatus.QUEUED.value,)
            )
        now = datetime.now(UTC)
        job.status = GenerationJobStatus.RUNNING.value
        job.started_at = now
        job.updated_at = now
        self.session.flush()
        return job

    def succeed_execution(
        self,
        generation_job_id: uuid.UUID,
        result: Mapping[str, object],
    ) -> GenerationJob:
        job = self._locked_job(generation_job_id)
        if job.status != GenerationJobStatus.RUNNING.value:
            raise GenerationJobStateConflict(
                job.id, "EXECUTE", job.status, (GenerationJobStatus.RUNNING.value,)
            )
        now = datetime.now(UTC)
        job.status = GenerationJobStatus.SUCCEEDED.value
        job.result = dict(result)
        job.completed_at = now
        job.updated_at = now
        self.session.flush()
        return job

    def fail_execution(
        self,
        generation_job_id: uuid.UUID,
        *,
        code: str,
        message: str,
        retryable: bool,
        attempts: int,
    ) -> GenerationJob:
        job = self._locked_job(generation_job_id)
        if job.status != GenerationJobStatus.RUNNING.value:
            raise GenerationJobStateConflict(
                job.id, "EXECUTE", job.status, (GenerationJobStatus.RUNNING.value,)
            )
        if not 1 <= attempts <= 5:
            raise ValueError("attempts must be between one and five")
        now = datetime.now(UTC)
        job.status = GenerationJobStatus.FAILED.value
        job.result = None
        job.error_code = _nonblank(code, "code", maximum=80)
        job.error = _nonblank(message, "message", maximum=500)
        job.error_retryable = retryable
        job.error_attempts = attempts
        job.completed_at = now
        job.updated_at = now
        self.session.flush()
        return job

    def get_job(self, generation_job_id: uuid.UUID, *, for_update: bool = False) -> GenerationJob:
        statement = select(GenerationJob).where(GenerationJob.id == generation_job_id)
        if for_update:
            statement = statement.with_for_update()
        job = self.session.scalar(statement)
        if job is None:
            raise LookupError(f"generation job not found: {generation_job_id}")
        return job

    def _locked_job(self, generation_job_id: uuid.UUID) -> GenerationJob:
        return self.get_job(generation_job_id, for_update=True)

    def persist_context(
        self,
        generation_job_id: uuid.UUID,
        context: BuiltGenerationContext,
    ) -> tuple[GenerationContext, ...]:
        job = self.session.scalar(
            select(GenerationJob).where(GenerationJob.id == generation_job_id).with_for_update()
        )
        if job is None:
            raise LookupError(f"generation job not found: {generation_job_id}")
        if job.context_sealed_at is not None:
            raise RuntimeError("generation job context is already sealed")
        if (
            job.slack_work_item_id != context.slack_work_item_id
            or job.source_version != context.source_version
            or job.ontology_schema_id != context.ontology_schema_id
        ):
            raise UngroundedGenerationContext(
                "generation job provenance does not match the built context"
            )
        existing = self.session.scalar(
            select(GenerationContext.id)
            .where(GenerationContext.generation_job_id == generation_job_id)
            .limit(1)
        )
        if existing is not None:
            raise RuntimeError("generation job contains an unsealed partial context")
        if not context.entries:
            raise UngroundedGenerationContext("generation context cannot be empty")

        rows = tuple(
            GenerationContext(
                generation_job_id=job.id,
                knowledge_evidence_id=entry.knowledge_evidence_id,
                node_id=entry.node_id,
                edge_id=entry.edge_id,
                source_record_id=entry.source_record_id,
                source_kind=entry.source_kind,
                source_entity_type=entry.source_entity_type,
                source_entity_id=entry.source_entity_id,
                source_revision=entry.source_revision,
                source_content_hash=entry.source_content_hash,
                source_permalink=entry.source_permalink,
                rank=entry.rank,
                reason=entry.reason,
                node_snapshot=entry.node_snapshot,
                edge_snapshot=entry.edge_snapshot,
            )
            for entry in context.entries
        )
        self.session.add_all(rows)
        self.session.flush()
        job.context_item_count = len(rows)
        job.context_digest = generation_context_digest(job, context.entries)
        job.context_sealed_at = context.as_of
        job.updated_at = datetime.now(UTC)
        self.session.flush()
        return rows

    def load_context(self, generation_job_id: uuid.UUID) -> PersistedGenerationContext:
        job = self.session.get(GenerationJob, generation_job_id)
        if job is None:
            raise LookupError(f"generation job not found: {generation_job_id}")
        if job.context_sealed_at is None or job.context_digest is None:
            raise UngroundedGenerationContext("generation job context is not sealed")
        rows = tuple(
            self.session.scalars(
                select(GenerationContext)
                .where(GenerationContext.generation_job_id == generation_job_id)
                .order_by(GenerationContext.rank)
            )
        )
        if len(rows) != job.context_item_count:
            raise UngroundedGenerationContext("generation context count does not match its seal")
        entries = tuple(_entry_from_model(row) for row in rows)
        if generation_context_digest(job, entries) != job.context_digest:
            raise UngroundedGenerationContext("generation context digest does not match its seal")
        return PersistedGenerationContext(job=job, entries=rows)

    def load_reconstruction(self, generation_job_id: uuid.UUID) -> GenerationReconstruction:
        """Read and classify only the immutable job/input/context snapshots."""

        job = self.session.get(GenerationJob, generation_job_id)
        if job is None:
            raise LookupError(f"generation job not found: {generation_job_id}")
        try:
            self.load_context(generation_job_id)
        except UngroundedGenerationContext:
            return reconstruct_generation_input(job, context_valid=False)
        return reconstruct_generation_input(job, context_valid=True)

    def load_comparison(self, generation_job_id: uuid.UUID) -> PersistedGenerationComparison:
        """Read only sealed context and immutable apply rows in their stored order."""

        persisted = self.load_context(generation_job_id)
        apply = self.session.scalar(
            select(GenerationApply).where(GenerationApply.generation_job_id == generation_job_id)
        )
        outcomes: tuple[GenerationApplyOutcome, ...] = ()
        if apply is not None:
            outcomes = tuple(
                self.session.scalars(
                    select(GenerationApplyOutcome)
                    .where(GenerationApplyOutcome.generation_apply_id == apply.id)
                    .order_by(GenerationApplyOutcome.generated_case_index)
                )
            )
        return PersistedGenerationComparison(
            job=persisted.job,
            entries=persisted.entries,
            apply=apply,
            apply_outcomes=outcomes,
        )

    def _eligible_node(self, node: KnowledgeNode, *, root_id: uuid.UUID) -> bool:
        if node.id == root_id:
            return True
        if node.node_type == "WORK_ITEM":
            return False
        if node.node_type in _GENERIC_KNOWLEDGE_TYPES:
            return True
        if node.node_type == "TEST_CASE":
            return self._active_test_case(node)
        if node.node_type == "EXECUTION":
            return self._risk_execution(node)
        if node.node_type == "ISSUE":
            return self._open_issue(node)
        return False

    def _active_test_case(self, node: KnowledgeNode) -> bool:
        if node.entity_type != "TEST_CASE" or node.entity_id is None:
            raise UngroundedGenerationContext("TEST_CASE knowledge lacks a ledger identity")
        test_case = self.session.get(TestCase, node.entity_id)
        if test_case is None:
            raise UngroundedGenerationContext("TEST_CASE knowledge references a missing case")
        if test_case.deleted_at is not None or test_case.status != "ACTIVE":
            return False
        version = self.session.get(TestCaseVersion, test_case.current_version_id)
        if version is None or version.test_case_id != test_case.id:
            raise UngroundedGenerationContext("active test case lacks its current version")
        if str(node.properties.get("currentVersionId") or "") != str(version.id):
            raise UngroundedGenerationContext(
                "TEST_CASE knowledge projection is stale for its current version"
            )
        return version.status == "ACTIVE"

    def _risk_execution(self, node: KnowledgeNode) -> bool:
        if node.entity_type != "EXECUTION_ATTEMPT" or node.entity_id is None:
            raise UngroundedGenerationContext("EXECUTION knowledge lacks a ledger identity")
        attempt = self.session.get(ExecutionAttempt, node.entity_id)
        if attempt is None:
            raise UngroundedGenerationContext("EXECUTION knowledge references a missing attempt")
        if (
            str(node.properties.get("id") or "") != str(attempt.id)
            or str(node.properties.get("cycleItemId") or "") != str(attempt.cycle_item_id)
            or str(node.properties.get("status") or "") != attempt.status
        ):
            raise UngroundedGenerationContext(
                "EXECUTION knowledge projection is stale for its ledger attempt"
            )
        if attempt.status not in _RISK_EXECUTION_STATUSES:
            return False
        item = self.session.get(CycleItem, attempt.cycle_item_id)
        if item is None:
            raise UngroundedGenerationContext("execution attempt lacks its pinned cycle item")
        test_case = self.session.get(TestCase, item.test_case_id)
        if test_case is None:
            raise UngroundedGenerationContext("execution attempt lacks its test case")
        return test_case.deleted_at is None and test_case.status == "ACTIVE"

    def _open_issue(self, node: KnowledgeNode) -> bool:
        if node.entity_type != "ISSUE" or node.entity_id is None:
            raise UngroundedGenerationContext("ISSUE knowledge lacks a ledger identity")
        issue = self.session.get(Issue, node.entity_id)
        if issue is None:
            raise UngroundedGenerationContext("ISSUE knowledge references a missing issue")
        if (
            str(node.properties.get("id") or "") != str(issue.id)
            or str(node.properties.get("status") or "") != issue.status
            or _integer_property(node.properties, "resourceVersion") != issue.resource_version
        ):
            raise UngroundedGenerationContext(
                "ISSUE knowledge projection is stale for its ledger issue"
            )
        return issue.deleted_at is None and issue.status in _OPEN_ISSUE_STATUSES

    def _ground_candidates(
        self,
        *,
        root_id: uuid.UUID,
        nodes: tuple[KnowledgeNode, ...],
        edges: tuple[KnowledgeEdge, ...],
        distances: dict[uuid.UUID, int],
        ontology_schema_id: uuid.UUID,
    ) -> tuple[_GroundedCandidate, ...]:
        node_by_id = {node.id: node for node in nodes}
        edge_by_id = {edge.id: edge for edge in edges}
        rows = tuple(
            self.session.execute(
                select(KnowledgeEvidence, KnowledgeSourceRecord)
                .join(
                    KnowledgeSourceRecord,
                    KnowledgeSourceRecord.id == KnowledgeEvidence.source_record_id,
                )
                .where(
                    or_(
                        KnowledgeEvidence.knowledge_node_id.in_(tuple(node_by_id)),
                        KnowledgeEvidence.knowledge_edge_id.in_(tuple(edge_by_id)),
                    )
                )
            ).all()
        )
        evidence_by_node: dict[uuid.UUID, list[tuple[KnowledgeEvidence, KnowledgeSourceRecord]]] = (
            defaultdict(list)
        )
        evidence_by_edge: dict[uuid.UUID, list[tuple[KnowledgeEvidence, KnowledgeSourceRecord]]] = (
            defaultdict(list)
        )
        for evidence, source in rows:
            self._validate_evidence(evidence, source, ontology_schema_id)
            if evidence.knowledge_node_id is not None:
                evidence_by_node[evidence.knowledge_node_id].append((evidence, source))
            elif evidence.knowledge_edge_id is not None:
                evidence_by_edge[evidence.knowledge_edge_id].append((evidence, source))

        candidates: list[_GroundedCandidate] = []
        for node in sorted(nodes, key=lambda item: item.id.hex):
            evidence_rows = evidence_by_node.get(node.id, [])
            if not evidence_rows:
                raise UngroundedGenerationContext(
                    f"ACTIVE knowledge node lacks evidence: {node.id}"
                )
            priority, reason = _node_rank(node, root_id=root_id)
            for evidence, source in evidence_rows:
                candidates.append(
                    _GroundedCandidate(
                        evidence=evidence,
                        source=source,
                        node=node,
                        edge=None,
                        distance=distances[node.id],
                        priority=priority,
                        reason=reason,
                    )
                )
        for edge in sorted(edges, key=lambda item: item.id.hex):
            evidence_rows = evidence_by_edge.get(edge.id, [])
            if not evidence_rows:
                raise UngroundedGenerationContext(
                    f"ACTIVE knowledge edge lacks evidence: {edge.id}"
                )
            distance = min(distances[edge.source_node_id], distances[edge.target_node_id])
            for evidence, source in evidence_rows:
                candidates.append(
                    _GroundedCandidate(
                        evidence=evidence,
                        source=source,
                        node=None,
                        edge=edge,
                        distance=distance,
                        priority=80,
                        reason=f"ACTIVE_RELATION_{edge.predicate}",
                    )
                )
        return tuple(candidates)

    @staticmethod
    def _validate_evidence(
        evidence: KnowledgeEvidence,
        source: KnowledgeSourceRecord,
        ontology_schema_id: uuid.UUID,
    ) -> None:
        revision = (evidence.source_revision or "").strip()
        if not revision:
            raise UngroundedGenerationContext(
                f"knowledge evidence lacks a source revision: {evidence.id}"
            )
        if (
            evidence.ontology_schema_id != ontology_schema_id
            or evidence.source_record_id != source.id
            or evidence.source_type != source.entity_type
            or evidence.source_id != source.entity_id
            or revision != source.revision_key
            or evidence.excerpt_hash != source.content_hash
            or evidence.permalink != source.permalink
            or not source.content_hash.strip()
        ):
            raise UngroundedGenerationContext(
                f"knowledge evidence/source provenance mismatch: {evidence.id}"
            )


def generation_context_digest(job: GenerationJob, entries: tuple[ContextEntry, ...]) -> str:
    """Hash all immutable context/provenance fields in canonical rank order."""

    ordered = sorted(entries, key=lambda entry: entry.rank)
    payload = {
        "job": {
            "slackWorkItemId": str(job.slack_work_item_id),
            "sourceVersion": job.source_version,
            "generationVersion": job.generation_version,
            "provider": job.provider,
            "model": job.model,
            "promptVersion": job.prompt_version,
            "schemaVersion": job.schema_version,
            "ontologySchemaId": str(job.ontology_schema_id),
        },
        "entries": [_digest_entry(entry) for entry in ordered],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _walk_active_graph(
    root_id: uuid.UUID,
    *,
    nodes_by_id: dict[uuid.UUID, KnowledgeNode],
    edges: tuple[KnowledgeEdge, ...],
    max_depth: int,
) -> dict[uuid.UUID, int]:
    if root_id not in nodes_by_id:
        return {}
    adjacency: dict[uuid.UUID, list[tuple[uuid.UUID, uuid.UUID]]] = defaultdict(list)
    for edge in edges:
        if edge.source_node_id in nodes_by_id and edge.target_node_id in nodes_by_id:
            adjacency[edge.source_node_id].append((edge.id, edge.target_node_id))
            adjacency[edge.target_node_id].append((edge.id, edge.source_node_id))
    for neighbours in adjacency.values():
        neighbours.sort(key=lambda item: (item[0].hex, item[1].hex))

    distances = {root_id: 0}
    queue: deque[uuid.UUID] = deque((root_id,))
    while queue:
        current = queue.popleft()
        if distances[current] >= max_depth:
            continue
        for _, adjacent in adjacency.get(current, []):
            if adjacent in distances:
                continue
            if adjacent != root_id and nodes_by_id[adjacent].node_type == "WORK_ITEM":
                continue
            distances[adjacent] = distances[current] + 1
            queue.append(adjacent)
    return distances


def _rank_context_entries(
    candidates: tuple[_GroundedCandidate, ...],
) -> tuple[ContextEntry, ...]:
    ordered = sorted(candidates, key=_candidate_sort_key)
    return tuple(
        ContextEntry(
            knowledge_evidence_id=candidate.evidence.id,
            node_id=candidate.node.id if candidate.node is not None else None,
            edge_id=candidate.edge.id if candidate.edge is not None else None,
            source_record_id=candidate.source.id,
            source_kind=candidate.source.source_kind,
            source_entity_type=candidate.source.entity_type,
            source_entity_id=candidate.source.entity_id,
            source_revision=candidate.source.revision_key,
            source_content_hash=candidate.source.content_hash,
            source_permalink=candidate.source.permalink,
            rank=rank,
            reason=candidate.reason,
            node_snapshot=(_node_snapshot(candidate.node) if candidate.node is not None else None),
            edge_snapshot=(_edge_snapshot(candidate.edge) if candidate.edge is not None else None),
        )
        for rank, candidate in enumerate(ordered, start=1)
    )


def _candidate_sort_key(candidate: _GroundedCandidate) -> tuple[Any, ...]:
    # Newer resources rank first within a policy tier.  UUID/source keys then
    # make ties independent of query row order.
    return (
        candidate.priority,
        candidate.distance,
        -_timestamp_micros(candidate.resource_recorded_at),
        0 if candidate.node is not None else 1,
        candidate.resource_id.hex,
        _timestamp_micros(candidate.evidence.recorded_at),
        candidate.source.revision_key,
        candidate.evidence.id.hex,
    )


def _node_rank(node: KnowledgeNode, *, root_id: uuid.UUID) -> tuple[int, str]:
    if node.id == root_id:
        return 0, "SLACK_WORK_ITEM_SOURCE"
    if node.node_type == "EXECUTION":
        status = str(node.properties.get("status") or "").upper()
        if status in _RISK_EXECUTION_STATUSES:
            return 60 if status == "FAIL" else 61, f"REGRESSION_{status}"
    return _NODE_PRIORITY.get(node.node_type, (45, "ACTIVE_KNOWLEDGE"))


def _node_snapshot(node: KnowledgeNode) -> dict[str, Any]:
    return {
        "id": str(node.id),
        "ontologySchemaId": str(node.ontology_schema_id),
        "nodeType": node.node_type,
        "status": node.status,
        "label": node.label,
        "entityType": node.entity_type,
        "entityId": str(node.entity_id) if node.entity_id is not None else None,
        "properties": node.properties,
        "recordedAt": _isoformat(node.recorded_at),
        "validFrom": _isoformat(node.valid_from),
        "validTo": _isoformat(node.valid_to),
    }


def _edge_snapshot(edge: KnowledgeEdge) -> dict[str, Any]:
    return {
        "id": str(edge.id),
        "ontologySchemaId": str(edge.ontology_schema_id),
        "sourceNodeId": str(edge.source_node_id),
        "predicate": edge.predicate,
        "targetNodeId": str(edge.target_node_id),
        "status": edge.status,
        "properties": edge.properties,
        "recordedAt": _isoformat(edge.recorded_at),
        "validFrom": _isoformat(edge.valid_from),
        "validTo": _isoformat(edge.valid_to),
    }


def _entry_from_model(row: GenerationContext) -> ContextEntry:
    return ContextEntry(
        knowledge_evidence_id=row.knowledge_evidence_id,
        node_id=row.node_id,
        edge_id=row.edge_id,
        source_record_id=row.source_record_id,
        source_kind=row.source_kind,
        source_entity_type=row.source_entity_type,
        source_entity_id=row.source_entity_id,
        source_revision=row.source_revision,
        source_content_hash=row.source_content_hash,
        source_permalink=row.source_permalink,
        rank=row.rank,
        reason=row.reason,
        node_snapshot=row.node_snapshot,
        edge_snapshot=row.edge_snapshot,
    )


def _assemble_generation_input(
    session: Session,
    *,
    job_id: uuid.UUID,
    output_schema_version: str,
    context: BuiltGenerationContext,
) -> dict[str, Any]:
    """Materialize the complete trusted request once while live sources are in scope."""

    work_item = session.get(SlackWorkItem, context.slack_work_item_id)
    if work_item is None:
        raise LookupError(f"Slack work item not found: {context.slack_work_item_id}")
    groups = _context_node_groups(context.entries)
    root_rows = next(
        (rows for rows in groups if _is_slack_root(rows[0], context.slack_work_item_id)),
        None,
    )
    if root_rows is None or root_rows[0].node_id is None:
        raise ValueError("sealed context has no Slack root snapshot")
    root_snapshot = root_rows[0].node_snapshot or {}
    root_properties = _snapshot_properties(root_snapshot)
    parsed = work_item.parse_result if isinstance(work_item.parse_result, dict) else {}
    snapshot_structured = root_properties.get("structuredFields")
    if not isinstance(snapshot_structured, dict):
        snapshot_structured = root_properties
    structured = {**snapshot_structured, **parsed}
    field_names = (
        "developmentItem",
        "requestedMenu",
        "featureDescription",
        "requestDetails",
    )
    structured_values = {name: _nullable_input_text(structured.get(name)) for name in field_names}
    raw_missing = structured.get("missingFields")
    if isinstance(raw_missing, list) and all(name in field_names for name in raw_missing):
        missing_fields = _unique_values(str(name) for name in raw_missing)
    else:
        missing_fields = [name for name in field_names if structured_values[name] is None]
    raw_questions = structured.get("openQuestions")
    open_questions = (
        _unique_values(value for value in raw_questions if isinstance(value, str) and value.strip())
        if isinstance(raw_questions, list)
        else []
    )
    source_revision_ids = _unique_values(_source_revision_identifier(row) for row in root_rows)
    permalink = work_item.permalink or next(
        (row.source_permalink for row in root_rows if row.source_permalink), None
    )
    if permalink is None:
        raise ValueError("Slack work item has no sealed permalink")

    active_knowledge = _active_knowledge_input(groups)
    existing_cases = _existing_case_input(session, groups)
    executions = _execution_input(session, groups)
    issues = _open_issue_input(session, groups)
    categories = _category_candidates_input(
        session,
        workspace_id=work_item.workspace_id,
        requested_menu=structured_values["requestedMenu"],
    )
    feature_group = next(
        (
            value
            for value in (
                structured_values["featureDescription"],
                structured_values["developmentItem"],
                structured_values["requestDetails"],
                _nullable_input_text(root_properties.get("content")),
                _nullable_input_text(root_snapshot.get("label")),
            )
            if value is not None
        ),
        "Generation batch",
    )[:500]
    return {
        "inputSchemaVersion": GENERATION_INPUT_SCHEMA_VERSION,
        "requestedOutputSchemaVersion": output_schema_version,
        "requestId": _identifier("generation_request", job_id),
        "createdAt": _isoformat(context.as_of),
        "slackWorkItem": {
            "workItemId": _identifier("slack_work_item", work_item.id),
            "evidenceNodeId": _identifier("knowledge_node", root_rows[0].node_id),
            "sourceVersion": context.source_version,
            "sourceRevisionIds": source_revision_ids,
            "permalink": permalink,
            **structured_values,
            "missingFields": missing_fields,
            "openQuestions": open_questions,
        },
        "activeKnowledge": active_knowledge,
        "existingCases": existing_cases,
        "failedOrBlockedExecutions": executions,
        "openIssues": issues,
        "extraSources": [],
        "categoryCandidates": categories,
        "generationPolicy": {
            "policyVersion": "generation_policy_v1",
            "batch": {
                "batchId": _identifier("generation_batch", job_id),
                "featureGroup": feature_group,
                "batchNumber": 1,
                "isFinalBatch": True,
            },
            "minimumCaseCount": 5,
            "maximumCaseCount": 10,
            "expectedResultMode": "CASE",
            "prioritizeHappyPath": True,
            "includeApplicableCrud": True,
            "allowUnsupportedEdgeCases": False,
            "requireEvidenceReferences": True,
            "unknownFactHandling": "OPEN_QUESTION",
            "sensitiveDataHandling": "REDACT",
            "language": "ko-KR",
        },
    }


def _active_knowledge_input(
    groups: list[tuple[ContextEntry, ...]],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for rows in groups:
        row = rows[0]
        snapshot = row.node_snapshot or {}
        node_type = snapshot.get("nodeType")
        if (
            node_type not in {"REQUIREMENT", "DECISION", "CONSTRAINT"}
            or snapshot.get("status") != "ACTIVE"
            or snapshot.get("validTo") is not None
            or row.node_id is None
        ):
            continue
        properties = _snapshot_properties(snapshot)
        content = _nullable_input_text(properties.get("content"))
        valid_from = snapshot.get("validFrom") or snapshot.get("recordedAt")
        if content is None or not isinstance(valid_from, str):
            raise ValueError("selected active knowledge is incomplete")
        values.append(
            {
                "nodeId": _identifier("knowledge_node", row.node_id),
                "nodeType": node_type,
                "status": "ACTIVE",
                "content": content,
                "validFrom": valid_from,
                "validTo": None,
                "evidenceSourceRevisionIds": _unique_values(
                    _source_revision_identifier(item) for item in rows
                ),
                "rank": len(values) + 1,
                "selectionReason": row.reason,
            }
        )
    return values


def _existing_case_input(
    session: Session,
    groups: list[tuple[ContextEntry, ...]],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for rows in groups:
        row = rows[0]
        snapshot = row.node_snapshot or {}
        if snapshot.get("nodeType") != "TEST_CASE" or row.node_id is None:
            continue
        case_id = _snapshot_uuid(snapshot.get("entityId"))
        properties = _snapshot_properties(snapshot)
        version_id = _snapshot_uuid(
            properties.get("currentVersionId") or properties.get("testCaseVersionId")
        )
        test_case = session.get(TestCase, case_id)
        version = session.get(TestCaseVersion, version_id)
        if (
            test_case is None
            or version is None
            or version.test_case_id != test_case.id
            or test_case.deleted_at is not None
            or test_case.status != "ACTIVE"
        ):
            raise ValueError("selected existing case is no longer current")
        steps = tuple(
            session.scalars(
                select(TestStep)
                .where(TestStep.test_case_version_id == version.id)
                .order_by(TestStep.sequence)
            )
        )
        if not steps:
            raise ValueError("selected existing case has no steps")
        labels = list(
            session.scalars(
                select(Tag.name)
                .join(TestCaseVersionTag, TestCaseVersionTag.tag_id == Tag.id)
                .where(TestCaseVersionTag.test_case_version_id == version.id)
                .order_by(Tag.normalized_name, Tag.id)
            )
        )
        values.append(
            {
                "nodeId": _identifier("knowledge_node", row.node_id),
                "testCaseId": _identifier("case", test_case.id),
                "testCaseVersionId": _identifier("case_version", version.id),
                "caseKey": test_case.case_key,
                "status": "ACTIVE",
                "summary": version.summary,
                "precondition": version.precondition,
                "steps": [
                    {
                        "sequence": step.sequence,
                        "precondition": step.precondition,
                        "action": step.action,
                        "expectedResult": step.expected_result,
                    }
                    for step in steps
                ],
                "expectedResultMode": version.expected_result_mode,
                "caseExpectedResult": version.expected_result,
                "priority": version.priority,
                "labels": labels,
                "rank": len(values) + 1,
                "selectionReason": row.reason,
            }
        )
    return values


def _execution_input(
    session: Session,
    groups: list[tuple[ContextEntry, ...]],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for rows in groups:
        row = rows[0]
        snapshot = row.node_snapshot or {}
        if snapshot.get("nodeType") != "EXECUTION" or row.node_id is None:
            continue
        attempt_id = _snapshot_uuid(snapshot.get("entityId"))
        attempt = session.get(ExecutionAttempt, attempt_id)
        if attempt is None or attempt.status not in _RISK_EXECUTION_STATUSES:
            raise ValueError("selected execution is no longer eligible")
        cycle_item = session.get(CycleItem, attempt.cycle_item_id)
        if cycle_item is None:
            raise ValueError("selected execution has no pinned case version")
        actual_result = _nullable_input_text(attempt.actual_result or attempt.note)
        occurred_at = attempt.finished_at or attempt.started_at
        if actual_result is None or occurred_at is None:
            raise ValueError("selected execution has incomplete result evidence")
        issue_ids = _unique_values(
            _identifier("issue", issue_id)
            for issue_id in session.scalars(
                select(IssueLink.issue_id)
                .where(IssueLink.execution_attempt_id == attempt.id)
                .order_by(IssueLink.created_at, IssueLink.id)
            )
        )
        values.append(
            {
                "nodeId": _identifier("knowledge_node", row.node_id),
                "executionAttemptId": _identifier("execution_attempt", attempt.id),
                "testCaseVersionId": _identifier("case_version", cycle_item.test_case_version_id),
                "status": attempt.status,
                "platform": attempt.platform,
                "actualResult": actual_result,
                "occurredAt": _isoformat(occurred_at),
                "linkedIssueIds": issue_ids,
                "rank": len(values) + 1,
                "selectionReason": row.reason,
            }
        )
    return values


def _open_issue_input(
    session: Session,
    groups: list[tuple[ContextEntry, ...]],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for rows in groups:
        row = rows[0]
        snapshot = row.node_snapshot or {}
        if snapshot.get("nodeType") != "ISSUE" or row.node_id is None:
            continue
        issue_id = _snapshot_uuid(snapshot.get("entityId"))
        issue = session.get(Issue, issue_id)
        if (
            issue is None
            or issue.deleted_at is not None
            or issue.status not in _OPEN_ISSUE_STATUSES
        ):
            raise ValueError("selected issue is no longer open")
        attempt_ids = _unique_values(
            _identifier("execution_attempt", attempt_id)
            for attempt_id in session.scalars(
                select(IssueLink.execution_attempt_id)
                .where(
                    IssueLink.issue_id == issue.id,
                    IssueLink.execution_attempt_id.is_not(None),
                )
                .order_by(IssueLink.created_at, IssueLink.id)
            )
            if attempt_id is not None
        )
        if not attempt_ids:
            continue
        values.append(
            {
                "nodeId": _identifier("knowledge_node", row.node_id),
                "issueId": _identifier("issue", issue.id),
                "issueKey": issue.issue_key,
                "status": issue.status,
                "severity": issue.severity,
                "summary": issue.summary,
                "expectedResult": issue.expected_result,
                "actualResult": issue.actual_result,
                "linkedExecutionAttemptIds": attempt_ids,
                "updatedAt": _isoformat(issue.updated_at),
                "rank": len(values) + 1,
                "selectionReason": row.reason,
            }
        )
    return values


def _category_candidates_input(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    requested_menu: str | None,
) -> list[dict[str, Any]]:
    categories = tuple(
        session.scalars(
            select(Folder).where(
                Folder.workspace_id == workspace_id,
                Folder.parent_id.is_(None),
                Folder.deleted_at.is_(None),
            )
        )
    )
    ordered = sorted(
        categories,
        key=lambda row: (
            row.system_kind == AI_INBOX_SYSTEM_KIND,
            row.sort_order,
            row.normalized_name,
            row.id.hex,
        ),
    )
    if len(ordered) > 100:
        fallback = next((row for row in ordered if row.system_kind == AI_INBOX_SYSTEM_KIND), None)
        ordered = ordered[: 99 if fallback is not None else 100]
        if fallback is not None and fallback not in ordered:
            ordered.append(fallback)
    if not ordered:
        raise ValueError("generation requires at least one active category candidate")
    normalized_request = (requested_menu or "").casefold()
    return [
        {
            "categoryId": _identifier("folder", row.id),
            "label": row.name,
            "score": (
                1.0
                if normalized_request and row.normalized_name.casefold() in normalized_request
                else 0.0
            ),
            "selectionReason": (
                "AI_GENERATED_INBOX_FALLBACK"
                if row.system_kind == AI_INBOX_SYSTEM_KIND
                else "ACTIVE_ROOT_CATEGORY"
            ),
        }
        for row in ordered
    ]


def _context_node_groups(
    entries: tuple[ContextEntry, ...],
) -> list[tuple[ContextEntry, ...]]:
    grouped: dict[uuid.UUID, list[ContextEntry]] = {}
    for entry in sorted(entries, key=lambda item: item.rank):
        if entry.node_id is not None:
            grouped.setdefault(entry.node_id, []).append(entry)
    return [tuple(rows) for rows in grouped.values()]


def _is_slack_root(entry: ContextEntry, slack_work_item_id: uuid.UUID) -> bool:
    snapshot = entry.node_snapshot or {}
    return (
        snapshot.get("nodeType") == "WORK_ITEM"
        and snapshot.get("entityType") == "SLACK_WORK_ITEM"
        and snapshot.get("entityId") == str(slack_work_item_id)
    )


def _snapshot_properties(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = snapshot.get("properties")
    return value if isinstance(value, dict) else {}


def _snapshot_uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        raise ValueError("sealed snapshot identifier is missing")
    return uuid.UUID(value.rsplit("_", 1)[-1] if "_" in value else value)


def _source_revision_identifier(entry: ContextEntry) -> str:
    prefixes = {
        "SLACK_MESSAGE_REVISION": "slack_revision",
        "TEST_CASE_VERSION": "case_version",
        "EXECUTION_ATTEMPT": "execution_attempt",
        "OUTBOX_EVENT": "outbox_event",
    }
    return _identifier(
        prefixes.get(entry.source_entity_type, "source_revision"),
        entry.source_entity_id,
    )


def _identifier(prefix: str, value: uuid.UUID) -> str:
    return f"{prefix}_{value.hex}"


def _nullable_input_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _unique_values(values: Any) -> list[Any]:
    result: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result


def _digest_entry(entry: ContextEntry) -> dict[str, Any]:
    return {
        "knowledgeEvidenceId": str(entry.knowledge_evidence_id),
        "nodeId": str(entry.node_id) if entry.node_id is not None else None,
        "edgeId": str(entry.edge_id) if entry.edge_id is not None else None,
        "sourceRecordId": str(entry.source_record_id),
        "sourceKind": entry.source_kind,
        "sourceEntityType": entry.source_entity_type,
        "sourceEntityId": str(entry.source_entity_id),
        "sourceRevision": entry.source_revision,
        "sourceContentHash": entry.source_content_hash,
        "sourcePermalink": entry.source_permalink,
        "rank": entry.rank,
        "reason": entry.reason,
        "nodeSnapshot": entry.node_snapshot,
        "edgeSnapshot": entry.edge_snapshot,
    }


def _integer_property(properties: dict[str, Any], key: str) -> int | None:
    value = properties.get(key)
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _timestamp_micros(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000)


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _nonblank(value: str, field: str, *, maximum: int) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field} cannot be blank")
    if len(clean) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return clean
