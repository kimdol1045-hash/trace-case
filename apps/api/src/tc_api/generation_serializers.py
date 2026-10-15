"""Generation job wire serializers and redacted audit snapshots."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import ValidationError

from tc_api.generation_models import (
    ActiveKnowledgeCollectionResource,
    ActiveKnowledgeComparisonResource,
    ApplyComparisonResource,
    ApplyReceiptHeaderResource,
    CandidateApplyLinkageResource,
    CandidateReferenceEdgeResource,
    ComparisonJobResource,
    ComparisonReadPolicyResource,
    ComparisonSourceReferenceResource,
    ExistingCaseCollectionResource,
    ExistingCaseComparisonResource,
    ExistingCaseContentResource,
    GenerationComparisonResponse,
    GenerationCategoryCandidateResource,
    GenerationApplyReceiptResource,
    GenerationContextItemResource,
    GenerationJobDetailResource,
    GenerationJobResource,
    GenerationReconstructionResponse,
    GenerationResultComparisonResource,
    GeneratedCandidateComparisonResource,
    GeneratedTestCaseResource,
    HistoricalExistingCaseContentResource,
    HistoricalSnapshotResource,
    HistoricalTextResource,
    ReferenceEdgeCollectionResource,
    ResolvedReferenceSourceKind,
    ResolvedCandidateReferenceTargetResource,
    SlackRevisionEvidenceResource,
    SlackSourceComparisonResource,
    SlackStructuredFieldResource,
    SlackThreadEvidenceResource,
    SupplementalEvidenceCollectionResource,
    SupplementalEvidenceComparisonResource,
    UnavailableCandidateReferenceTargetResource,
)
from tc_api.ids import encode_identifier
from tc_backend.db.models import GenerationApply, GenerationContext, GenerationJob
from tc_backend.generation.application import (
    GenerationStoredResultInvalid,
    canonical_sha256,
)
from tc_backend.generation.errors import GenerationValidationError
from tc_backend.generation.models import (
    ContextEvidence,
    GenerationContext as ValidationContext,
)
from tc_backend.generation.reconstruction import GenerationReconstruction
from tc_backend.generation.validator import GenerationResponseValidator
from tc_backend.repositories.generation_repository import (
    PersistedGenerationComparison,
    PersistedGenerationContext,
)


def generation_job_resource(job: GenerationJob) -> GenerationJobResource:
    if job.context_digest is None or job.context_sealed_at is None:
        raise ValueError("generation job context is not sealed")
    failure = None
    if job.error_code is not None:
        if (
            job.error is None
            or job.error_retryable is None
            or job.error_attempts is None
            or job.completed_at is None
        ):
            raise ValueError("generation failure metadata is incomplete")
        failure = {
            "code": job.error_code,
            "message": job.error,
            "retryable": job.error_retryable,
            "attempts": job.error_attempts,
            "occurredAt": job.completed_at,
        }
    return GenerationJobResource.model_validate(
        {
            "id": encode_identifier("generation_job", job.id),
            "slackWorkItemId": encode_identifier(
                "slack_work_item", job.slack_work_item_id
            ),
            "status": job.status,
            "lineage": {
                "rootJobId": encode_identifier("generation_job", job.root_job_id),
                "retryOfJobId": (
                    encode_identifier("generation_job", job.retry_of_job_id)
                    if job.retry_of_job_id is not None
                    else None
                ),
                "attemptNumber": job.attempt_number,
            },
            "context": {
                "inputSchemaVersion": "1.0.0",
                "outputSchemaVersion": job.schema_version,
                "sourceVersion": job.source_version,
                "generationVersion": job.generation_version,
                "ontologySchemaId": encode_identifier(
                    "ontology_schema", job.ontology_schema_id
                ),
                "promptVersion": job.prompt_version,
                "itemCount": job.context_item_count,
                "digest": job.context_digest,
                "sealedAt": job.context_sealed_at,
            },
            "failure": failure,
            "result": job.result,
            "createdAt": job.created_at,
            "startedAt": job.started_at,
            "completedAt": job.completed_at,
            "updatedAt": job.updated_at,
        }
    )


def generation_reconstruction(
    reconstructed: GenerationReconstruction,
) -> GenerationReconstructionResponse:
    job = reconstructed.job
    if job.context_digest is None or job.context_sealed_at is None:
        raise ValueError("generation job context is not sealed")
    input_digest = (
        {
            "algorithm": "SHA-256",
            "canonicalization": "RFC8785",
            "value": reconstructed.input_digest.value,
            "canonicalByteLength": reconstructed.input_digest.canonical_byte_length,
        }
        if reconstructed.input_digest is not None
        else None
    )
    return GenerationReconstructionResponse.model_validate(
        {
            "reconstructionSchemaVersion": "1.0.0",
            "jobId": encode_identifier("generation_job", job.id),
            "availability": reconstructed.availability,
            "reason": (
                reconstructed.reason.value if reconstructed.reason is not None else None
            ),
            "recordedInputSchemaVersion": reconstructed.recorded_input_schema_version,
            "input": reconstructed.generation_input,
            "provenance": {
                "sealedContextDigest": job.context_digest,
                "sealedAt": job.context_sealed_at,
                "inputDigest": input_digest,
            },
            "policy": {
                "sourceBasis": "SEALED_GENERATION_INPUT",
                "readOnly": True,
                "sealedSnapshotsOnly": True,
                "liveSlackConsulted": False,
                "liveKnowledgeConsulted": False,
                "liveCasesConsulted": False,
                "liveIssuesConsulted": False,
                "liveCategoriesConsulted": False,
                "providerCallAttempted": False,
                "mutationPerformed": False,
                "arrayOrderPreserved": True,
                "nullEmptyDistinctionsPreserved": True,
            },
        }
    )


def generation_context_item(row: GenerationContext) -> GenerationContextItemResource:
    is_node = row.node_id is not None
    subject_id = row.node_id if is_node else row.edge_id
    snapshot = row.node_snapshot if is_node else row.edge_snapshot
    if subject_id is None or snapshot is None:
        raise ValueError("generation context subject snapshot is incomplete")
    return GenerationContextItemResource.model_validate(
        {
            "id": encode_identifier("generation_context", row.id),
            "knowledgeEvidenceId": encode_identifier(
                "knowledge_evidence", row.knowledge_evidence_id
            ),
            "subjectType": "NODE" if is_node else "EDGE",
            "subjectId": encode_identifier(
                "knowledge_node" if is_node else "knowledge_edge", subject_id
            ),
            "source": {
                "kind": row.source_kind,
                "entityType": row.source_entity_type,
                "entityId": encode_identifier(
                    row.source_entity_type.lower(), row.source_entity_id
                ),
                "revision": row.source_revision,
                "contentHash": row.source_content_hash,
                "permalink": row.source_permalink,
            },
            "rank": row.rank,
            "reason": row.reason,
            "snapshot": snapshot,
        }
    )


def generation_job_detail(
    persisted: PersistedGenerationContext,
) -> GenerationJobDetailResource:
    return GenerationJobDetailResource(
        job=generation_job_resource(persisted.job),
        context_items=[generation_context_item(row) for row in persisted.entries],
    )


def generation_audit_snapshot(job: GenerationJob) -> dict[str, object]:
    """Keep audits observable without storing context bodies or provider secrets."""

    return generation_job_resource(job).model_dump(mode="json", by_alias=True)


def generation_apply_receipt(row: GenerationApply) -> GenerationApplyReceiptResource:
    """Validate the immutable stored receipt at every API serialization boundary."""

    return GenerationApplyReceiptResource.model_validate(row.receipt)


def generation_comparison(
    persisted: PersistedGenerationComparison,
) -> GenerationComparisonResponse:
    """Project a comparison exclusively from sealed job, result, and receipt rows."""

    job = persisted.job
    references_by_node = _references_by_node(persisted.entries)
    root_rows = _slack_root_rows(persisted.entries, job.slack_work_item_id)
    root_snapshot = root_rows[0].node_snapshot if root_rows else None
    root_references = [_source_reference(row) for row in root_rows]

    receipt, outcomes_by_index = _comparison_apply(persisted)
    generation, reference_edges = _comparison_generation(
        job,
        references_by_node=references_by_node,
        source_kinds_by_node=_reference_source_kinds(persisted.entries),
        outcomes_by_index=outcomes_by_index,
        apply_id=receipt.id if receipt is not None else None,
    )
    return GenerationComparisonResponse(
        comparison_schema_version="1.0.0",
        job=_comparison_job(job),
        policy=ComparisonReadPolicyResource(
            source_basis="SEALED_GENERATION_JOB",
            read_only=True,
            live_source_substitution=False,
            generation_input_reconstructed=False,
            provider_call_attempted=False,
            mutation_performed=False,
        ),
        slack_source=_slack_comparison(
            job,
            root_snapshot=root_snapshot,
            root_rows=root_rows,
            root_references=root_references,
        ),
        active_knowledge=_active_knowledge(persisted.entries),
        existing_cases=_existing_cases(persisted.entries),
        supplemental_evidence=_supplemental_evidence(
            persisted.entries,
            slack_work_item_id=job.slack_work_item_id,
        ),
        generation=generation,
        reference_edges=reference_edges,
        apply=(
            ApplyComparisonResource(state="APPLIED", receipt=receipt)
            if receipt is not None
            else ApplyComparisonResource(state="NOT_APPLIED", receipt=None)
        ),
    )


def _comparison_job(job: GenerationJob) -> ComparisonJobResource:
    resource = generation_job_resource(job)
    return ComparisonJobResource(
        id=resource.id,
        slack_work_item_id=resource.slack_work_item_id,
        status=resource.status,
        lineage=resource.lineage,
        context=resource.context,
        created_at=resource.created_at,
        started_at=resource.started_at,
        completed_at=resource.completed_at,
    )


def _source_reference(row: GenerationContext) -> ComparisonSourceReferenceResource:
    is_node = row.node_id is not None
    subject_id = row.node_id if is_node else row.edge_id
    if subject_id is None:
        raise ValueError("generation context subject is incomplete")
    return ComparisonSourceReferenceResource(
        context_item_id=encode_identifier("generation_context", row.id),
        context_rank=row.rank,
        knowledge_evidence_id=encode_identifier(
            "knowledge_evidence", row.knowledge_evidence_id
        ),
        subject_type="NODE" if is_node else "EDGE",
        subject_id=encode_identifier(
            "knowledge_node" if is_node else "knowledge_edge", subject_id
        ),
        source_kind=row.source_kind,  # type: ignore[arg-type]
        source_entity_type=row.source_entity_type,
        source_entity_id=encode_identifier(
            row.source_entity_type.lower(), row.source_entity_id
        ),
        source_revision=row.source_revision,
        content_hash=row.source_content_hash,
        permalink=(
            _historical_text(row.source_permalink, sealed=True)
            if row.source_permalink is not None
            else HistoricalTextResource(
                state="EMPTY", value=None, reason="SEALED_EMPTY"
            )
        ),
    )


def _references_by_node(
    entries: tuple[GenerationContext, ...],
) -> dict[str, list[ComparisonSourceReferenceResource]]:
    grouped: dict[str, list[ComparisonSourceReferenceResource]] = {}
    for row in entries:
        if row.node_id is None:
            continue
        node_id = encode_identifier("knowledge_node", row.node_id)
        grouped.setdefault(node_id, []).append(_source_reference(row))
    return grouped


def _slack_root_rows(
    entries: tuple[GenerationContext, ...], slack_work_item_id: uuid.UUID
) -> tuple[GenerationContext, ...]:
    rows: list[GenerationContext] = []
    for row in entries:
        snapshot = row.node_snapshot
        if (
            snapshot is not None
            and snapshot.get("nodeType") == "WORK_ITEM"
            and snapshot.get("entityType") == "SLACK_WORK_ITEM"
            and snapshot.get("entityId") == str(slack_work_item_id)
        ):
            rows.append(row)
    return tuple(rows)


def _slack_comparison(
    job: GenerationJob,
    *,
    root_snapshot: dict[str, Any] | None,
    root_rows: tuple[GenerationContext, ...],
    root_references: list[ComparisonSourceReferenceResource],
) -> SlackSourceComparisonResource:
    properties = _snapshot_properties(root_snapshot)
    structured = properties.get("structuredFields")
    structured = structured if isinstance(structured, dict) else properties
    field_keys = (
        ("DEVELOPMENT_ITEM", "developmentItem"),
        ("REQUESTED_MENU", "requestedMenu"),
        ("FEATURE_DESCRIPTION", "featureDescription"),
        ("REQUEST_DETAILS", "requestDetails"),
    )
    fields = []
    for field, key in field_keys:
        sealed = key in structured
        value = _historical_text(structured.get(key), sealed=sealed)
        fields.append(
            SlackStructuredFieldResource(
                field=field,  # type: ignore[arg-type]
                value=value,
                source_references=root_references if sealed else [],
            )
        )

    permalink = _historical_property(properties, "permalink")
    if permalink.state == "MISSING_SOURCE" and root_rows:
        stored_permalink = root_rows[0].source_permalink
        permalink = (
            _historical_text(stored_permalink, sealed=True)
            if stored_permalink is not None
            else HistoricalTextResource(
                state="EMPTY", value=None, reason="SEALED_EMPTY"
            )
        )
    return SlackSourceComparisonResource(
        slack_work_item_id=encode_identifier("slack_work_item", job.slack_work_item_id),
        source_version=job.source_version,
        workspace_id=_historical_property(properties, "workspaceId"),
        channel_id=_historical_property(properties, "channelId"),
        message_ts=_historical_property(properties, "messageTs"),
        thread_ts=_historical_property(properties, "threadTs"),
        permalink=permalink,
        fields=fields,
        thread_evidence=_slack_thread_evidence(
            properties,
            root_rows=root_rows,
        ),
    )


def _slack_thread_evidence(
    properties: dict[str, Any],
    *,
    root_rows: tuple[GenerationContext, ...],
) -> SlackThreadEvidenceResource:
    key = next(
        (
            name
            for name in ("threadEvidence", "slackThreadEvidence")
            if name in properties
        ),
        None,
    )
    if key is None:
        return SlackThreadEvidenceResource(
            availability="MISSING_SOURCE", items=[], reason="NOT_SEALED"
        )
    raw_items = properties[key]
    if raw_items is None or raw_items == []:
        return SlackThreadEvidenceResource(
            availability="EMPTY", items=[], reason="SEALED_EMPTY"
        )
    if not isinstance(raw_items, list):
        return SlackThreadEvidenceResource(
            availability="MISSING_SOURCE", items=[], reason="SOURCE_INCOMPLETE"
        )
    items: list[SlackRevisionEvidenceResource] = []
    try:
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                raise ValueError("Slack thread revision is not an object")
            source_revision_id = _sealed_identifier(
                raw.get("sourceRevisionId"), "slack_revision"
            )
            message_id = _sealed_identifier(raw.get("messageId"), "slack_message")
            matching_rows = tuple(
                row
                for row in root_rows
                if _identifier_uuid(source_revision_id) == row.source_entity_id
            )
            references = (
                [_source_reference(row) for row in matching_rows]
                if matching_rows
                else []
            )
            if not references:
                raise ValueError("Slack revision cannot be tied to sealed context")
            operation = raw.get("operation")
            if operation not in {"CREATE", "EDIT", "DELETE"}:
                raise ValueError("Slack revision operation is invalid")
            body = (
                HistoricalTextResource(state="EMPTY", value=None, reason="SEALED_EMPTY")
                if operation == "DELETE"
                else _historical_text(raw.get("body"), sealed="body" in raw)
            )
            items.append(
                SlackRevisionEvidenceResource(
                    index=index,
                    source_revision_id=source_revision_id,
                    message_id=message_id,
                    message_ts=raw["messageTs"],
                    revision_ts=raw["revisionTs"],
                    role=raw["role"],
                    operation=cast(Literal["CREATE", "EDIT", "DELETE"], operation),
                    author_id=_historical_text(
                        raw.get("authorId"), sealed="authorId" in raw
                    ),
                    body=body,
                    permalink=_historical_text(
                        raw.get("permalink"), sealed="permalink" in raw
                    ),
                    recorded_at=raw.get("recordedAt"),
                    source_references=references,
                )
            )
    except (KeyError, TypeError, ValueError, ValidationError):
        return SlackThreadEvidenceResource(
            availability="MISSING_SOURCE", items=[], reason="SOURCE_INCOMPLETE"
        )
    return SlackThreadEvidenceResource(
        availability="AVAILABLE", items=items, reason=None
    )


def _active_knowledge(
    entries: tuple[GenerationContext, ...],
) -> ActiveKnowledgeCollectionResource:
    items: list[ActiveKnowledgeComparisonResource] = []
    for rows in _node_groups(entries).values():
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
        items.append(
            ActiveKnowledgeComparisonResource(
                evidence_node_id=encode_identifier("knowledge_node", row.node_id),
                node_type=node_type,
                status="ACTIVE",
                active_at_seal=True,
                content=_historical_property(properties, "content"),
                valid_from=snapshot.get("validFrom"),
                valid_to=None,
                selection_reason=row.reason,
                source_references=[_source_reference(item) for item in rows],
            )
        )
    if not items:
        return ActiveKnowledgeCollectionResource(
            availability="EMPTY", items=[], reason="SEALED_EMPTY"
        )
    return ActiveKnowledgeCollectionResource(
        availability="AVAILABLE", items=items, reason=None
    )


def _existing_cases(
    entries: tuple[GenerationContext, ...],
) -> ExistingCaseCollectionResource:
    items: list[ExistingCaseComparisonResource] = []
    for rows in _node_groups(entries).values():
        row = rows[0]
        snapshot = row.node_snapshot or {}
        if (
            snapshot.get("nodeType") != "TEST_CASE"
            or snapshot.get("status") != "ACTIVE"
            or row.node_id is None
        ):
            continue
        properties = _snapshot_properties(snapshot)
        test_case_id = _sealed_identifier(snapshot.get("entityId"), "case")
        version_value = properties.get("currentVersionId")
        if version_value is None:
            version_value = properties.get("testCaseVersionId")
        try:
            test_case_version_id = _sealed_identifier(version_value, "case_version")
        except ValueError:
            continue
        items.append(
            ExistingCaseComparisonResource(
                evidence_node_id=encode_identifier("knowledge_node", row.node_id),
                test_case_id=test_case_id,
                test_case_version_id=test_case_version_id,
                case_key=_historical_property(properties, "caseKey"),
                status="ACTIVE",
                content=_existing_case_content(properties),
                selection_reason=row.reason,
                source_references=[_source_reference(item) for item in rows],
            )
        )
    if not items:
        return ExistingCaseCollectionResource(
            availability="EMPTY", items=[], reason="SEALED_EMPTY"
        )
    return ExistingCaseCollectionResource(
        availability="AVAILABLE", items=items, reason=None
    )


def _existing_case_content(
    properties: dict[str, Any],
) -> HistoricalExistingCaseContentResource:
    raw = next(
        (
            properties[key]
            for key in ("caseContent", "currentVersion")
            if key in properties
        ),
        None,
    )
    if not isinstance(raw, dict):
        return HistoricalExistingCaseContentResource(
            state="MISSING_SOURCE", value=None, reason="NOT_SEALED"
        )
    try:
        content = ExistingCaseContentResource.model_validate(raw)
    except ValidationError:
        return HistoricalExistingCaseContentResource(
            state="MISSING_SOURCE", value=None, reason="SOURCE_INCOMPLETE"
        )
    return HistoricalExistingCaseContentResource(
        state="PRESENT", value=content, reason=None
    )


def _supplemental_evidence(
    entries: tuple[GenerationContext, ...],
    *,
    slack_work_item_id: uuid.UUID,
) -> SupplementalEvidenceCollectionResource:
    items: list[SupplementalEvidenceComparisonResource] = []
    for rows in _subject_groups(entries).values():
        row = rows[0]
        snapshot = row.node_snapshot or row.edge_snapshot or {}
        node_type = snapshot.get("nodeType")
        if _is_primary_comparison_node(snapshot, slack_work_item_id):
            continue
        if row.node_id is not None:
            evidence_id = encode_identifier("knowledge_node", row.node_id)
        elif row.edge_id is not None:
            evidence_id = encode_identifier("knowledge_edge", row.edge_id)
        else:  # pragma: no cover - database constraint
            continue
        properties = _snapshot_properties(snapshot)
        title_value = (
            snapshot.get("label")
            or properties.get("title")
            or properties.get("summary")
            or snapshot.get("predicate")
        )
        kind = _supplemental_kind(node_type, row.source_entity_type)
        items.append(
            SupplementalEvidenceComparisonResource(
                evidence_node_id=evidence_id,
                kind=kind,
                title=_historical_text(title_value, sealed=title_value is not None),
                snapshot=HistoricalSnapshotResource(
                    state="PRESENT", value=snapshot, reason=None
                ),
                selection_reason=row.reason,
                source_references=[_source_reference(item) for item in rows],
            )
        )
    if not items:
        return SupplementalEvidenceCollectionResource(
            availability="EMPTY", items=[], reason="SEALED_EMPTY"
        )
    return SupplementalEvidenceCollectionResource(
        availability="AVAILABLE", items=items, reason=None
    )


def _comparison_generation(
    job: GenerationJob,
    *,
    references_by_node: dict[str, list[ComparisonSourceReferenceResource]],
    source_kinds_by_node: dict[str, ResolvedReferenceSourceKind],
    outcomes_by_index: dict[int, Any],
    apply_id: str | None,
) -> tuple[GenerationResultComparisonResource, ReferenceEdgeCollectionResource]:
    if job.status != "SUCCEEDED":
        if apply_id is not None:
            raise GenerationStoredResultInvalid(job.id, "RECEIPT", "STATE_MISMATCH")
        return (
            GenerationResultComparisonResource(
                availability="EMPTY",
                output_schema_version="1.0.0",
                result_digest=None,
                category_candidate=None,
                candidates=[],
                reason="JOB_NOT_SUCCEEDED",
            ),
            ReferenceEdgeCollectionResource(
                availability="EMPTY",
                items=[],
                reason="NO_GENERATED_CANDIDATES",
            ),
        )
    if job.result is None:
        return (
            GenerationResultComparisonResource(
                availability="MISSING_SOURCE",
                output_schema_version="1.0.0",
                result_digest=None,
                category_candidate=None,
                candidates=[],
                reason="RESULT_MISSING",
            ),
            ReferenceEdgeCollectionResource(
                availability="MISSING_SOURCE", items=[], reason="RESULT_MISSING"
            ),
        )
    cases, category = _validated_stored_result(job)
    if apply_id is not None and set(outcomes_by_index) != set(range(len(cases))):
        raise GenerationStoredResultInvalid(job.id, "RECEIPT", "OUTCOME_INDEX_MISMATCH")
    candidates: list[GeneratedCandidateComparisonResource] = []
    edges: list[CandidateReferenceEdgeResource] = []
    for candidate_index, candidate in enumerate(cases):
        outcome = outcomes_by_index.get(candidate_index)
        candidate_resource = GeneratedTestCaseResource.model_validate(candidate)
        candidates.append(
            GeneratedCandidateComparisonResource(
                index=candidate_index,
                candidate=candidate_resource,
                apply=(
                    CandidateApplyLinkageResource(
                        state="APPLIED", apply_id=apply_id, outcome=outcome
                    )
                    if apply_id is not None
                    else CandidateApplyLinkageResource(
                        state="NOT_APPLIED", apply_id=None, outcome=None
                    )
                ),
            )
        )
        evidence_ids = candidate["evidenceNodeIds"]
        if not isinstance(evidence_ids, list):  # pragma: no cover - trusted validator
            raise GenerationStoredResultInvalid(job.id, "SCHEMA", "SCHEMA_INVALID")
        for evidence_index, evidence_node_id in enumerate(evidence_ids):
            if not isinstance(evidence_node_id, str):  # pragma: no cover
                raise GenerationStoredResultInvalid(job.id, "SCHEMA", "SCHEMA_INVALID")
            context_references = references_by_node.get(evidence_node_id, [])
            source_kind = source_kinds_by_node.get(evidence_node_id, "OTHER_SEALED")
            target = (
                ResolvedCandidateReferenceTargetResource(
                    state="RESOLVED",
                    source_kind=source_kind,
                    source_id=evidence_node_id,
                    context_references=context_references,
                    reason=None,
                )
                if context_references
                else UnavailableCandidateReferenceTargetResource(
                    state="MISSING_SOURCE",
                    source_kind="UNKNOWN",
                    source_id=evidence_node_id,
                    context_references=[],
                    reason="UNRESOLVED_REFERENCE",
                )
            )
            edges.append(
                CandidateReferenceEdgeResource(
                    candidate_index=candidate_index,
                    evidence_index=evidence_index,
                    evidence_node_id=evidence_node_id,
                    relation="SUPPORTED_BY",
                    target=target,
                )
            )
    return (
        GenerationResultComparisonResource(
            availability="AVAILABLE",
            output_schema_version="1.0.0",
            result_digest=canonical_sha256(job.result),
            category_candidate=GenerationCategoryCandidateResource.model_validate(
                category
            ),
            candidates=candidates,
            reason=None,
        ),
        ReferenceEdgeCollectionResource(
            availability="AVAILABLE", items=edges, reason=None
        ),
    )


def _validated_stored_result(
    job: GenerationJob,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = job.result
    if not isinstance(payload, Mapping):
        raise GenerationStoredResultInvalid(job.id, "SCHEMA", "SCHEMA_INVALID")
    raw_cases = payload.get("cases")
    raw_category = payload.get("categoryCandidate")
    if not isinstance(raw_cases, list) or not isinstance(raw_category, dict):
        raise GenerationStoredResultInvalid(job.id, "SCHEMA", "SCHEMA_INVALID")
    evidence_ids = {
        value
        for candidate in raw_cases
        if isinstance(candidate, dict)
        for value in candidate.get("evidenceNodeIds", [])
        if isinstance(value, str)
    }
    category_ids = {
        value
        for value in [
            raw_category.get("categoryId"),
            *[
                alternative.get("categoryId")
                for alternative in raw_category.get("alternatives", [])
                if isinstance(alternative, dict)
            ],
        ]
        if isinstance(value, str)
    }
    validation_context = ValidationContext(
        evidence=tuple(
            ContextEvidence(
                node_id=value,
                status="ACTIVE",
                content=json.dumps(payload, ensure_ascii=False),
                active=True,
            )
            for value in sorted(evidence_ids)
        ),
        category_ids=frozenset(category_ids),
    )
    try:
        GenerationResponseValidator().validate(payload, validation_context)
    except (GenerationValidationError, TypeError, ValueError) as error:
        raise GenerationStoredResultInvalid(
            job.id, "SCHEMA", "SCHEMA_INVALID"
        ) from error
    return raw_cases, raw_category


def _comparison_apply(
    persisted: PersistedGenerationComparison,
) -> tuple[ApplyReceiptHeaderResource | None, dict[int, Any]]:
    row = persisted.apply
    if row is None:
        if persisted.apply_outcomes:
            raise GenerationStoredResultInvalid(
                persisted.job.id, "RECEIPT", "ORPHAN_OUTCOME"
            )
        return None, {}
    try:
        receipt = GenerationApplyReceiptResource.model_validate(row.receipt)
    except ValidationError as error:
        raise GenerationStoredResultInvalid(
            persisted.job.id, "RECEIPT", "RECEIPT_INVALID"
        ) from error
    raw_cases = row.receipt.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) != len(
        persisted.apply_outcomes
    ):
        raise GenerationStoredResultInvalid(
            persisted.job.id, "RECEIPT", "OUTCOME_COUNT_MISMATCH"
        )
    outcomes: dict[int, Any] = {}
    for index, (outcome_row, raw_outcome, outcome) in enumerate(
        zip(persisted.apply_outcomes, raw_cases, receipt.cases, strict=True)
    ):
        if (
            outcome_row.generated_case_index != index
            or outcome_row.snapshot != raw_outcome
            or outcome.generated_case_index != index
        ):
            raise GenerationStoredResultInvalid(
                persisted.job.id, "RECEIPT", "OUTCOME_INDEX_MISMATCH"
            )
        outcomes[index] = outcome
    expected_job_id = encode_identifier("generation_job", persisted.job.id)
    if (
        receipt.id != encode_identifier("generation_apply", row.id)
        or receipt.generation_job_id != expected_job_id
        or receipt.context_digest != row.context_digest
        or receipt.result_digest != row.result_digest
        or receipt.placement_digest != row.placement_digest
        or receipt.audit_id != encode_identifier("audit", row.audit_id)
        or receipt.applied_at != row.applied_at
        or row.context_digest != persisted.job.context_digest
        or (
            persisted.job.result is not None
            and row.result_digest != canonical_sha256(persisted.job.result)
        )
    ):
        raise GenerationStoredResultInvalid(
            persisted.job.id, "RECEIPT", "RECEIPT_MISMATCH"
        )
    return (
        ApplyReceiptHeaderResource(
            id=receipt.id,
            generation_job_id=receipt.generation_job_id,
            context_digest=receipt.context_digest,
            result_digest=receipt.result_digest,
            placement_digest=receipt.placement_digest,
            transaction_status="COMMITTED",
            parent=receipt.parent,
            counts=receipt.counts,
            audit_id=receipt.audit_id,
            applied_at=receipt.applied_at,
        ),
        outcomes,
    )


def _node_groups(
    entries: tuple[GenerationContext, ...],
) -> dict[uuid.UUID, list[GenerationContext]]:
    grouped: dict[uuid.UUID, list[GenerationContext]] = {}
    for row in entries:
        if row.node_id is not None:
            grouped.setdefault(row.node_id, []).append(row)
    return grouped


def _subject_groups(
    entries: tuple[GenerationContext, ...],
) -> dict[tuple[str, uuid.UUID], list[GenerationContext]]:
    grouped: dict[tuple[str, uuid.UUID], list[GenerationContext]] = {}
    for row in entries:
        identifier = row.node_id if row.node_id is not None else row.edge_id
        if identifier is None:
            continue
        kind = "NODE" if row.node_id is not None else "EDGE"
        grouped.setdefault((kind, identifier), []).append(row)
    return grouped


def _snapshot_properties(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if snapshot is None:
        return {}
    properties = snapshot.get("properties")
    return properties if isinstance(properties, dict) else {}


def _historical_property(
    properties: dict[str, Any], key: str
) -> HistoricalTextResource:
    return _historical_text(properties.get(key), sealed=key in properties)


def _historical_text(value: Any, *, sealed: bool) -> HistoricalTextResource:
    if not sealed:
        return HistoricalTextResource(
            state="MISSING_SOURCE", value=None, reason="NOT_SEALED"
        )
    if isinstance(value, dict):
        try:
            return HistoricalTextResource.model_validate(value)
        except ValidationError:
            return HistoricalTextResource(
                state="MISSING_SOURCE", value=None, reason="SOURCE_INCOMPLETE"
            )
    if value is None or (isinstance(value, str) and not value.strip()):
        return HistoricalTextResource(state="EMPTY", value=None, reason="SEALED_EMPTY")
    if not isinstance(value, str):
        return HistoricalTextResource(
            state="MISSING_SOURCE", value=None, reason="SOURCE_INCOMPLETE"
        )
    return HistoricalTextResource(state="PRESENT", value=value, reason=None)


def _sealed_identifier(value: Any, prefix: str) -> str:
    if isinstance(value, uuid.UUID):
        return encode_identifier(prefix, value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("sealed identifier is missing")
    if value.startswith(f"{prefix}_"):
        return value
    try:
        return encode_identifier(prefix, uuid.UUID(value))
    except ValueError:
        if value[0].isalpha() and all(
            character.isalnum() or character in "_-" for character in value
        ):
            return value
        raise ValueError("sealed identifier is malformed") from None


def _identifier_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value.rsplit("_", 1)[-1])
    except ValueError:
        return None


def _is_primary_comparison_node(
    snapshot: dict[str, Any], slack_work_item_id: uuid.UUID
) -> bool:
    node_type = snapshot.get("nodeType")
    if (
        node_type == "WORK_ITEM"
        and snapshot.get("entityType") == "SLACK_WORK_ITEM"
        and snapshot.get("entityId") == str(slack_work_item_id)
    ):
        return True
    if (
        node_type in {"REQUIREMENT", "DECISION", "CONSTRAINT"}
        and snapshot.get("status") == "ACTIVE"
        and snapshot.get("validTo") is None
    ):
        return True
    return node_type == "TEST_CASE" and snapshot.get("status") == "ACTIVE"


def _supplemental_kind(
    node_type: Any, source_entity_type: str
) -> Literal["RISK_EXECUTION", "OPEN_ISSUE", "EXPLICIT_SOURCE", "OTHER_SEALED"]:
    if node_type == "EXECUTION":
        return "RISK_EXECUTION"
    if node_type == "ISSUE":
        return "OPEN_ISSUE"
    if source_entity_type in {"EXTRA_SOURCE", "EXPLICIT_SOURCE"}:
        return "EXPLICIT_SOURCE"
    return "OTHER_SEALED"


def _reference_source_kinds(
    entries: tuple[GenerationContext, ...],
) -> dict[str, ResolvedReferenceSourceKind]:
    kinds: dict[str, ResolvedReferenceSourceKind] = {}
    for node_id, rows in _node_groups(entries).items():
        row = rows[0]
        snapshot = row.node_snapshot or {}
        node_type = snapshot.get("nodeType")
        kind: ResolvedReferenceSourceKind
        if node_type == "WORK_ITEM" and snapshot.get("entityType") == "SLACK_WORK_ITEM":
            kind = "SLACK_WORK_ITEM"
        elif node_type in {"REQUIREMENT", "DECISION", "CONSTRAINT"}:
            kind = "ACTIVE_KNOWLEDGE"
        elif node_type == "TEST_CASE":
            kind = "EXISTING_CASE"
        else:
            kind = _supplemental_kind(node_type, row.source_entity_type)
        kinds[encode_identifier("knowledge_node", node_id)] = kind
    return kinds
