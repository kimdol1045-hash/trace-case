"""Workspace settings endpoints."""

from __future__ import annotations

from typing import Any, Annotated

from fastapi import APIRouter, Header, Request

from tc_api.dependencies import SessionDependency
from tc_api.idempotency import execute_idempotent
from tc_api.models import ErrorResponse, UpdateWorkspaceSettingsRequest, WorkspaceSettingsResponse
from tc_api.serializers import audit_context, workspace_response
from tc_backend.db.audit import AuditContext
from tc_backend.db.workspaces import WorkspacePatch, WorkspaceRepository

router = APIRouter(prefix="/api/workspace/settings", tags=["workspace"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    409: {"model": ErrorResponse, "description": "Optimistic lock or idempotency conflict"},
    422: {"model": ErrorResponse, "description": "Validation error"},
}


@router.get("", operation_id="getWorkspaceSettings", response_model=WorkspaceSettingsResponse)
def get_workspace_settings(session: SessionDependency) -> WorkspaceSettingsResponse:
    return workspace_response(WorkspaceRepository(session).get())


@router.patch(
    "",
    operation_id="updateWorkspaceSettings",
    response_model=WorkspaceSettingsResponse,
    responses=ERROR_RESPONSES,
)
def update_workspace_settings(
    payload: UpdateWorkspaceSettingsRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ] = None,
) -> WorkspaceSettingsResponse:
    context = audit_context(request, idempotency_key=idempotency_key)

    def mutate(_: AuditContext) -> WorkspaceSettingsResponse:
        settings = WorkspaceRepository(session).update_settings(
            base_version=payload.baseVersion,
            patch=WorkspacePatch(
                team_name=payload.teamName,
                case_prefix=payload.casePrefix,
                issue_prefix=payload.issuePrefix,
                default_environment=payload.defaultEnvironment,
                generation_quiet_minutes=payload.generationQuietMinutes,
            ),
            audit=context,
        )
        return workspace_response(settings)

    with session.begin():
        return execute_idempotent(
            session,
            key=idempotency_key,
            operation="UPDATE_WORKSPACE_SETTINGS",
            payload=payload.model_dump(mode="json", by_alias=True),
            response_model=WorkspaceSettingsResponse,
            context=context,
            action=mutate,
        )
