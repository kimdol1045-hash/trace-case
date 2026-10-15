"""Sprint 3 cycle snapshots, execution attempts, and progress endpoints."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Header, Path, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from tc_api.cycle_models import (
    CompleteCycleRequest,
    CompleteCycleResponse,
    CompleteExecutionAttemptRequest,
    CreateCycleRequest,
    CycleErrorResponse,
    CycleItemPageResponse,
    CyclePageResponse,
    CycleStatusCountsResponse,
    CycleProgressResponse,
    CycleResourceResponse,
    DeleteCycleRequest,
    DeleteCycleResponse,
    ExecutionAttemptHistoryPageResponse,
    ExecutionAttemptResponse,
    ExecutionStatusCountsResponse,
    LabelScope,
    LabelScopeOptionResponse,
    OverallProgressResponse,
    OwnerProgressResponse,
    PermanentDeleteCycleRequest,
    PermanentDeleteCycleResponse,
    PlatformProgressResponse,
    ProgressValuesResponse,
    RefreshCycleItemSnapshotRequest,
    RefreshCycleItemSnapshotResponse,
    RefreshCycleSnapshotRequest,
    RecordStepResultsRequest,
    ReopenCycleRequest,
    ReopenCycleResponse,
    RestoreCycleRequest,
    RestoreCycleResponse,
    RetryExecutionAttemptRequest,
    RetryExecutionAttemptResponse,
    ScopeOptionPageResponse,
    ScopePreviewItemResponse,
    ScopePreviewResponse,
    StartCycleRequest,
    StartCycleResponse,
    StartExecutionAttemptRequest,
    StepExecutionResponse,
    UpdateCycleRequest,
    UpdateCycleScopeRequest,
    WorkItemProgressResponse,
    WorkItemScope,
    WorkItemScopeOptionResponse,
    LabelProgressResponse,
)
from tc_api.cycle_serializers import (
    attempt_response,
    cycle_item_version_diff,
    cycle_item_response,
    cycle_list_item_response,
    cycle_progress_response,
    cycle_resource_response,
    step_execution_response,
)
from tc_api.dependencies import ObjectStorageRuntimeDependency, SessionDependency
from tc_api.errors import CycleApiError, CycleCursorError
from tc_api.idempotency import execute_idempotent
from tc_api.ids import decode_identifier, encode_identifier
from tc_api.permanent_delete import delete_owned_attachment_objects
from tc_api.serializers import audit_context
from tc_backend.db.audit import AuditContext, append_audit
from tc_backend.db.cases import CaseRepository, normalize_tag_name
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.cycles import (
    CycleAggregate,
    CycleRepository,
    StepResultInput as LedgerStepResultInput,
)
from tc_backend.db.errors import ResourceNotFoundError, VersionConflictError
from tc_backend.db.models import (
    AttachmentOwnerType,
    CycleItem,
    CycleScopeType,
    CycleStatus,
    ExecutionAttempt,
    ExecutionStatus,
    Tag,
    TestCase,
    TestCaseStatus,
    TestCaseTag,
    TestCaseVersion,
    TestCycle,
    WorkItem,
)

router = APIRouter(tags=["cycles"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "model": CycleErrorResponse,
        "description": "Cycle ledger resource not found",
    },
    409: {
        "model": CycleErrorResponse,
        "description": "Version, state, snapshot, or attempt conflict",
    },
    422: {
        "model": CycleErrorResponse,
        "description": "Validation, cursor, or incomplete completion warning",
    },
}
IdempotencyHeader = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


@router.get(
    "/api/cycles/scope-options",
    operation_id="listCycleScopeOptions",
    response_model=ScopeOptionPageResponse,
    responses=ERROR_RESPONSES,
)
def list_cycle_scope_options(
    session: SessionDependency,
    scope_type: Annotated[Literal["WORK_ITEM", "LABEL"], Query(alias="type")],
    search: Annotated[
        str | None, Query(min_length=1, max_length=200, pattern=r"\S")
    ] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ScopeOptionPageResponse:
    fingerprint = _fingerprint({"type": scope_type, "search": search or ""})
    offset = _decode_cursor(cursor, fingerprint) if cursor else 0
    options: list[Any] = []
    if scope_type == "WORK_ITEM":
        rows = list(
            session.scalars(
                select(WorkItem)
                .where(WorkItem.workspace_id == DEFAULT_WORKSPACE_ID)
                .order_by(WorkItem.work_item_key)
            )
        )
        for row in rows:
            if (
                search
                and search.casefold()
                not in f"{row.work_item_key} {row.summary}".casefold()
            ):
                continue
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(TestCase)
                    .join(
                        TestCaseVersion,
                        TestCaseVersion.id == TestCase.current_version_id,
                    )
                    .where(
                        TestCase.work_item_id == row.id,
                        TestCase.deleted_at.is_(None),
                        TestCase.status == TestCaseStatus.ACTIVE.value,
                        TestCaseVersion.status == TestCaseStatus.ACTIVE.value,
                    )
                )
                or 0
            )
            if count:
                options.append(
                    WorkItemScopeOptionResponse(
                        type="WORK_ITEM",
                        work_item_id=encode_identifier("work_item", row.id),
                        work_item_key=row.work_item_key,
                        summary=row.summary,
                        owner_label=row.owner_label,
                        case_count=count,
                    )
                )
    else:
        label_rows = session.execute(
            select(Tag.name, func.count(func.distinct(TestCase.id)))
            .join(TestCaseTag, TestCaseTag.tag_id == Tag.id)
            .join(TestCase, TestCase.id == TestCaseTag.test_case_id)
            .join(TestCaseVersion, TestCaseVersion.id == TestCase.current_version_id)
            .where(
                Tag.workspace_id == DEFAULT_WORKSPACE_ID,
                TestCase.deleted_at.is_(None),
                TestCase.status == TestCaseStatus.ACTIVE.value,
                TestCaseVersion.status == TestCaseStatus.ACTIVE.value,
            )
            .group_by(Tag.name)
            .order_by(Tag.name)
        ).all()
        options = [
            LabelScopeOptionResponse(type="LABEL", label=name, case_count=count)
            for name, count in label_rows
            if not search or search.casefold() in name.casefold()
        ]
    page = options[offset : offset + limit]
    return ScopeOptionPageResponse(
        items=page,
        next_cursor=_encode_cursor(offset + limit, fingerprint)
        if offset + limit < len(options)
        else None,
        total=len(options),
    )


@router.get(
    "/api/cycles/scope-preview",
    operation_id="previewCycleScope",
    response_model=ScopePreviewResponse,
    responses=ERROR_RESPONSES,
)
def preview_cycle_scope(
    session: SessionDependency,
    scope_type: Annotated[Literal["WORK_ITEM", "LABEL"], Query(alias="scopeType")],
    work_item_id: Annotated[str | None, Query(alias="workItemId")] = None,
    label: Annotated[
        str | None, Query(min_length=1, max_length=100, pattern=r"\S")
    ] = None,
    labels: Annotated[list[str] | None, Query()] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ScopePreviewResponse:
    scope, scope_values = _resolve_scope_query(
        session, scope_type, work_item_id, label, labels
    )
    fingerprint = _fingerprint(
        {"scopeType": scope_type, "scopeValues": list(scope_values)}
    )
    offset = _decode_cursor(cursor, fingerprint) if cursor else 0
    rows = _scoped_rows(session, scope_type, scope_values)
    page = rows[offset : offset + limit]
    return ScopePreviewResponse(
        scope=scope,
        items=[_preview_item(session, case, version) for case, version in page],
        next_cursor=_encode_cursor(offset + limit, fingerprint)
        if offset + limit < len(rows)
        else None,
        total=len(rows),
        resolved_at=datetime.now(UTC),
    )


@router.get(
    "/api/cycles",
    operation_id="listCycles",
    response_model=CyclePageResponse,
    responses=ERROR_RESPONSES,
)
def list_cycles(
    session: SessionDependency,
    search: Annotated[
        str | None, Query(min_length=1, max_length=200, pattern=r"\S")
    ] = None,
    statuses: Annotated[
        list[Literal["PLANNED", "IN_PROGRESS", "COMPLETED"]] | None, Query()
    ] = None,
    scope_types: Annotated[
        list[Literal["WORK_ITEM", "LABEL"]] | None, Query(alias="scopeTypes")
    ] = None,
    work_item_ids: Annotated[list[str] | None, Query(alias="workItemIds")] = None,
    labels: Annotated[list[str] | None, Query()] = None,
    start_date_from: Annotated[date | None, Query(alias="startDateFrom")] = None,
    start_date_to: Annotated[date | None, Query(alias="startDateTo")] = None,
    sort: Literal[
        "START_DATE_ASC", "START_DATE_DESC", "UPDATED_AT_DESC"
    ] = "START_DATE_ASC",
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    deleted_only: Annotated[bool, Query(alias="deletedOnly")] = False,
) -> CyclePageResponse:
    for field, values in (
        ("statuses", statuses),
        ("scopeTypes", scope_types),
        ("workItemIds", work_item_ids),
        ("labels", labels),
    ):
        _unique(field, values)
    work_item_keys = []
    for value in work_item_ids or []:
        row = session.get(WorkItem, _id(value, "work_item", "workItemIds"))
        if row is None or row.workspace_id != DEFAULT_WORKSPACE_ID:
            raise ResourceNotFoundError(
                "WORK_ITEM", _id(value, "work_item", "workItemIds")
            )
        work_item_keys.append(row.work_item_key)
    normalized_labels = [normalize_tag_name(value) for value in labels or []]
    canonical_labels = list(
        session.scalars(
            select(Tag.name).where(
                Tag.workspace_id == DEFAULT_WORKSPACE_ID,
                Tag.normalized_name.in_(normalized_labels),
            )
        )
    )
    filters: list[Any] = [
        TestCycle.workspace_id == DEFAULT_WORKSPACE_ID,
        TestCycle.deleted_at.is_not(None)
        if deleted_only
        else TestCycle.deleted_at.is_(None),
    ]
    if search:
        filters.append(
            or_(
                func.lower(TestCycle.name).contains(search.casefold()),
                func.lower(TestCycle.description).contains(search.casefold()),
            )
        )
    if scope_types:
        filters.append(TestCycle.scope_type.in_(scope_types))
    if work_item_keys:
        filters.append(
            (TestCycle.scope_type == "WORK_ITEM")
            & TestCycle.scope_value.in_(work_item_keys)
        )
    if normalized_labels:
        filters.append(
            (TestCycle.scope_type == "LABEL")
            & (
                or_(*(TestCycle.scope_values.contains([value]) for value in canonical_labels))
                if canonical_labels
                else False
            )
        )
    if start_date_from:
        filters.append(func.date(TestCycle.starts_at) >= start_date_from)
    if start_date_to:
        filters.append(func.date(TestCycle.starts_at) <= start_date_to)
    count_filters = list(filters)
    if statuses:
        filters.append(TestCycle.status.in_(statuses))
    fingerprint = _fingerprint(
        {
            "search": search,
            "statuses": sorted(statuses or []),
            "scopeTypes": sorted(scope_types or []),
            "workItemIds": sorted(work_item_ids or []),
            "labels": sorted(normalized_labels),
            "startDateFrom": str(start_date_from),
            "startDateTo": str(start_date_to),
            "sort": sort,
            "deletedOnly": deleted_only,
        }
    )
    offset = _decode_cursor(cursor, fingerprint) if cursor else 0
    statement = select(TestCycle).where(*filters)
    order = {
        "START_DATE_ASC": (TestCycle.starts_at.asc(), TestCycle.id.asc()),
        "START_DATE_DESC": (TestCycle.starts_at.desc(), TestCycle.id.asc()),
        "UPDATED_AT_DESC": (TestCycle.updated_at.desc(), TestCycle.id.asc()),
    }[sort]
    all_rows = list(session.scalars(statement.order_by(*order)))
    rows = all_rows[offset : offset + limit]
    status_counts = {"PLANNED": 0, "IN_PROGRESS": 0, "COMPLETED": 0}
    for status_value, count in session.execute(
        select(TestCycle.status, func.count())
        .where(*count_filters)
        .group_by(TestCycle.status)
    ):
        status_counts[status_value] = count
    return CyclePageResponse(
        items=[cycle_list_item_response(session, row) for row in rows],
        next_cursor=_encode_cursor(offset + limit, fingerprint)
        if offset + limit < len(all_rows)
        else None,
        total=len(all_rows),
        status_counts=CycleStatusCountsResponse(**status_counts),
    )


@router.post(
    "/api/cycles",
    operation_id="createCycle",
    response_model=CycleResourceResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def create_cycle(
    payload: CreateCycleRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> CycleResourceResponse:
    context = audit_context(
        request, idempotency_key=idempotency_key, reason="Initial cycle creation"
    )

    def mutate(active: AuditContext) -> CycleResourceResponse:
        scope_type, scope_value, scope_values = _scope_value(session, payload.scope)
        aggregate = CycleRepository(session).create(
            name=payload.name,
            description=payload.description,
            start_date=payload.startDate,
            scope_type=scope_type,
            scope_value=scope_value,
            scope_values=scope_values,
            audit=active,
        )
        return cycle_resource_response(session, aggregate)

    return _mutation(
        session,
        idempotency_key,
        "CREATE_CYCLE",
        payload,
        CycleResourceResponse,
        context,
        mutate,
        audit_action="CREATE",
    )


@router.get(
    "/api/cycles/{cycleId}",
    operation_id="getCycle",
    response_model=CycleResourceResponse,
    responses=ERROR_RESPONSES,
)
def get_cycle(
    cycle_id: Annotated[str, Path(alias="cycleId")],
    session: SessionDependency,
    include_deleted: Annotated[bool, Query(alias="includeDeleted")] = False,
) -> CycleResourceResponse:
    return cycle_resource_response(
        session,
        CycleRepository(session).get(
            _id(cycle_id, "cycle", "cycleId"), include_deleted=include_deleted
        ),
    )


@router.patch(
    "/api/cycles/{cycleId}",
    operation_id="updateCycle",
    response_model=CycleResourceResponse,
    responses=ERROR_RESPONSES,
)
def update_cycle(
    cycle_id: Annotated[str, Path(alias="cycleId")],
    payload: UpdateCycleRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> CycleResourceResponse:
    decoded = _id(cycle_id, "cycle", "cycleId")
    context = audit_context(
        request, idempotency_key=idempotency_key, reason="Cycle details updated"
    )

    def mutate(active: AuditContext) -> CycleResourceResponse:
        repository = CycleRepository(session)
        cycle = repository.get(decoded, for_update=True)
        _assert_cycle_version(cycle, payload.baseResourceVersion)
        _require_cycle_details_mutable(cycle)
        aggregate = repository.update(
            cycle_id=decoded,
            base_resource_version=payload.baseResourceVersion,
            audit=active,
            name=payload.name,
            description=payload.description,
            starts_at=payload.startDate,
        )
        return cycle_resource_response(session, aggregate)

    return _cycle_mutation(
        session,
        idempotency_key,
        "UPDATE_CYCLE",
        cycle_id,
        payload,
        CycleResourceResponse,
        context,
        mutate,
        audit_action="UPDATE",
    )


@router.patch(
    "/api/cycles/{cycleId}/scope",
    operation_id="updateCycleScope",
    response_model=CycleResourceResponse,
    responses=ERROR_RESPONSES,
)
def update_cycle_scope(
    cycle_id: Annotated[str, Path(alias="cycleId")],
    payload: UpdateCycleScopeRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> CycleResourceResponse:
    decoded = _id(cycle_id, "cycle", "cycleId")
    context = audit_context(
        request, idempotency_key=idempotency_key, reason=payload.reason
    )

    def mutate(active: AuditContext) -> CycleResourceResponse:
        scope_type, scope_value, scope_values = _scope_value(session, payload.scope)
        repository = CycleRepository(session)
        cycle = repository.get(decoded, for_update=True)
        _assert_cycle_version(cycle, payload.baseResourceVersion)
        _require_planned(cycle, "CYCLE_SCOPE_LOCKED", "UPDATE_SCOPE")
        aggregate = repository.update(
            cycle_id=decoded,
            base_resource_version=payload.baseResourceVersion,
            audit=active,
            scope_type=scope_type,
            scope_value=scope_value,
            scope_values=scope_values,
        )
        return cycle_resource_response(session, aggregate)

    return _cycle_mutation(
        session,
        idempotency_key,
        "UPDATE_CYCLE_SCOPE",
        cycle_id,
        payload,
        CycleResourceResponse,
        context,
        mutate,
        audit_action="CHANGE_SCOPE",
    )


@router.delete(
    "/api/cycles/{cycleId}",
    operation_id="deleteCycle",
    response_model=DeleteCycleResponse,
    responses=ERROR_RESPONSES,
)
def delete_cycle(
    cycle_id: Annotated[str, Path(alias="cycleId")],
    payload: DeleteCycleRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> DeleteCycleResponse:
    decoded = _id(cycle_id, "cycle", "cycleId")
    context = audit_context(
        request, idempotency_key=idempotency_key, reason=payload.reason
    )

    def mutate(active: AuditContext) -> DeleteCycleResponse:
        repository = CycleRepository(session)
        cycle = repository.get(decoded, for_update=True)
        _assert_cycle_version(cycle, payload.baseResourceVersion)
        audit_id = repository.delete(cycle_id=decoded, audit=active)
        if cycle.deleted_at is None:  # pragma: no cover - repository invariant
            raise RuntimeError("cycle soft-delete timestamp was not recorded")
        return DeleteCycleResponse(
            operation="DELETE",
            cycle_id=cycle_id,
            deleted_at=cycle.deleted_at,
            audit_id=encode_identifier("audit", audit_id),
        )

    return _cycle_mutation(
        session,
        idempotency_key,
        "DELETE_CYCLE",
        cycle_id,
        payload,
        DeleteCycleResponse,
        context,
        mutate,
        audit_action="DELETE",
    )


@router.post(
    "/api/cycles/{cycleId}/restore",
    operation_id="restoreCycle",
    response_model=RestoreCycleResponse,
    responses=ERROR_RESPONSES,
)
def restore_cycle(
    cycle_id: Annotated[str, Path(alias="cycleId")],
    payload: RestoreCycleRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> RestoreCycleResponse:
    decoded = _id(cycle_id, "cycle", "cycleId")
    context = audit_context(
        request, idempotency_key=idempotency_key, reason=payload.reason
    )

    def mutate(active: AuditContext) -> RestoreCycleResponse:
        repository = CycleRepository(session)
        cycle = repository.get(decoded, for_update=True, include_deleted=True)
        _assert_cycle_version(cycle, payload.baseResourceVersion)
        if cycle.deleted_at is None:
            raise CycleApiError(
                "CYCLE_RESTORE_CONFLICT",
                "삭제된 사이클만 복구할 수 있습니다.",
                details={
                    "cycleId": cycle_id,
                    "status": cycle.status,
                    "operation": "RESTORE_CYCLE",
                },
            )
        aggregate = repository.restore(
            cycle_id=decoded,
            base_resource_version=payload.baseResourceVersion,
            audit=active,
        )
        if aggregate.audit_id is None:  # pragma: no cover - repository invariant
            raise RuntimeError("cycle restore audit id was not recorded")
        return RestoreCycleResponse(
            operation="RESTORE",
            cycle=cycle_list_item_response(session, aggregate.cycle),
            audit_id=encode_identifier("audit", aggregate.audit_id),
        )

    return _cycle_mutation(
        session,
        idempotency_key,
        "RESTORE_CYCLE",
        cycle_id,
        payload,
        RestoreCycleResponse,
        context,
        mutate,
        audit_action="RESTORE",
    )


@router.delete(
    "/api/cycles/{cycleId}/permanent",
    operation_id="permanentlyDeleteCycle",
    response_model=PermanentDeleteCycleResponse,
    responses=ERROR_RESPONSES,
)
def permanently_delete_cycle(
    cycle_id: Annotated[str, Path(alias="cycleId")],
    payload: PermanentDeleteCycleRequest,
    request: Request,
    session: SessionDependency,
    runtime: ObjectStorageRuntimeDependency,
) -> PermanentDeleteCycleResponse:
    decoded = _id(cycle_id, "cycle", "cycleId")
    context = audit_context(request, idempotency_key=None, reason=payload.reason)
    with session.begin():
        attempt_ids = CycleRepository(session).prepare_permanent_delete(
            cycle_id=decoded,
            base_resource_version=payload.baseResourceVersion,
        )
    deleted_attachment_count = delete_owned_attachment_objects(
        session,
        runtime,
        owner_type=AttachmentOwnerType.EXECUTION_ATTEMPT,
        owner_ids=attempt_ids,
        audit=context,
    )
    with session.begin():
        audit_id, metadata_count = CycleRepository(session).permanent_delete(
            cycle_id=decoded,
            base_resource_version=payload.baseResourceVersion,
            audit=context,
        )
    if metadata_count != deleted_attachment_count:  # pragma: no cover
        raise RuntimeError("attachment metadata changed during permanent deletion")
    return PermanentDeleteCycleResponse(
        operation="PERMANENT_DELETE",
        cycle_id=cycle_id,
        deleted_attachment_count=metadata_count,
        audit_id=encode_identifier("audit", audit_id),
    )


@router.post(
    "/api/cycles/{cycleId}/start",
    operation_id="startCycle",
    response_model=StartCycleResponse,
    responses=ERROR_RESPONSES,
)
def start_cycle(
    cycle_id: Annotated[str, Path(alias="cycleId")],
    payload: StartCycleRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> StartCycleResponse:
    decoded = _id(cycle_id, "cycle", "cycleId")
    context = _actor_context(
        audit_context(request, idempotency_key=idempotency_key, reason="Cycle started"),
        payload.actorLabel,
    )

    def mutate(active: AuditContext) -> StartCycleResponse:
        repository = CycleRepository(session)
        cycle = repository.get(decoded, for_update=True)
        _assert_cycle_version(cycle, payload.baseResourceVersion)
        _transition(cycle, "PLANNED", "IN_PROGRESS")
        scoped_case_ids = {
            row.id
            for row, _ in _scoped_rows(
                session,
                cycle.scope_type,
                tuple(cycle.scope_values or [cycle.scope_value]),
            )
        }
        pinned_case_ids = {
            row.test_case_id
            for row in session.scalars(
                select(CycleItem).where(CycleItem.cycle_id == cycle.id)
            )
        }
        if scoped_case_ids != pinned_case_ids:
            raise CycleApiError(
                "CYCLE_SCOPE_INVALID",
                "Cycle scope membership changed; explicitly refresh the PLANNED snapshot.",
                {
                    "cycleId": cycle_id,
                    "scopeType": cycle.scope_type,
                    "scopeValue": cycle.scope_value,
                },
            )
        aggregate = repository.start(
            cycle_id=decoded,
            base_resource_version=payload.baseResourceVersion,
            audit=active,
        )
        item_responses = [
            cycle_item_response(session, item) for item in aggregate.items
        ]
        return StartCycleResponse(
            cycle=cycle_resource_response(session, aggregate),
            items=sorted(item_responses, key=lambda item: (item.sort_order, item.id)),
            audit_id=encode_identifier("audit", _audit_id(aggregate)),
        )

    return _cycle_mutation(
        session,
        idempotency_key,
        "START_CYCLE",
        cycle_id,
        payload,
        StartCycleResponse,
        context,
        mutate,
        audit_action="START",
    )


@router.post(
    "/api/cycles/{cycleId}/complete",
    operation_id="completeCycle",
    response_model=CompleteCycleResponse,
    responses=ERROR_RESPONSES,
)
def complete_cycle(
    cycle_id: Annotated[str, Path(alias="cycleId")],
    payload: CompleteCycleRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> CompleteCycleResponse:
    decoded = _id(cycle_id, "cycle", "cycleId")
    context = _actor_context(
        audit_context(
            request, idempotency_key=idempotency_key, reason="Cycle completed"
        ),
        payload.actorLabel,
    )

    def mutate(active: AuditContext) -> CompleteCycleResponse:
        repository = CycleRepository(session)
        cycle = repository.get(decoded, for_update=True)
        _assert_cycle_version(cycle, payload.baseResourceVersion)
        _transition(cycle, "IN_PROGRESS", "COMPLETED")
        progress = repository.get_progress(decoded).overall
        if progress.unexecuted_count and not payload.acknowledgeIncomplete:
            raise CycleApiError(
                "CYCLE_INCOMPLETE_WARNING",
                "Cycle completion requires confirmation while executions remain incomplete.",
                {
                    "cycleId": cycle_id,
                    "unexecutedCount": progress.unexecuted_count,
                    "requiresConfirmation": True,
                },
                status=422,
            )
        aggregate = repository.complete(
            cycle_id=decoded,
            base_resource_version=payload.baseResourceVersion,
            audit=active,
        )
        warnings = []
        if progress.unexecuted_count:
            from tc_api.cycle_models import IncompleteExecutionWarningResponse

            warnings.append(
                IncompleteExecutionWarningResponse(
                    code="INCOMPLETE_EXECUTIONS",
                    unexecuted_count=progress.unexecuted_count,
                )
            )
        return CompleteCycleResponse(
            cycle=cycle_resource_response(session, aggregate),
            warnings=warnings,
            audit_id=encode_identifier("audit", _audit_id(aggregate)),
        )

    return _cycle_mutation(
        session,
        idempotency_key,
        "COMPLETE_CYCLE",
        cycle_id,
        payload,
        CompleteCycleResponse,
        context,
        mutate,
        audit_action="COMPLETE",
    )


@router.post(
    "/api/cycles/{cycleId}/reopen",
    operation_id="reopenCycle",
    response_model=ReopenCycleResponse,
    responses=ERROR_RESPONSES,
)
def reopen_cycle(
    cycle_id: Annotated[str, Path(alias="cycleId")],
    payload: ReopenCycleRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> ReopenCycleResponse:
    decoded = _id(cycle_id, "cycle", "cycleId")
    context = _actor_context(
        audit_context(request, idempotency_key=idempotency_key, reason=payload.reason),
        payload.actorLabel,
    )

    def mutate(active: AuditContext) -> ReopenCycleResponse:
        repository = CycleRepository(session)
        cycle = repository.get(decoded, for_update=True)
        _assert_cycle_version(cycle, payload.baseResourceVersion)
        _transition(cycle, "COMPLETED", "IN_PROGRESS")
        aggregate = repository.reopen(
            cycle_id=decoded,
            base_resource_version=payload.baseResourceVersion,
            audit=active,
        )
        return ReopenCycleResponse(
            cycle=cycle_resource_response(session, aggregate),
            audit_id=encode_identifier("audit", _audit_id(aggregate)),
        )

    return _cycle_mutation(
        session,
        idempotency_key,
        "REOPEN_CYCLE",
        cycle_id,
        payload,
        ReopenCycleResponse,
        context,
        mutate,
        audit_action="REOPEN",
    )


@router.post(
    "/api/cycles/{cycleId}/snapshot/refresh",
    operation_id="refreshCycleSnapshot",
    response_model=CycleResourceResponse,
    responses=ERROR_RESPONSES,
)
def refresh_cycle_snapshot(
    cycle_id: Annotated[str, Path(alias="cycleId")],
    payload: RefreshCycleSnapshotRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> CycleResourceResponse:
    decoded = _id(cycle_id, "cycle", "cycleId")
    context = audit_context(
        request, idempotency_key=idempotency_key, reason=payload.reason
    )

    def mutate(active: AuditContext) -> CycleResourceResponse:
        repository = CycleRepository(session)
        cycle = repository.get(decoded, for_update=True)
        _assert_cycle_version(cycle, payload.baseResourceVersion)
        _require_planned(cycle, "CYCLE_SCOPE_LOCKED", "REFRESH_SNAPSHOT")
        return cycle_resource_response(
            session,
            repository.refresh_snapshot(
                cycle_id=decoded,
                base_resource_version=payload.baseResourceVersion,
                audit=active,
            ),
        )

    return _cycle_mutation(
        session,
        idempotency_key,
        "REFRESH_CYCLE_SNAPSHOT",
        cycle_id,
        payload,
        CycleResourceResponse,
        context,
        mutate,
        audit_action="REFRESH_SNAPSHOT",
    )


@router.get(
    "/api/cycles/{cycleId}/items",
    operation_id="listCycleItems",
    response_model=CycleItemPageResponse,
    responses=ERROR_RESPONSES,
)
def list_cycle_items(
    cycle_id: Annotated[str, Path(alias="cycleId")],
    session: SessionDependency,
    search: Annotated[
        str | None, Query(min_length=1, max_length=200, pattern=r"\S")
    ] = None,
    work_item_ids: Annotated[list[str] | None, Query(alias="workItemIds")] = None,
    labels: Annotated[list[str] | None, Query()] = None,
    owner_labels: Annotated[list[str] | None, Query(alias="ownerLabels")] = None,
    has_latest_version: Annotated[bool | None, Query(alias="hasLatestVersion")] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    include_deleted: Annotated[bool, Query(alias="includeDeleted")] = False,
) -> CycleItemPageResponse:
    decoded = _id(cycle_id, "cycle", "cycleId")
    CycleRepository(session).get(decoded, include_deleted=include_deleted)
    for field, values in (
        ("workItemIds", work_item_ids),
        ("labels", labels),
        ("ownerLabels", owner_labels),
    ):
        _unique(field, values)
    work_ids = {_id(value, "work_item", "workItemIds") for value in work_item_ids or []}
    fingerprint = _fingerprint(
        {
            "cycleId": cycle_id,
            "search": search,
            "workItemIds": sorted(work_item_ids or []),
            "labels": sorted(labels or []),
            "ownerLabels": sorted(owner_labels or []),
            "hasLatestVersion": has_latest_version,
            "includeDeleted": include_deleted,
        }
    )
    offset = _decode_cursor(cursor, fingerprint) if cursor else 0
    if not any((search, work_ids, labels, owner_labels)) and has_latest_version is None:
        total = session.scalar(
            select(func.count()).select_from(CycleItem).where(CycleItem.cycle_id == decoded)
        ) or 0
        page_rows = list(
            session.scalars(
                select(CycleItem)
                .join(TestCase, TestCase.id == CycleItem.test_case_id)
                .where(CycleItem.cycle_id == decoded)
                .order_by(TestCase.sort_order, CycleItem.id)
                .offset(offset)
                .limit(limit)
            )
        )
        return CycleItemPageResponse(
            items=[cycle_item_response(session, row) for row in page_rows],
            next_cursor=_encode_cursor(offset + limit, fingerprint)
            if offset + limit < total
            else None,
            total=total,
        )
    rows = list(
        session.scalars(
            select(CycleItem)
            .join(TestCase, TestCase.id == CycleItem.test_case_id)
            .where(CycleItem.cycle_id == decoded)
            .order_by(TestCase.sort_order, CycleItem.id)
        )
    )
    responses = [cycle_item_response(session, row) for row in rows]
    responses = [
        item
        for item in responses
        if (
            not search
            or search.casefold()
            in f"{item.case_key} {item.pinned_version.summary}".casefold()
        )
        and (
            not work_ids
            or _id(item.work_item_id, "work_item", "workItemId") in work_ids
        )
        and (
            not labels
            or any(
                label.casefold() in {value.casefold() for value in item.labels}
                for label in labels
            )
        )
        and (
            not owner_labels
            or (
                item.owner_label is not None
                and item.owner_label.casefold()
                in {value.casefold() for value in owner_labels}
            )
        )
        and (has_latest_version is None or item.is_latest_version == has_latest_version)
    ]
    page = responses[offset : offset + limit]
    return CycleItemPageResponse(
        items=page,
        next_cursor=_encode_cursor(offset + limit, fingerprint)
        if offset + limit < len(responses)
        else None,
        total=len(responses),
    )


@router.post(
    "/api/cycle-items/{cycleItemId}/snapshot/refresh",
    operation_id="refreshCycleItemSnapshot",
    response_model=RefreshCycleItemSnapshotResponse,
    responses=ERROR_RESPONSES,
)
def refresh_cycle_item_snapshot(
    cycle_item_id: Annotated[str, Path(alias="cycleItemId")],
    payload: RefreshCycleItemSnapshotRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> RefreshCycleItemSnapshotResponse:
    decoded = _id(cycle_item_id, "cycle_item", "cycleItemId")
    expected = _id(
        payload.expectedPinnedVersionId, "case_version", "expectedPinnedVersionId"
    )
    target = _id(payload.targetVersionId, "case_version", "targetVersionId")
    context = audit_context(
        request, idempotency_key=idempotency_key, reason=payload.reason
    )

    def mutate(active: AuditContext) -> RefreshCycleItemSnapshotResponse:
        repository = CycleRepository(session)
        item = _cycle_item(session, decoded, for_update=True)
        cycle = repository.get(item.cycle_id, for_update=True)
        _assert_cycle_version(cycle, payload.baseResourceVersion)
        _require_planned(cycle, "CYCLE_SCOPE_LOCKED", "REFRESH_SNAPSHOT")
        latest = session.get(TestCase, item.test_case_id)
        target_row = session.get(TestCaseVersion, target)
        if item.test_case_version_id != expected:
            _snapshot_conflict(
                "SNAPSHOT_PIN_CONFLICT",
                cycle_item_id,
                payload,
                item.test_case_version_id,
                latest.current_version_id if latest else target,
            )
        if target_row is None or target_row.test_case_id != item.test_case_id:
            _snapshot_conflict(
                "SNAPSHOT_CASE_MISMATCH",
                cycle_item_id,
                payload,
                item.test_case_version_id,
                latest.current_version_id if latest else target,
            )
        if latest is None or latest.current_version_id != target:
            _snapshot_conflict(
                "SNAPSHOT_TARGET_NOT_LATEST",
                cycle_item_id,
                payload,
                item.test_case_version_id,
                latest.current_version_id if latest else target,
            )
        diff = cycle_item_version_diff(
            session, repository.get_item_snapshot_diff(decoded)
        )
        aggregate = repository.refresh_item_snapshot(
            cycle_item_id=decoded,
            base_resource_version=payload.baseResourceVersion,
            audit=active,
        )
        refreshed = next(row for row in aggregate.items if row.id == decoded)
        return RefreshCycleItemSnapshotResponse(
            cycle_item=cycle_item_response(session, refreshed),
            previous_pinned_version_id=payload.expectedPinnedVersionId,
            diff=diff,
            audit_id=encode_identifier("audit", _audit_id(aggregate)),
        )

    return _cycle_item_mutation(
        session,
        idempotency_key,
        "REFRESH_CYCLE_ITEM_SNAPSHOT",
        cycle_item_id,
        payload,
        RefreshCycleItemSnapshotResponse,
        context,
        mutate,
        audit_action="REFRESH_SNAPSHOT",
    )


@router.post(
    "/api/cycle-items/{cycleItemId}/attempts",
    operation_id="startExecutionAttempt",
    response_model=ExecutionAttemptResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def start_execution_attempt(
    cycle_item_id: Annotated[str, Path(alias="cycleItemId")],
    payload: StartExecutionAttemptRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> ExecutionAttemptResponse:
    decoded = _id(cycle_item_id, "cycle_item", "cycleItemId")
    context = audit_context(
        request, idempotency_key=idempotency_key, reason=payload.note
    )

    def mutate(active: AuditContext) -> ExecutionAttemptResponse:
        item, cycle = _locked_item_cycle(session, decoded)
        _require_execution(cycle, "START_ATTEMPT")
        previous = session.scalar(
            select(ExecutionAttempt)
            .where(
                ExecutionAttempt.cycle_item_id == item.id,
                ExecutionAttempt.platform == payload.platform,
            )
            .order_by(ExecutionAttempt.attempt_no.desc())
            .limit(1)
        )
        if previous is not None:
            raise CycleApiError(
                "EXECUTION_PLATFORM_DUPLICATE",
                "Use retry to append another attempt for this platform.",
                _attempt_conflict_details(cycle_item_id, payload.platform, previous),
            )
        attempt = _append_attempt(
            session,
            item=item,
            cycle=cycle,
            platform=payload.platform,
            attempt_no=1,
            executor_label=payload.executorLabel,
            note=payload.note,
            audit=active,
        )
        return attempt_response(session, attempt, is_latest=True)

    return _cycle_item_mutation(
        session,
        idempotency_key,
        "START_EXECUTION_ATTEMPT",
        cycle_item_id,
        payload,
        ExecutionAttemptResponse,
        context,
        mutate,
        audit_action="START",
    )


@router.get(
    "/api/cycle-items/{cycleItemId}/executions/{platform}/steps",
    operation_id="getStepExecution",
    response_model=StepExecutionResponse,
    responses=ERROR_RESPONSES,
)
def get_step_execution(
    cycle_item_id: Annotated[str, Path(alias="cycleItemId")],
    platform: Literal["WEB", "AOS", "IOS"],
    session: SessionDependency,
) -> StepExecutionResponse:
    item = _cycle_item(
        session, _id(cycle_item_id, "cycle_item", "cycleItemId")
    )
    attempt = session.scalar(
        select(ExecutionAttempt)
        .where(
            ExecutionAttempt.cycle_item_id == item.id,
            ExecutionAttempt.platform == platform,
        )
        .order_by(ExecutionAttempt.attempt_no.desc())
        .limit(1)
    )
    return step_execution_response(session, item, platform, attempt)


@router.patch(
    "/api/cycle-items/{cycleItemId}/executions/{platform}/steps",
    operation_id="recordStepExecutionResults",
    response_model=StepExecutionResponse,
    responses=ERROR_RESPONSES,
)
def record_step_execution_results(
    cycle_item_id: Annotated[str, Path(alias="cycleItemId")],
    platform: Literal["WEB", "AOS", "IOS"],
    payload: RecordStepResultsRequest,
    request: Request,
    session: SessionDependency,
) -> StepExecutionResponse:
    decoded = _id(cycle_item_id, "cycle_item", "cycleItemId")
    context = audit_context(
        request,
        idempotency_key=None,
        reason="Step execution results recorded",
    )
    with session.begin():
        repository = CycleRepository(session)
        _, cycle = _locked_item_cycle(session, decoded)
        _require_execution(cycle, "RECORD_STEP_RESULTS")
        attempt = repository.record_step_results(
            cycle_item_id=decoded,
            platform=platform,
            results=tuple(
                LedgerStepResultInput(
                    sequence=result.sequence,
                    status=result.status,
                    note=result.note,
                )
                for result in payload.results
            ),
            executor_label=payload.executorLabel,
            audit=context,
        )
        item = _cycle_item(session, decoded)
        return step_execution_response(session, item, platform, attempt)


@router.post(
    "/api/execution-attempts/{attemptId}/result",
    operation_id="recordExecutionAttemptResult",
    response_model=ExecutionAttemptResponse,
    responses=ERROR_RESPONSES,
)
def record_execution_attempt_result(
    attempt_id: Annotated[str, Path(alias="attemptId")],
    payload: CompleteExecutionAttemptRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> ExecutionAttemptResponse:
    decoded = _id(attempt_id, "attempt", "attemptId")
    context = audit_context(
        request, idempotency_key=idempotency_key, reason=payload.note
    )

    def mutate(active: AuditContext) -> ExecutionAttemptResponse:
        attempt = _attempt(session, decoded, for_update=True)
        item, cycle = _locked_item_cycle(session, attempt.cycle_item_id)
        _require_execution(cycle, "COMPLETE_ATTEMPT")
        if attempt.status != ExecutionStatus.UNEXECUTED.value:
            raise CycleApiError(
                "EXECUTION_ATTEMPT_FINAL",
                "Finished execution attempts are immutable.",
                _attempt_conflict_details(
                    encode_identifier("cycle_item", item.id), attempt.platform, attempt
                ),
            )
        if attempt.cycle_run_number != cycle.run_number:
            raise CycleApiError(
                "EXECUTION_ATTEMPT_SEQUENCE_CONFLICT",
                "An attempt from an earlier cycle run cannot be completed.",
                _attempt_conflict_details(
                    encode_identifier("cycle_item", item.id), attempt.platform, attempt
                ),
            )
        if payload.executorLabel is not None:
            attempt.executor_label = payload.executorLabel.strip()
        completed = CycleRepository(session).complete_attempt(
            attempt_id=decoded,
            status=payload.status,
            actual_result=payload.actualResult,
            note=payload.note if payload.note is not None else attempt.note,
            audit=active,
        )
        return attempt_response(session, completed, is_latest=True)

    return _attempt_mutation(
        session,
        idempotency_key,
        "RECORD_EXECUTION_ATTEMPT_RESULT",
        attempt_id,
        payload,
        ExecutionAttemptResponse,
        context,
        mutate,
        audit_action="COMPLETE",
    )


@router.post(
    "/api/cycle-items/{cycleItemId}/attempts/retry",
    operation_id="retryExecutionAttempt",
    response_model=RetryExecutionAttemptResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def retry_execution_attempt(
    cycle_item_id: Annotated[str, Path(alias="cycleItemId")],
    payload: RetryExecutionAttemptRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> RetryExecutionAttemptResponse:
    decoded = _id(cycle_item_id, "cycle_item", "cycleItemId")
    previous_id = _id(payload.previousAttemptId, "attempt", "previousAttemptId")
    context = audit_context(
        request, idempotency_key=idempotency_key, reason=payload.note
    )

    def mutate(active: AuditContext) -> RetryExecutionAttemptResponse:
        item, cycle = _locked_item_cycle(session, decoded)
        _require_execution(cycle, "RETRY_ATTEMPT")
        previous = _attempt(session, previous_id, for_update=True)
        latest = session.scalar(
            select(ExecutionAttempt)
            .where(
                ExecutionAttempt.cycle_item_id == decoded,
                ExecutionAttempt.platform == previous.platform,
            )
            .order_by(ExecutionAttempt.attempt_no.desc())
            .limit(1)
        )
        if (
            previous.cycle_item_id != decoded
            or latest is None
            or latest.id != previous.id
        ):
            raise CycleApiError(
                "EXECUTION_ATTEMPT_SEQUENCE_CONFLICT",
                "Retry must reference the latest attempt for this cycle item and platform.",
                _attempt_conflict_details(
                    cycle_item_id,
                    previous.platform,
                    latest or previous,
                    expected=previous.attempt_no,
                ),
            )
        if (
            previous.status == ExecutionStatus.UNEXECUTED.value
            and previous.cycle_run_number == cycle.run_number
        ):
            raise CycleApiError(
                "EXECUTION_ATTEMPT_ALREADY_ACTIVE",
                "The platform already has an active attempt.",
                _attempt_conflict_details(cycle_item_id, previous.platform, previous),
            )
        previous_wire = attempt_response(session, previous, is_latest=False)
        attempt = _append_attempt(
            session,
            item=item,
            cycle=cycle,
            platform=previous.platform,
            attempt_no=previous.attempt_no + 1,
            executor_label=payload.executorLabel,
            note=payload.note,
            audit=active,
        )
        return RetryExecutionAttemptResponse(
            previous_attempt=previous_wire,
            attempt=attempt_response(session, attempt, is_latest=True),
        )

    return _cycle_item_mutation(
        session,
        idempotency_key,
        "RETRY_EXECUTION_ATTEMPT",
        cycle_item_id,
        payload,
        RetryExecutionAttemptResponse,
        context,
        mutate,
        audit_action="START",
    )


@router.get(
    "/api/cycle-items/{cycleItemId}/attempts",
    operation_id="listExecutionAttemptHistory",
    response_model=ExecutionAttemptHistoryPageResponse,
    responses=ERROR_RESPONSES,
)
def list_execution_attempt_history(
    cycle_item_id: Annotated[str, Path(alias="cycleItemId")],
    session: SessionDependency,
    platforms: Annotated[list[Literal["WEB", "AOS", "IOS"]] | None, Query()] = None,
    statuses: Annotated[
        list[Literal["UNEXECUTED", "PASS", "FAIL", "BLOCKED"]] | None, Query()
    ] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ExecutionAttemptHistoryPageResponse:
    decoded = _id(cycle_item_id, "cycle_item", "cycleItemId")
    _cycle_item(session, decoded)
    _unique("platforms", platforms)
    _unique("statuses", statuses)
    fingerprint = _fingerprint(
        {
            "cycleItemId": cycle_item_id,
            "platforms": sorted(platforms or []),
            "statuses": sorted(statuses or []),
        }
    )
    offset = _decode_cursor(cursor, fingerprint) if cursor else 0
    statement = select(ExecutionAttempt).where(
        ExecutionAttempt.cycle_item_id == decoded
    )
    if platforms:
        statement = statement.where(ExecutionAttempt.platform.in_(platforms))
    ordered = statement.order_by(
        ExecutionAttempt.platform, ExecutionAttempt.attempt_no.desc()
    )
    all_platform_rows = list(session.scalars(ordered))
    latest_ids: list[str] = []
    seen: set[str] = set()
    for row in all_platform_rows:
        if row.platform not in seen:
            latest_ids.append(encode_identifier("attempt", row.id))
            seen.add(row.platform)
    if statuses:
        statement = statement.where(ExecutionAttempt.status.in_(statuses))
    rows = list(
        session.scalars(
            statement.order_by(
                ExecutionAttempt.platform, ExecutionAttempt.attempt_no.desc()
            )
        )
    )
    page = rows[offset : offset + limit]
    return ExecutionAttemptHistoryPageResponse(
        items=[
            attempt_response(
                session,
                row,
                is_latest=encode_identifier("attempt", row.id) in latest_ids,
            )
            for row in page
        ],
        next_cursor=_encode_cursor(offset + limit, fingerprint)
        if offset + limit < len(rows)
        else None,
        total=len(rows),
        latest_attempt_ids=latest_ids,
    )


@router.get(
    "/api/cycles/{cycleId}/progress",
    operation_id="getCycleProgress",
    response_model=CycleProgressResponse,
    responses=ERROR_RESPONSES,
)
def get_cycle_progress(
    cycle_id: Annotated[str, Path(alias="cycleId")],
    session: SessionDependency,
    platforms: Annotated[list[Literal["WEB", "AOS", "IOS"]] | None, Query()] = None,
    owner_labels: Annotated[list[str] | None, Query(alias="ownerLabels")] = None,
    work_item_ids: Annotated[list[str] | None, Query(alias="workItemIds")] = None,
    labels: Annotated[list[str] | None, Query()] = None,
) -> CycleProgressResponse:
    decoded = _id(cycle_id, "cycle", "cycleId")
    for field, values in (
        ("platforms", platforms),
        ("ownerLabels", owner_labels),
        ("workItemIds", work_item_ids),
        ("labels", labels),
    ):
        _unique(field, values)
    cycle = CycleRepository(session).get(decoded)
    _require_progress(cycle)
    if not any((platforms, owner_labels, work_item_ids, labels)):
        return cycle_progress_response(
            session, CycleRepository(session).get_progress(decoded)
        )
    return _filtered_progress_response(
        session,
        cycle,
        platforms=platforms,
        owner_labels=owner_labels,
        work_item_ids=work_item_ids,
        labels=labels,
    )


@router.get(
    "/api/cycles/{cycleId}/progress/overall",
    operation_id="getCycleOverallProgress",
    response_model=OverallProgressResponse,
    responses=ERROR_RESPONSES,
)
def get_cycle_overall_progress(
    cycle_id: Annotated[str, Path(alias="cycleId")], session: SessionDependency
) -> OverallProgressResponse:
    return get_cycle_progress(cycle_id, session).overall


@router.get(
    "/api/cycles/{cycleId}/progress/platform",
    operation_id="getCyclePlatformProgress",
    response_model=list[PlatformProgressResponse],
    responses=ERROR_RESPONSES,
)
def get_cycle_platform_progress(
    cycle_id: Annotated[str, Path(alias="cycleId")], session: SessionDependency
) -> list[PlatformProgressResponse]:
    return get_cycle_progress(cycle_id, session).by_platform


@router.get(
    "/api/cycles/{cycleId}/progress/owner",
    operation_id="getCycleOwnerProgress",
    response_model=list[OwnerProgressResponse],
    responses=ERROR_RESPONSES,
)
def get_cycle_owner_progress(
    cycle_id: Annotated[str, Path(alias="cycleId")], session: SessionDependency
) -> list[OwnerProgressResponse]:
    return get_cycle_progress(cycle_id, session).by_owner


@router.get(
    "/api/cycles/{cycleId}/progress/work-item",
    operation_id="getCycleWorkItemProgress",
    response_model=list[WorkItemProgressResponse],
    responses=ERROR_RESPONSES,
)
def get_cycle_work_item_progress(
    cycle_id: Annotated[str, Path(alias="cycleId")], session: SessionDependency
) -> list[WorkItemProgressResponse]:
    return get_cycle_progress(cycle_id, session).by_work_item


@router.get(
    "/api/cycles/{cycleId}/progress/label",
    operation_id="getCycleLabelProgress",
    response_model=list[LabelProgressResponse],
    responses=ERROR_RESPONSES,
)
def get_cycle_label_progress(
    cycle_id: Annotated[str, Path(alias="cycleId")], session: SessionDependency
) -> list[LabelProgressResponse]:
    return get_cycle_progress(cycle_id, session).by_label


def _scope_value(
    session: Session, scope: WorkItemScope | LabelScope
) -> tuple[str, str, tuple[str, ...]]:
    result: tuple[str, str, tuple[str, ...]]
    if isinstance(scope, WorkItemScope):
        identifier = _id(scope.workItemId, "work_item", "scope.workItemId")
        work_item = session.get(WorkItem, identifier)
        if (
            work_item is None
            or work_item.workspace_id != DEFAULT_WORKSPACE_ID
            or work_item.work_item_key != scope.workItemKey
        ):
            raise CycleApiError(
                "CYCLE_SCOPE_INVALID",
                "The work-item scope identifiers do not resolve to the same work item.",
                {
                    "scopeType": "WORK_ITEM",
                    "scopeValue": scope.workItemKey,
                },
            )
        result = (
            CycleScopeType.WORK_ITEM.value,
            work_item.work_item_key,
            (work_item.work_item_key,),
        )
    else:
        requested_labels = tuple(scope.labels or ([scope.label] if scope.label else []))
        normalized_labels = [normalize_tag_name(value) for value in requested_labels]
        canonical_by_normalized = {
            row.normalized_name: row.name
            for row in session.scalars(
                select(Tag).where(
                    Tag.workspace_id == DEFAULT_WORKSPACE_ID,
                    Tag.normalized_name.in_(normalized_labels),
                )
            )
        }
        if len(canonical_by_normalized) != len(normalized_labels):
            raise CycleApiError(
                "CYCLE_SCOPE_INVALID",
                "One or more label scope values do not exist.",
                {"scopeType": "LABEL", "scopeValues": list(requested_labels)},
            )
        canonical_labels = tuple(
            canonical_by_normalized[value] for value in normalized_labels
        )
        result = (
            CycleScopeType.LABEL.value,
            canonical_labels[0],
            canonical_labels,
        )
    if not _scoped_rows(session, result[0], result[2]):
        raise CycleApiError(
            "CYCLE_SCOPE_EMPTY",
            "The selected cycle scope contains no active test cases.",
            {"scopeType": result[0], "scopeValue": result[1]},
        )
    return result


def _resolve_scope_query(
    session: Session,
    scope_type: str,
    work_item_id: str | None,
    label: str | None,
    labels: list[str] | None,
) -> tuple[WorkItemScope | LabelScope, tuple[str, ...]]:
    if scope_type == "WORK_ITEM":
        if work_item_id is None or label is not None or labels is not None:
            raise ValueError("WORK_ITEM preview requires only workItemId")
        row = session.get(WorkItem, _id(work_item_id, "work_item", "workItemId"))
        if row is None or row.workspace_id != DEFAULT_WORKSPACE_ID:
            raise ResourceNotFoundError(
                "WORK_ITEM", _id(work_item_id, "work_item", "workItemId")
            )
        return WorkItemScope(
            type="WORK_ITEM", workItemId=work_item_id, workItemKey=row.work_item_key
        ), (row.work_item_key,)
    if work_item_id is not None or (label is None and not labels):
        raise ValueError("LABEL preview requires label or labels")
    scope_payload: dict[str, object] = {"type": "LABEL"}
    if label is not None:
        scope_payload["label"] = label
    if labels is not None:
        scope_payload["labels"] = labels
    scope = LabelScope.model_validate(scope_payload)
    _, _, canonical = _scope_value(session, scope)
    return LabelScope(type="LABEL", label=canonical[0], labels=list(canonical)), canonical


def _scoped_rows(
    session: Session, scope_type: str, scope_values: Sequence[str]
) -> list[tuple[TestCase, TestCaseVersion]]:
    return CycleRepository(session)._scoped_case_rows(
        CycleScopeType(scope_type),
        scope_values[0],
        scope_values=scope_values,
        for_update=False,
    )


def _preview_item(
    session: Session, case: TestCase, version: TestCaseVersion
) -> ScopePreviewItemResponse:
    work_item = session.get(WorkItem, case.work_item_id)
    snapshot = CaseRepository(session).get_version(case.id, version.version_number)
    if work_item is None:
        raise RuntimeError("test case work item is missing")
    return ScopePreviewItemResponse(
        test_case_id=encode_identifier("case", case.id),
        case_key=case.case_key,
        summary=version.summary,
        work_item_id=encode_identifier("work_item", work_item.id),
        work_item_key=work_item.work_item_key,
        owner_label=version.assignee_label,
        labels=list(snapshot.tag_names),
        current_version_id=encode_identifier("case_version", version.id),
        current_version=version.version_number,
    )


def _transition(cycle: TestCycle, expected: str, requested: str) -> None:
    if cycle.status != expected:
        allowed = {
            "PLANNED": ["IN_PROGRESS"],
            "IN_PROGRESS": ["COMPLETED"],
            "COMPLETED": ["IN_PROGRESS"],
        }[cycle.status]
        raise CycleApiError(
            "CYCLE_STATE_TRANSITION_INVALID",
            "The requested cycle state transition is invalid.",
            {
                "cycleId": encode_identifier("cycle", cycle.id),
                "currentStatus": cycle.status,
                "requestedStatus": requested,
                "allowedTransitions": allowed,
            },
        )


def _assert_cycle_version(cycle: TestCycle, expected: int) -> None:
    if cycle.resource_version != expected:
        raise VersionConflictError(
            "TEST_CYCLE", cycle.id, expected, cycle.resource_version
        )


def _require_planned(cycle: TestCycle, code: str, operation: str) -> None:
    if cycle.status != CycleStatus.PLANNED.value:
        raise CycleApiError(
            code,
            "The cycle mutation is locked in its current state.",
            {
                "cycleId": encode_identifier("cycle", cycle.id),
                "status": cycle.status,
                "operation": operation,
            },
        )


def _require_cycle_details_mutable(cycle: TestCycle) -> None:
    if cycle.status == CycleStatus.COMPLETED.value:
        raise CycleApiError(
            "CYCLE_EXECUTION_LOCKED",
            "Completed cycle details are locked.",
            {
                "cycleId": encode_identifier("cycle", cycle.id),
                "status": cycle.status,
                "operation": "UPDATE_DETAILS",
            },
        )


def _require_execution(cycle: TestCycle, operation: str) -> None:
    if cycle.status == CycleStatus.PLANNED.value:
        raise CycleApiError(
            "CYCLE_EXECUTION_NOT_STARTED",
            "Execution mutation requires an IN_PROGRESS cycle.",
            {
                "cycleId": encode_identifier("cycle", cycle.id),
                "status": cycle.status,
                "operation": operation,
            },
        )
    if cycle.status == CycleStatus.COMPLETED.value:
        raise CycleApiError(
            "CYCLE_EXECUTION_LOCKED",
            "Execution results are locked after completion.",
            {
                "cycleId": encode_identifier("cycle", cycle.id),
                "status": cycle.status,
                "operation": operation,
            },
        )


def _require_progress(cycle: TestCycle) -> None:
    if cycle.status == CycleStatus.PLANNED.value:
        raise CycleApiError(
            "CYCLE_EXECUTION_NOT_STARTED",
            "Progress excludes PLANNED cycles.",
            {
                "cycleId": encode_identifier("cycle", cycle.id),
                "status": cycle.status,
                "operation": "START_ATTEMPT",
            },
        )


def _snapshot_conflict(
    code: str,
    cycle_item_id: str,
    payload: RefreshCycleItemSnapshotRequest,
    actual: uuid.UUID,
    latest: uuid.UUID,
) -> None:
    raise CycleApiError(
        code,
        "The explicit snapshot refresh compare-and-swap precondition failed.",
        {
            "cycleItemId": cycle_item_id,
            "expectedPinnedVersionId": payload.expectedPinnedVersionId,
            "actualPinnedVersionId": encode_identifier("case_version", actual),
            "targetVersionId": payload.targetVersionId,
            "latestVersionId": encode_identifier("case_version", latest),
        },
    )


def _attempt_conflict_details(
    cycle_item_id: str,
    platform: str,
    attempt: ExecutionAttempt,
    *,
    expected: int | None = None,
) -> dict[str, Any]:
    return {
        "cycleItemId": cycle_item_id,
        "platform": platform,
        "attemptId": encode_identifier("attempt", attempt.id),
        "expectedAttemptNo": expected
        if expected is not None
        else attempt.attempt_no + 1,
        "actualAttemptNo": attempt.attempt_no,
        "status": attempt.status,
    }


def _filtered_progress_response(
    session: Session,
    cycle: TestCycle,
    *,
    platforms: Sequence[str] | None,
    owner_labels: list[str] | None,
    work_item_ids: list[str] | None,
    labels: list[str] | None,
) -> CycleProgressResponse:
    selected_platforms = platforms or ["WEB", "AOS", "IOS"]
    selected_work_items = {
        _id(value, "work_item", "workItemIds") for value in work_item_ids or []
    }
    selected_owners = {value.casefold() for value in owner_labels or []}
    selected_labels = {value.casefold() for value in labels or []}
    items = list(
        session.scalars(
            select(CycleItem)
            .where(CycleItem.cycle_id == cycle.id)
            .order_by(CycleItem.created_at, CycleItem.id)
        )
    )
    item_ids = [item.id for item in items]
    latest: dict[tuple[uuid.UUID, str], ExecutionAttempt] = {}
    if item_ids:
        for attempt in session.scalars(
            select(ExecutionAttempt)
            .where(ExecutionAttempt.cycle_item_id.in_(item_ids))
            .order_by(
                ExecutionAttempt.cycle_item_id,
                ExecutionAttempt.platform,
                ExecutionAttempt.attempt_no.desc(),
            )
        ):
            latest.setdefault((attempt.cycle_item_id, attempt.platform), attempt)

    slots: list[tuple[str, str, str, str, tuple[str, ...], str]] = []
    for item in items:
        pinned_row = session.get(TestCaseVersion, item.test_case_version_id)
        work_item = session.get(WorkItem, item.work_item_id)
        if pinned_row is None or work_item is None:
            raise RuntimeError("cycle progress item references a missing pinned row")
        snapshot = CaseRepository(session).get_version(
            item.test_case_id, pinned_row.version_number
        )
        item_labels = tuple(snapshot.tag_names) or ("UNLABELED",)
        owner = snapshot.assignee_label or "UNASSIGNED"
        if selected_work_items and item.work_item_id not in selected_work_items:
            continue
        if selected_owners and owner.casefold() not in selected_owners:
            continue
        if selected_labels and not selected_labels.intersection(
            value.casefold() for value in item_labels
        ):
            continue
        for platform in selected_platforms:
            latest_attempt = latest.get((item.id, platform))
            slots.append(
                (
                    platform,
                    owner,
                    encode_identifier("work_item", work_item.id),
                    work_item.work_item_key,
                    item_labels,
                    latest_attempt.status
                    if latest_attempt is not None
                    else "UNEXECUTED",
                )
            )

    overall_values = _wire_progress_values([slot[5] for slot in slots])
    platform_groups: dict[str, list[str]] = defaultdict(list)
    owner_groups: dict[str, list[str]] = defaultdict(list)
    work_item_groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    label_groups: dict[str, list[str]] = defaultdict(list)
    for platform, owner, work_item_id, work_item_key, item_labels, state in slots:
        platform_groups[platform].append(state)
        owner_groups[owner].append(state)
        work_item_groups[(work_item_id, work_item_key)].append(state)
        for item_label in item_labels:
            label_groups[item_label].append(state)
    return CycleProgressResponse(
        cycle_id=encode_identifier("cycle", cycle.id),
        calculated_at=datetime.now(UTC),
        excludes_planned=True,
        latest_attempt_only=True,
        overall=OverallProgressResponse(
            dimension="OVERALL",
            key="ALL",
            label="All executions",
            values=overall_values,
        ),
        by_platform=[
            PlatformProgressResponse(
                dimension="PLATFORM",
                key=cast(Any, platform),
                label=platform,
                values=_wire_progress_values(platform_groups[platform]),
            )
            for platform in ["WEB", "AOS", "IOS"]
            if platform in platform_groups
        ],
        by_owner=[
            OwnerProgressResponse(
                dimension="OWNER",
                key=owner,
                label=owner,
                values=_wire_progress_values(owner_groups[owner]),
            )
            for owner in sorted(owner_groups)
        ],
        by_work_item=[
            WorkItemProgressResponse(
                dimension="WORK_ITEM",
                key=work_item_id,
                label=work_item_key,
                values=_wire_progress_values(
                    work_item_groups[(work_item_id, work_item_key)]
                ),
            )
            for work_item_id, work_item_key in sorted(
                work_item_groups, key=lambda value: value[1]
            )
        ],
        by_label=[
            LabelProgressResponse(
                dimension="LABEL",
                key=label,
                label=label,
                values=_wire_progress_values(label_groups[label]),
            )
            for label in sorted(label_groups)
        ],
    )


def _wire_progress_values(statuses: list[str]) -> ProgressValuesResponse:
    counts = {status: 0 for status in ("UNEXECUTED", "PASS", "FAIL", "BLOCKED")}
    for status_value in statuses:
        counts[status_value] += 1
    executed = counts["PASS"] + counts["FAIL"]
    aggregate_status = "FAIL" if counts["FAIL"] else "PASS" if counts["PASS"] else "UNEXECUTED"
    return ProgressValuesResponse(
        counts=ExecutionStatusCountsResponse(total=len(statuses), **counts),
        executed_count=executed,
        pass_rate=counts["PASS"] / executed if executed else 0,
        aggregate_status=cast(Any, aggregate_status),
    )


def _append_attempt(
    session: Session,
    *,
    item: CycleItem,
    cycle: TestCycle,
    platform: str,
    attempt_no: int,
    executor_label: str | None,
    note: str | None,
    audit: AuditContext,
) -> ExecutionAttempt:
    """Insert the immutable attempt row once, including its optional start note."""
    now = datetime.now(UTC)
    attempt = ExecutionAttempt(
        id=uuid.uuid4(),
        cycle_item_id=item.id,
        platform=platform,
        attempt_no=attempt_no,
        cycle_run_number=cycle.run_number,
        status=ExecutionStatus.UNEXECUTED.value,
        actual_result=None,
        note=note.strip() if note else None,
        executor_label=executor_label.strip() if executor_label else None,
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(attempt)
    session.flush()
    append_audit(
        session,
        workspace_id=cycle.workspace_id,
        entity_type="EXECUTION_ATTEMPT",
        entity_id=attempt.id,
        action="START",
        before=None,
        after=CycleRepository(session)._attempt_snapshot(attempt),
        context=audit,
    )
    return attempt


def _locked_item_cycle(
    session: Session, item_id: uuid.UUID
) -> tuple[CycleItem, TestCycle]:
    item = _cycle_item(session, item_id, for_update=True)
    cycle = CycleRepository(session).get(item.cycle_id, for_update=True)
    return item, cycle


def _cycle_item(
    session: Session, identifier: uuid.UUID, *, for_update: bool = False
) -> CycleItem:
    statement = select(CycleItem).where(CycleItem.id == identifier)
    if for_update:
        statement = statement.with_for_update()
    row = session.scalar(statement)
    if row is None:
        raise ResourceNotFoundError("CYCLE_ITEM", identifier)
    return row


def _attempt(
    session: Session, identifier: uuid.UUID, *, for_update: bool = False
) -> ExecutionAttempt:
    statement = select(ExecutionAttempt).where(ExecutionAttempt.id == identifier)
    if for_update:
        statement = statement.with_for_update()
    row = session.scalar(statement)
    if row is None:
        raise ResourceNotFoundError("EXECUTION_ATTEMPT", identifier)
    return row


def _actor_context(context: AuditContext, actor_label: str | None) -> AuditContext:
    return replace(
        context, actor_label=actor_label.strip() if actor_label else context.actor_label
    )


def _audit_id(aggregate: CycleAggregate) -> uuid.UUID:
    if aggregate.audit_id is None:
        raise RuntimeError("cycle mutation did not append an audit row")
    return aggregate.audit_id


def _id(value: str, prefix: str, field: str) -> uuid.UUID:
    return decode_identifier(value, prefix=prefix, field=field)


def _unique(field: str, values: list[Any] | None) -> None:
    if values is not None and len(values) != len(set(values)):
        raise ValueError(f"{field} must contain unique values")


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _encode_cursor(offset: int, fingerprint: str) -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps(
                {"offset": offset, "filter": fingerprint}, separators=(",", ":")
            ).encode()
        )
        .decode()
        .rstrip("=")
    )


def _decode_cursor(cursor: str, fingerprint: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if (
            not isinstance(value, dict)
            or value.get("filter") != fingerprint
            or not isinstance(value.get("offset"), int)
            or value["offset"] < 0
        ):
            raise ValueError
        return value["offset"]
    except (
        ValueError,
        TypeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as error:
        raise CycleCursorError("cycle cursor is invalid") from error


def _mutation(
    session: Session,
    key: str | None,
    operation: str,
    payload: Any,
    response_model: Any,
    context: AuditContext,
    mutate: Any,
    *,
    audit_action: str,
) -> Any:
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation=operation,
            payload=payload.model_dump(mode="json", by_alias=True),
            response_model=response_model,
            context=context,
            action=mutate,
            audit_action=audit_action,
        )


def _cycle_mutation(
    session: Session,
    key: str | None,
    operation: str,
    cycle_id: str,
    payload: Any,
    response_model: Any,
    context: AuditContext,
    mutate: Any,
    *,
    audit_action: str,
) -> Any:
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation=operation,
            payload={
                "cycleId": cycle_id,
                **payload.model_dump(mode="json", by_alias=True),
            },
            response_model=response_model,
            context=context,
            action=mutate,
            audit_action=audit_action,
        )


def _cycle_item_mutation(
    session: Session,
    key: str | None,
    operation: str,
    item_id: str,
    payload: Any,
    response_model: Any,
    context: AuditContext,
    mutate: Any,
    *,
    audit_action: str,
) -> Any:
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation=operation,
            payload={
                "cycleItemId": item_id,
                **payload.model_dump(mode="json", by_alias=True),
            },
            response_model=response_model,
            context=context,
            action=mutate,
            audit_action=audit_action,
        )


def _attempt_mutation(
    session: Session,
    key: str | None,
    operation: str,
    attempt_id: str,
    payload: Any,
    response_model: Any,
    context: AuditContext,
    mutate: Any,
    *,
    audit_action: str,
) -> Any:
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation=operation,
            payload={
                "attemptId": attempt_id,
                **payload.model_dump(mode="json", by_alias=True),
            },
            response_model=response_model,
            context=context,
            action=mutate,
            audit_action=audit_action,
        )
