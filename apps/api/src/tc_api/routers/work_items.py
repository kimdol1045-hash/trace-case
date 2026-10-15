"""PostgreSQL-backed parent work item ledger endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tc_api.dependencies import SessionDependency
from tc_api.idempotency import execute_idempotent
from tc_api.ids import decode_identifier
from tc_api.models import (
    CreateWorkItemRequest,
    ErrorResponse,
    UpdateWorkItemRequest,
    WorkItemDetailResponse,
    WorkItemPageResponse,
    WorkItemResourceResponse,
)
from tc_api.serializers import audit_context, case_list_item_response, work_item_response
from tc_backend.db.audit import AuditContext
from tc_backend.db.cases import CaseRepository
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.models import TestCase, WorkItem

router = APIRouter(prefix="/api/work-items", tags=["work-items"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Work item not found"},
    409: {"model": ErrorResponse, "description": "Key, category or version conflict"},
    422: {"model": ErrorResponse, "description": "Validation error"},
}
IdempotencyHeader = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


@router.get(
    "",
    operation_id="listWorkItems",
    response_model=WorkItemPageResponse,
    responses=ERROR_RESPONSES,
)
def list_work_items(
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WorkItemPageResponse:
    repository = CaseRepository(session)
    items = repository.list_work_items(limit=limit, offset=offset)
    total = session.scalar(
        select(func.count()).select_from(WorkItem).where(
            WorkItem.workspace_id == DEFAULT_WORKSPACE_ID
        )
    ) or 0
    return WorkItemPageResponse(
        items=[work_item_response(item) for item in items],
        total=total,
    )


@router.post(
    "",
    operation_id="createWorkItem",
    response_model=WorkItemResourceResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def create_work_item(
    payload: CreateWorkItemRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> WorkItemResourceResponse:
    context = audit_context(
        request,
        idempotency_key=idempotency_key,
        reason="Initial work item creation",
    )

    def mutate(_: AuditContext) -> WorkItemResourceResponse:
        item = CaseRepository(session).create_work_item(
            work_item_key=payload.key,
            category_id=decode_identifier(
                payload.categoryId, prefix="folder", field="categoryId"
            ),
            summary=payload.summary,
            description=payload.description,
            owner_label=payload.ownerLabel,
            labels=payload.labels,
            audit=context,
        )
        return work_item_response(item)

    return _mutation(
        session,
        idempotency_key,
        "CREATE_WORK_ITEM",
        payload.model_dump(mode="json", by_alias=True),
        WorkItemResourceResponse,
        context,
        mutate,
        "CREATE",
    )


@router.get(
    "/{workItemKey}",
    operation_id="getWorkItem",
    response_model=WorkItemDetailResponse,
    responses=ERROR_RESPONSES,
)
def get_work_item(
    work_item_key: Annotated[
        str,
        Path(alias="workItemKey", min_length=1, max_length=100, pattern=r"\S"),
    ],
    session: SessionDependency,
) -> WorkItemDetailResponse:
    repository = CaseRepository(session)
    item = repository.get_work_item_by_key(work_item_key)
    case_ids = list(
        session.scalars(
            select(TestCase.id)
            .where(
                TestCase.workspace_id == DEFAULT_WORKSPACE_ID,
                TestCase.work_item_id == item.id,
                TestCase.deleted_at.is_(None),
            )
            .order_by(TestCase.sort_order, TestCase.id)
            .limit(200)
        )
    )
    return WorkItemDetailResponse(
        work_item=work_item_response(item),
        test_cases=[
            case_list_item_response(repository.get_aggregate(case_id), item)
            for case_id in case_ids
        ],
    )


@router.patch(
    "/{workItemKey}",
    operation_id="updateWorkItem",
    response_model=WorkItemResourceResponse,
    responses=ERROR_RESPONSES,
)
def update_work_item(
    work_item_key: Annotated[
        str,
        Path(alias="workItemKey", min_length=1, max_length=100, pattern=r"\S"),
    ],
    payload: UpdateWorkItemRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> WorkItemResourceResponse:
    context = audit_context(
        request,
        idempotency_key=idempotency_key,
        reason=payload.changeReason,
    )

    def mutate(_: AuditContext) -> WorkItemResourceResponse:
        item = CaseRepository(session).update_work_item(
            work_item_key=work_item_key,
            base_version=payload.baseResourceVersion,
            category_id=decode_identifier(
                payload.categoryId, prefix="folder", field="categoryId"
            ),
            summary=payload.summary,
            description=payload.description,
            owner_label=payload.ownerLabel,
            labels=payload.labels,
            audit=context,
        )
        return work_item_response(item)

    return _mutation(
        session,
        idempotency_key,
        "UPDATE_WORK_ITEM",
        {"workItemKey": work_item_key, **payload.model_dump(mode="json", by_alias=True)},
        WorkItemResourceResponse,
        context,
        mutate,
        "UPDATE",
    )


def _mutation(
    session: Session,
    key: str | None,
    operation: str,
    payload: dict[str, Any],
    response_model: type[WorkItemResourceResponse],
    context: AuditContext,
    mutate: Any,
    audit_action: str,
) -> WorkItemResourceResponse:
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation=operation,
            payload=payload,
            response_model=response_model,
            context=context,
            action=mutate,
            audit_action=audit_action,
        )
