"""Folder tree query and mutation endpoints."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any, Annotated

from fastapi import APIRouter, Header, Path, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from tc_api.dependencies import SessionDependency
from tc_api.idempotency import execute_idempotent, pending_audit_id
from tc_api.ids import decode_identifier, encode_identifier
from tc_api.models import (
    CreateFolderRequest,
    ErrorResponse,
    FolderChildrenResponse,
    FolderDeletePreviewResponse,
    FolderMutationResponse,
    FolderTreeResponse,
    MoveFolderRequest,
    RenameFolderRequest,
    RestoreFolderRequest,
    SoftDeleteFolderRequest,
    SortFolderRequest,
)
from tc_api.serializers import (
    audit_context,
    create_folder_mutation_response,
    folder_mutation_response,
    folder_node_response,
    utc_now,
)
from tc_backend.db.audit import AuditContext
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.folders import FolderRepository

router = APIRouter(prefix="/api/folders", tags=["folders"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Folder not found"},
    409: {"model": ErrorResponse, "description": "Folder topology or version conflict"},
    422: {"model": ErrorResponse, "description": "Validation error"},
}

IdempotencyHeader = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=255),
]


@router.get("/tree", operation_id="getFolderTree", response_model=FolderTreeResponse)
def get_folder_tree(session: SessionDependency) -> FolderTreeResponse:
    nodes = FolderRepository(session).list_tree()
    return FolderTreeResponse(
        workspace_id=encode_identifier("workspace", DEFAULT_WORKSPACE_ID),
        generated_at=utc_now(),
        items=[folder_node_response(node) for node in nodes],
    )


@router.get(
    "/children",
    operation_id="getFolderChildren",
    response_model=FolderChildrenResponse,
    responses=ERROR_RESPONSES,
)
def get_folder_children(
    session: SessionDependency,
    parent_id: Annotated[str | None, Query(alias="parentId")] = None,
) -> FolderChildrenResponse:
    decoded_parent = _folder_id(parent_id, field="parentId") if parent_id is not None else None
    nodes = FolderRepository(session).list_children(parent_id=decoded_parent)
    return FolderChildrenResponse(
        workspace_id=encode_identifier("workspace", DEFAULT_WORKSPACE_ID),
        parent_id=parent_id,
        generated_at=utc_now(),
        items=[folder_node_response(node, include_children=False) for node in nodes],
    )


@router.post(
    "",
    operation_id="createFolder",
    response_model=FolderMutationResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def create_folder(
    payload: CreateFolderRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> FolderMutationResponse:
    parent_id = _folder_id(payload.parentId, field="parentId") if payload.parentId else None
    context = audit_context(request, idempotency_key=idempotency_key)

    def mutate(_: AuditContext) -> FolderMutationResponse:
        folder = FolderRepository(session).create(
            name=payload.name,
            parent_id=parent_id,
            sort_order=payload.sortOrder,
            audit=context,
        )
        audit_id = pending_audit_id(session, trace_id=context.trace_id or "", action="CREATE")
        return create_folder_mutation_response(folder, operation="CREATE", audit_id=audit_id)

    with session.begin():
        return execute_idempotent(
            session,
            key=idempotency_key,
            operation="CREATE_FOLDER",
            payload=payload.model_dump(mode="json", by_alias=True),
            response_model=FolderMutationResponse,
            context=context,
            action=mutate,
        )


@router.patch(
    "/{folderId}",
    operation_id="renameFolder",
    response_model=FolderMutationResponse,
    response_model_exclude_none=True,
    responses=ERROR_RESPONSES,
)
def rename_folder(
    folder_id: Annotated[str, Path(alias="folderId")],
    payload: RenameFolderRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> FolderMutationResponse:
    decoded_id = _folder_id(folder_id)
    context = audit_context(request, idempotency_key=idempotency_key)

    def mutate(_: AuditContext) -> FolderMutationResponse:
        folder = FolderRepository(session).rename(
            folder_id=decoded_id,
            base_version=payload.baseVersion,
            name=payload.name,
            audit=context,
        )
        audit_id = pending_audit_id(session, trace_id=context.trace_id or "", action="RENAME")
        return create_folder_mutation_response(folder, operation="RENAME", audit_id=audit_id)

    with session.begin():
        return execute_idempotent(
            session,
            key=idempotency_key,
            operation="RENAME_FOLDER",
            payload={"folderId": folder_id, **payload.model_dump(mode="json", by_alias=True)},
            response_model=FolderMutationResponse,
            context=context,
            action=mutate,
        )


@router.post(
    "/{folderId}/move",
    operation_id="moveFolder",
    response_model=FolderMutationResponse,
    response_model_exclude_none=True,
    responses=ERROR_RESPONSES,
)
def move_folder(
    folder_id: Annotated[str, Path(alias="folderId")],
    payload: MoveFolderRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> FolderMutationResponse:
    decoded_id = _folder_id(folder_id)
    target_id = (
        _folder_id(payload.targetParentId, field="targetParentId")
        if payload.targetParentId is not None
        else None
    )
    context = audit_context(request, idempotency_key=idempotency_key)

    def mutate(_: AuditContext) -> FolderMutationResponse:
        result = FolderRepository(session).move(
            folder_id=decoded_id,
            base_version=payload.baseVersion,
            target_parent_id=target_id,
            sort_order=payload.sortOrder,
            audit=context,
        )
        return folder_mutation_response(result, operation="MOVE_SUBTREE")

    return _mutation(
        session=session,
        key=idempotency_key,
        operation="MOVE_FOLDER",
        folder_id=folder_id,
        payload=payload,
        context=context,
        mutate=mutate,
    )


@router.post(
    "/{folderId}/sort",
    operation_id="sortFolder",
    response_model=FolderMutationResponse,
    response_model_exclude_none=True,
    responses=ERROR_RESPONSES,
)
def sort_folder(
    folder_id: Annotated[str, Path(alias="folderId")],
    payload: SortFolderRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> FolderMutationResponse:
    decoded_id = _folder_id(folder_id)
    context = audit_context(request, idempotency_key=idempotency_key)

    def mutate(_: AuditContext) -> FolderMutationResponse:
        result = FolderRepository(session).sort(
            folder_id=decoded_id,
            base_version=payload.baseVersion,
            sort_order=payload.sortOrder,
            audit=context,
        )
        return folder_mutation_response(result, operation="SORT")

    return _mutation(
        session=session,
        key=idempotency_key,
        operation="SORT_FOLDER",
        folder_id=folder_id,
        payload=payload,
        context=context,
        mutate=mutate,
    )


@router.get(
    "/{folderId}/delete-preview",
    operation_id="previewFolderDelete",
    response_model=FolderDeletePreviewResponse,
    responses=ERROR_RESPONSES,
)
def preview_folder_delete(
    folder_id: Annotated[str, Path(alias="folderId")],
    session: SessionDependency,
) -> FolderDeletePreviewResponse:
    impact = FolderRepository(session).preview_soft_delete(folder_id=_folder_id(folder_id))
    return FolderDeletePreviewResponse(
        folder_id=folder_id,
        affected_folder_count=impact.folder_count,
        active_test_case_count=impact.test_case_count,
        requires_confirmation=impact.requires_confirmation,
    )


@router.delete(
    "/{folderId}",
    operation_id="softDeleteFolder",
    response_model=FolderMutationResponse,
    response_model_exclude_none=True,
    responses=ERROR_RESPONSES,
)
def soft_delete_folder(
    folder_id: Annotated[str, Path(alias="folderId")],
    payload: SoftDeleteFolderRequest,
    request: Request,
    session: SessionDependency,
    confirm: Annotated[bool, Query(description="Confirms a non-empty folder subtree")] = False,
    idempotency_key: IdempotencyHeader = None,
) -> FolderMutationResponse:
    decoded_id = _folder_id(folder_id)
    context = audit_context(
        request,
        idempotency_key=idempotency_key,
        reason=payload.reason,
    )

    def mutate(_: AuditContext) -> FolderMutationResponse:
        result = FolderRepository(session).soft_delete(
            folder_id=decoded_id,
            base_version=payload.baseVersion,
            acknowledge_contents=confirm,
            audit=context,
        )
        return folder_mutation_response(result, operation="SOFT_DELETE_SUBTREE")

    return _mutation(
        session=session,
        key=idempotency_key,
        operation="SOFT_DELETE_FOLDER",
        folder_id=folder_id,
        payload={"confirm": confirm, **payload.model_dump(mode="json", by_alias=True)},
        context=context,
        mutate=mutate,
    )


@router.post(
    "/{folderId}/restore",
    operation_id="restoreFolder",
    response_model=FolderMutationResponse,
    response_model_exclude_none=True,
    responses=ERROR_RESPONSES,
)
def restore_folder(
    folder_id: Annotated[str, Path(alias="folderId")],
    payload: RestoreFolderRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> FolderMutationResponse:
    decoded_id = _folder_id(folder_id)
    batch_id = decode_identifier(
        payload.deletionBatchId,
        prefix="folder_delete",
        field="deletionBatchId",
    )
    target_was_supplied = "targetParentId" in payload.model_fields_set
    target_id = (
        _folder_id(payload.targetParentId, field="targetParentId")
        if payload.targetParentId is not None
        else None
    )
    context = audit_context(
        request,
        idempotency_key=idempotency_key,
        reason=payload.reason,
    )

    def mutate(_: AuditContext) -> FolderMutationResponse:
        result = FolderRepository(session).restore(
            folder_id=decoded_id,
            base_version=payload.baseVersion,
            deletion_batch_id=batch_id,
            audit=context,
            use_original_parent=not target_was_supplied,
            target_parent_id=target_id,
        )
        return folder_mutation_response(result, operation="RESTORE_SUBTREE")

    return _mutation(
        session=session,
        key=idempotency_key,
        operation="RESTORE_FOLDER",
        folder_id=folder_id,
        payload={
            **payload.model_dump(mode="json", by_alias=True),
            "targetParentSpecified": target_was_supplied,
        },
        context=context,
        mutate=mutate,
    )


def _mutation(
    *,
    session: Session,
    key: str | None,
    operation: str,
    folder_id: str,
    payload: BaseModel | Mapping[str, Any],
    context: AuditContext,
    mutate: Callable[[AuditContext], FolderMutationResponse],
) -> FolderMutationResponse:
    data = payload.model_dump(mode="json", by_alias=True) if isinstance(payload, BaseModel) else payload
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation=operation,
            payload={"folderId": folder_id, **dict(data)},
            response_model=FolderMutationResponse,
            context=context,
            action=mutate,
        )


def _folder_id(value: str | None, *, field: str = "folderId") -> uuid.UUID:
    if value is None:
        raise ValueError(f"{field} is required")
    return decode_identifier(value, prefix="folder", field=field)
