"""Database/domain to wire-contract serialization helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import Request

from tc_api.ids import encode_identifier, encode_resource_identifier
from tc_api.models import (
    AuditEntryResponse,
    AuditSourceValue,
    AuditSourceResponse,
    FolderMutationResponse,
    FolderNodeResponse,
    FolderOperation,
    GenerationManagementActorResponse,
    GenerationManagementManagedResponse,
    GenerationManagementNeverManagedResponse,
    GenerationManagementReleasedResponse,
    GenerationManagementResponse,
    PathSegmentResponse,
    WorkspaceSettingsResponse,
    CopiedFromResponse,
    SourceMetadataResponse,
    TestCaseListItemResponse,
    TestCaseResourceResponse,
    TestCaseVersionResponse,
    TestStepResponse,
    WorkItemResourceResponse,
)
from tc_backend.db.audit import AuditContext
from tc_backend.db.folders import FolderMutationResult, FolderTreeNode
from tc_backend.db.cases import CaseAggregate, VersionSnapshot
from tc_backend.db.models import (
    AuditLog,
    AuditSourceType,
    Folder,
    TestCase,
    TestCaseVersion,
    WorkItem,
    WorkspaceSettings,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


def utc_now() -> datetime:
    return datetime.now(UTC)


def workspace_response(settings: WorkspaceSettings) -> WorkspaceSettingsResponse:
    return WorkspaceSettingsResponse(
        id=encode_identifier("workspace", settings.id),
        team_name=settings.team_name,
        case_prefix=settings.case_prefix,
        issue_prefix=settings.issue_prefix,
        default_environment=settings.default_environment,
        generation_quiet_minutes=settings.generation_quiet_minutes,
        version=settings.version,
        created_at=settings.created_at,
        updated_at=settings.updated_at,
    )


def folder_node_response(
    node: FolderTreeNode,
    *,
    include_children: bool = True,
) -> FolderNodeResponse:
    return FolderNodeResponse(
        id=encode_identifier("folder", node.id),
        parent_id=(
            encode_identifier("folder", node.parent_id) if node.parent_id is not None else None
        ),
        name=node.name,
        sort_order=node.sort_order,
        depth=node.depth,
        path=[
            PathSegmentResponse(id=encode_identifier("folder", identifier), name=name)
            for identifier, name in node.path
        ],
        direct_test_case_count=node.direct_test_case_count,
        subtree_test_case_count=node.subtree_test_case_count,
        version=node.version,
        deleted_at=node.deleted_at,
        children=(
            [folder_node_response(child) for child in node.children]
            if include_children
            else []
        ),
    )


def create_folder_mutation_response(
    folder: Folder,
    *,
    operation: FolderOperation,
    audit_id: uuid.UUID,
) -> FolderMutationResponse:
    return FolderMutationResponse(
        operation=operation,
        root_folder_id=encode_identifier("folder", folder.id),
        affected_folder_ids=[encode_identifier("folder", folder.id)],
        root_version=folder.version,
        audit_id=encode_identifier("audit", audit_id),
    )


def folder_mutation_response(
    result: FolderMutationResult,
    *,
    operation: FolderOperation,
) -> FolderMutationResponse:
    return FolderMutationResponse(
        operation=operation,
        root_folder_id=encode_identifier("folder", result.root_folder_id),
        affected_folder_ids=[
            encode_identifier("folder", identifier) for identifier in result.affected_folder_ids
        ],
        root_version=result.root_version,
        audit_id=encode_identifier("audit", result.audit_id),
        deletion_batch_id=(
            encode_identifier("folder_delete", result.deletion_batch_id)
            if result.deletion_batch_id is not None
            else None
        ),
    )


def audit_context(
    request: Request,
    *,
    idempotency_key: str | None,
    reason: str | None = None,
) -> AuditContext:
    return AuditContext(
        source_type=AuditSourceType.API,
        actor_label=_optional_header(request, "X-Actor-Label", 200),
        external_actor_id=_optional_header(request, "X-External-Actor-Id", 200),
        reason=reason,
        trace_id=request.state.trace_id,
        idempotency_key=idempotency_key,
    )


def audit_entry_response(
    row: AuditLog,
    *,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
) -> AuditEntryResponse:
    return AuditEntryResponse(
        id=encode_identifier("audit", row.id),
        workspace_id=encode_identifier("workspace", row.workspace_id),
        entity_type=row.entity_type,
        entity_id=encode_resource_identifier(row.entity_type, row.entity_id),
        action=row.action,
        before=before,
        after=after,
        source=AuditSourceResponse(
            type=cast(AuditSourceValue, row.source_type),
            actor_label=row.actor_label,
            external_actor_id=row.external_actor_id,
        ),
        reason=row.reason,
        occurred_at=row.occurred_at,
        trace_id=row.trace_id,
        idempotency_key=row.idempotency_key,
    )


def source_metadata_response(snapshot: VersionSnapshot) -> SourceMetadataResponse:
    metadata = dict(snapshot.source_metadata)
    source_type = metadata.pop("type", None)
    if source_type not in {"UI", "API", "SYSTEM", "SLACK", "WORKER"}:
        source_type = {
            "MANUAL": "UI",
            "API": "API",
            "FILE_IMPORT": "SYSTEM",
            "SLACK_AI": "WORKER",
        }.get(snapshot.source_type, "SYSTEM")
    allowed = {
        "actorLabel",
        "externalActorId",
        "slackWorkItemId",
        "generationJobId",
        "sourceRevisionIds",
        "evidenceNodeIds",
        "correlationId",
    }
    return SourceMetadataResponse.model_validate(
        {"type": source_type, **{key: value for key, value in metadata.items() if key in allowed}}
    )


def version_response(
    session: Session,
    snapshot: VersionSnapshot,
) -> TestCaseVersionResponse:
    restored_from = _version_number(session, snapshot.restored_from_version_id)
    return TestCaseVersionResponse(
        id=encode_identifier("case_version", snapshot.id),
        test_case_id=encode_identifier("case", snapshot.test_case_id),
        version=snapshot.version_number,
        summary=snapshot.summary,
        precondition=snapshot.precondition,
        expected_result_mode=cast(Any, snapshot.expected_result_mode),
        case_expected_result=snapshot.expected_result,
        priority=cast(Any, snapshot.priority),
        owner_label=snapshot.assignee_label,
        labels=list(snapshot.tag_names),
        steps=[
            TestStepResponse(
                sequence=step.sequence,
                precondition=step.precondition,
                action=step.action,
                expected_result=step.expected_result,
            )
            for step in snapshot.steps
        ],
        source=source_metadata_response(snapshot),
        change_reason=snapshot.change_reason,
        restored_from_version=restored_from,
        created_at=snapshot.created_at,
    )


def case_resource_response(
    session: Session,
    aggregate: CaseAggregate,
) -> TestCaseResourceResponse:
    row = aggregate.test_case
    copied_from_version_id = aggregate.current_version.copied_from_version_id
    if copied_from_version_id is None:
        copied_from_version_id = session.scalar(
            select(TestCaseVersion.copied_from_version_id)
            .where(
                TestCaseVersion.test_case_id == row.id,
                TestCaseVersion.copied_from_version_id.is_not(None),
            )
            .order_by(TestCaseVersion.version_number)
            .limit(1)
        )
    copied_from = _copied_from_response(session, copied_from_version_id)
    return TestCaseResourceResponse(
        id=encode_identifier("case", row.id),
        key=row.case_key,
        work_item_id=encode_identifier("work_item", row.work_item_id),
        category_id=encode_identifier("folder", row.category_id),
        sort_order=row.sort_order,
        status=cast(Any, row.status),
        resource_version=row.version,
        version=aggregate.current_version.version_number,
        current_version=version_response(session, aggregate.current_version),
        generation_management=_generation_management_response(row),
        copied_from=copied_from,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _generation_management_response(row: TestCase) -> GenerationManagementResponse:
    if row.generation_lineage_id is None:
        return GenerationManagementNeverManagedResponse(managed=False)
    if (
        row.generation_created_outcome_id is None
        or row.generation_baseline_version_id is None
        or row.generation_latest_version_id is None
    ):
        raise RuntimeError("test case has incomplete generation management lineage")
    lineage_id = encode_identifier("generation_lineage", row.generation_lineage_id)
    created_outcome_id = encode_identifier(
        "generation_apply_outcome", row.generation_created_outcome_id
    )
    baseline_version_id = encode_identifier(
        "case_version", row.generation_baseline_version_id
    )
    latest_version_id = encode_identifier("case_version", row.generation_latest_version_id)
    if row.generation_managed:
        return GenerationManagementManagedResponse(
            managed=True,
            lineage_id=lineage_id,
            created_outcome_id=created_outcome_id,
            baseline_generated_version_id=baseline_version_id,
            latest_generated_version_id=latest_version_id,
        )
    if (
        row.generation_released_at is None
        or row.generation_release_reason is None
        or row.generation_release_actor is None
        or row.generation_release_mutation is None
        or row.generation_released_from_version_id is None
    ):
        raise RuntimeError("test case has incomplete generation management release")
    actor = GenerationManagementActorResponse.model_validate(
        row.generation_release_actor
    )
    return GenerationManagementReleasedResponse(
        managed=False,
        lineage_id=lineage_id,
        created_outcome_id=created_outcome_id,
        baseline_generated_version_id=baseline_version_id,
        latest_generated_version_id=latest_version_id,
        released_at=row.generation_released_at,
        release_reason=row.generation_release_reason,
        release_actor=actor,
        release_mutation=cast(Any, row.generation_release_mutation),
        released_from_version_id=encode_identifier(
            "case_version", row.generation_released_from_version_id
        ),
        released_by_version_id=(
            encode_identifier("case_version", row.generation_released_by_version_id)
            if row.generation_released_by_version_id is not None
            else None
        ),
    )


def case_list_item_response(
    aggregate: CaseAggregate,
    work_item: WorkItem,
) -> TestCaseListItemResponse:
    row = aggregate.test_case
    snapshot = aggregate.current_version
    return TestCaseListItemResponse(
        id=encode_identifier("case", row.id),
        key=row.case_key,
        work_item_id=encode_identifier("work_item", row.work_item_id),
        work_item_key=work_item.work_item_key,
        work_item_summary=work_item.summary,
        category_id=encode_identifier("folder", row.category_id),
        sort_order=row.sort_order,
        status=cast(Any, row.status),
        summary=snapshot.summary,
        priority=cast(Any, snapshot.priority),
        owner_label=snapshot.assignee_label,
        labels=list(snapshot.tag_names),
        resource_version=row.version,
        version=snapshot.version_number,
        source=source_metadata_response(snapshot),
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def work_item_response(work_item: WorkItem) -> WorkItemResourceResponse:
    return WorkItemResourceResponse(
        id=encode_identifier("work_item", work_item.id),
        key=work_item.work_item_key,
        category_id=encode_identifier("folder", work_item.category_id),
        summary=work_item.summary,
        description=work_item.description,
        owner_label=work_item.owner_label,
        labels=list(work_item.labels),
        resource_version=work_item.version,
        created_at=work_item.created_at,
        updated_at=work_item.updated_at,
    )


def copied_from_response(session: Session, snapshot: VersionSnapshot) -> CopiedFromResponse:
    response = _copied_from_response(session, snapshot.copied_from_version_id)
    if response is None:
        raise RuntimeError("copied test case is missing its source version")
    return response


def _copied_from_response(
    session: Session,
    version_id: uuid.UUID | None,
) -> CopiedFromResponse | None:
    if version_id is None:
        return None
    source_version, source_case = session.execute(
        select(TestCaseVersion, TestCase)
        .join(TestCase, TestCase.id == TestCaseVersion.test_case_id)
        .where(TestCaseVersion.id == version_id)
    ).one()
    return CopiedFromResponse(
        test_case_id=encode_identifier("case", source_case.id),
        case_key=source_case.case_key,
        version_id=encode_identifier("case_version", source_version.id),
        version=source_version.version_number,
    )


def _version_number(session: Session, version_id: uuid.UUID | None) -> int | None:
    if version_id is None:
        return None
    return session.scalar(
        select(TestCaseVersion.version_number).where(TestCaseVersion.id == version_id)
    )


def _optional_header(request: Request, name: str, maximum: int) -> str | None:
    raw = request.headers.get(name)
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    return value[:maximum]
