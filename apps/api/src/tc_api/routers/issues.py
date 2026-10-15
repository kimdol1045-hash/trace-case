"""Sprint 4 issue aggregate, relation, comment, and history endpoints."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Path, Query, Request, status
from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.orm import Session

from tc_api.dependencies import ObjectStorageRuntimeDependency, SessionDependency
from tc_api.errors import CycleCursorError
from tc_api.idempotency import execute_idempotent
from tc_api.ids import decode_identifier, encode_identifier
from tc_api.permanent_delete import delete_owned_attachment_objects
from tc_api.models import ErrorResponse
from tc_api.serializers import audit_context
from tc_api.sprint4_models import (
    CreateIssueCommentRequest,
    CreateIssueFromAttemptRequest,
    CreateIssueLinkRequest,
    CreateIssueRequest,
    DeleteIssueLinkRequest,
    DeleteIssueLinkResponse,
    DeleteIssueRequest,
    DeleteIssueResponse,
    IssueCommentPage,
    IssueCommentResource,
    IssueHistoryPage,
    IssueLinkPage,
    IssueLinkResource,
    IssuePage,
    IssueResource,
    IssueSeverityValue,
    IssueStatusValue,
    PermanentDeleteIssueRequest,
    PermanentDeleteIssueResponse,
    RestoreIssueRequest,
    RestoreIssueResponse,
    UpdateIssueRequest,
)
from tc_api.sprint4_serializers import (
    issue_comment_resource,
    issue_history_event,
    issue_link_resource,
    issue_list_item,
    issue_resource,
)
from tc_backend.db.audit import AuditContext, append_audit
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.errors import (
    PermanentDeleteConflictError,
    ResourceNotFoundError,
    VersionConflictError,
)
from tc_backend.db.issues import IssueRepository, IssueStepInput
from tc_backend.db.models import (
    Attachment,
    AttachmentOwnerType,
    AuditLog,
    CycleItem,
    Issue,
    IssueLink,
)

router = APIRouter(tags=["issues"])
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Issue or linked resource not found"},
    409: {"model": ErrorResponse, "description": "Version, state, or link conflict"},
    422: {"model": ErrorResponse, "description": "Validation or cursor error"},
}
IdempotencyHeader = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


@router.get(
    "/api/issues",
    operation_id="listIssues",
    response_model=IssuePage,
    responses=ERROR_RESPONSES,
)
def list_issues(
    session: SessionDependency,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    statuses: Annotated[list[IssueStatusValue] | None, Query()] = None,
    severities: Annotated[list[IssueSeverityValue] | None, Query()] = None,
    cycle_ids: Annotated[list[str] | None, Query(alias="cycleIds")] = None,
    test_case_ids: Annotated[list[str] | None, Query(alias="testCaseIds")] = None,
    cycle_item_ids: Annotated[list[str] | None, Query(alias="cycleItemIds")] = None,
    execution_attempt_ids: Annotated[
        list[str] | None, Query(alias="executionAttemptIds")
    ] = None,
    assignee_labels: Annotated[list[str] | None, Query(alias="assigneeLabels")] = None,
    include_deleted: Annotated[bool, Query(alias="includeDeleted")] = False,
    deleted_only: Annotated[bool, Query(alias="deletedOnly")] = False,
    sort: Literal[
        "UPDATED_AT_DESC", "UPDATED_AT_ASC", "SEVERITY_DESC", "KEY_ASC"
    ] = "UPDATED_AT_DESC",
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> IssuePage:
    supplied = {
        "statuses": statuses,
        "severities": severities,
        "cycleIds": cycle_ids,
        "testCaseIds": test_case_ids,
        "cycleItemIds": cycle_item_ids,
        "executionAttemptIds": execution_attempt_ids,
        "assigneeLabels": assignee_labels,
    }
    for field, values in supplied.items():
        if values is not None and len(values) != len(set(values)):
            raise ValueError(f"{field} must contain unique values")
    cycle_uuids = [_id(value, "cycle", "cycleIds") for value in cycle_ids or ()]
    case_uuids = [_id(value, "case", "testCaseIds") for value in test_case_ids or ()]
    item_uuids = [
        _id(value, "cycle_item", "cycleItemIds") for value in cycle_item_ids or ()
    ]
    attempt_uuids = [
        _id(value, "attempt", "executionAttemptIds")
        for value in execution_attempt_ids or ()
    ]
    filters: list[Any] = [Issue.workspace_id == DEFAULT_WORKSPACE_ID]
    if deleted_only:
        filters.append(Issue.deleted_at.is_not(None))
    elif not include_deleted:
        filters.append(Issue.deleted_at.is_(None))
    if search:
        pattern = f"%{search.casefold()}%"
        filters.append(
            or_(func.lower(Issue.issue_key).like(pattern), func.lower(Issue.summary).like(pattern))
        )
    if statuses:
        filters.append(Issue.status.in_(statuses))
    if severities:
        filters.append(Issue.severity.in_(severities))
    if assignee_labels:
        filters.append(Issue.assignee_label.in_(assignee_labels))
    if case_uuids:
        filters.append(
            exists(select(1).where(IssueLink.issue_id == Issue.id, IssueLink.test_case_id.in_(case_uuids)))
        )
    if item_uuids:
        filters.append(
            exists(select(1).where(IssueLink.issue_id == Issue.id, IssueLink.cycle_item_id.in_(item_uuids)))
        )
    if attempt_uuids:
        filters.append(
            exists(
                select(1).where(
                    IssueLink.issue_id == Issue.id,
                    IssueLink.execution_attempt_id.in_(attempt_uuids),
                )
            )
        )
    if cycle_uuids:
        filters.append(
            exists(
                select(1)
                .select_from(IssueLink)
                .join(CycleItem, CycleItem.id == IssueLink.cycle_item_id)
                .where(IssueLink.issue_id == Issue.id, CycleItem.cycle_id.in_(cycle_uuids))
            )
        )
    fingerprint = _fingerprint(
        {
            "search": search,
            **{key: sorted(value or ()) for key, value in supplied.items()},
            "includeDeleted": include_deleted,
            "deletedOnly": deleted_only,
            "sort": sort,
        }
    )
    offset = _decode_cursor(cursor, fingerprint) if cursor else 0
    statement = select(Issue).where(*filters)
    severity_order = case(
        (Issue.severity == "CRITICAL", 4),
        (Issue.severity == "HIGH", 3),
        (Issue.severity == "MEDIUM", 2),
        else_=1,
    )
    ordering = {
        "UPDATED_AT_DESC": (Issue.updated_at.desc(), Issue.id.desc()),
        "UPDATED_AT_ASC": (Issue.updated_at.asc(), Issue.id.asc()),
        "SEVERITY_DESC": (severity_order.desc(), Issue.updated_at.desc(), Issue.id.desc()),
        "KEY_ASC": (Issue.issue_key.asc(), Issue.id.asc()),
    }[sort]
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = list(session.scalars(statement.order_by(*ordering).offset(offset).limit(limit + 1)))
    return IssuePage(
        items=[issue_list_item(session, row) for row in rows[:limit]],
        next_cursor=_encode_cursor(offset + limit, fingerprint) if len(rows) > limit else None,
        total=total,
    )


@router.post(
    "/api/issues",
    operation_id="createIssue",
    response_model=IssueResource,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def create_issue(
    payload: CreateIssueRequest,
    request: Request,
    session: SessionDependency,
    idempotency_header: IdempotencyHeader = None,
) -> IssueResource:
    key = _idempotency_key(payload.idempotencyKey, idempotency_header)
    context = audit_context(request, idempotency_key=key, reason="Initial creation")

    def mutate(_: AuditContext) -> IssueResource:
        repository = IssueRepository(session)
        aggregate = repository.create(
            summary=payload.summary,
            precondition=payload.precondition,
            steps=_steps(payload.steps),
            expected_result=payload.expectedResult,
            actual_result=payload.actualResult,
            severity=payload.severity,
            assignee_label=payload.assigneeLabel,
            audit=context,
        )
        for target in payload.initialLinks:
            _add_link(repository, aggregate.issue.id, target, context)
        return issue_resource(session, repository.get_aggregate(aggregate.issue.id))

    return _mutation(
        session,
        key,
        "CREATE_ISSUE",
        payload,
        IssueResource,
        context,
        mutate,
        audit_action="CREATE",
    )


@router.post(
    "/api/execution-attempts/{attemptId}/issues",
    operation_id="createIssueFromExecutionAttempt",
    response_model=IssueResource,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def create_issue_from_attempt(
    attempt_id: Annotated[str, Path(alias="attemptId")],
    payload: CreateIssueFromAttemptRequest,
    request: Request,
    session: SessionDependency,
    idempotency_header: IdempotencyHeader = None,
) -> IssueResource:
    decoded = _id(attempt_id, "attempt", "attemptId")
    key = _idempotency_key(payload.idempotencyKey, idempotency_header)
    context = audit_context(request, idempotency_key=key, reason="Created from execution")

    def mutate(_: AuditContext) -> IssueResource:
        repository = IssueRepository(session)
        aggregate = repository.create_from_attempt(
            attempt_id=decoded,
            severity=payload.severity,
            assignee_label=payload.assigneeLabel,
            audit=context,
        )
        source_link = next(
            link
            for link in repository.get_aggregate(aggregate.issue.id).links
            if link.execution_attempt_id == decoded
        )
        repository.add_link(
            issue_id=aggregate.issue.id,
            test_case_id=source_link.test_case_id,
            audit=context,
        )
        repository.add_link(
            issue_id=aggregate.issue.id,
            cycle_item_id=source_link.cycle_item_id,
            audit=context,
        )
        return issue_resource(session, repository.get_aggregate(aggregate.issue.id))

    return _mutation(
        session,
        key,
        "CREATE_ISSUE_FROM_ATTEMPT",
        payload,
        IssueResource,
        context,
        mutate,
        audit_action="CREATE_FROM_ATTEMPT",
        extra={"attemptId": attempt_id},
    )


@router.get(
    "/api/issues/{issueId}",
    operation_id="getIssue",
    response_model=IssueResource,
    responses=ERROR_RESPONSES,
)
def get_issue(
    issue_id: Annotated[str, Path(alias="issueId")],
    session: SessionDependency,
    include_deleted: Annotated[bool, Query(alias="includeDeleted")] = False,
) -> IssueResource:
    repository = IssueRepository(session)
    return issue_resource(
        session,
        repository.get_aggregate(
            _id(issue_id, "issue", "issueId"), include_deleted=include_deleted
        ),
    )


@router.patch(
    "/api/issues/{issueId}",
    operation_id="updateIssue",
    response_model=IssueResource,
    responses=ERROR_RESPONSES,
)
def update_issue(
    issue_id: Annotated[str, Path(alias="issueId")],
    payload: UpdateIssueRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> IssueResource:
    decoded = _id(issue_id, "issue", "issueId")
    context = audit_context(request, idempotency_key=idempotency_key, reason=payload.reason)

    def mutate(_: AuditContext) -> IssueResource:
        values: dict[str, Any] = {}
        mapping = {
            "summary": "summary",
            "precondition": "precondition",
            "expectedResult": "expected_result",
            "actualResult": "actual_result",
            "status": "status",
            "severity": "severity",
            "assigneeLabel": "assignee_label",
        }
        for wire, domain in mapping.items():
            if wire in payload.model_fields_set:
                values[domain] = getattr(payload, wire)
        if payload.steps is not None:
            values["steps"] = _steps(payload.steps)
        aggregate = IssueRepository(session).update(
            issue_id=decoded,
            base_resource_version=payload.baseResourceVersion,
            audit=context,
            **values,
        )
        return issue_resource(session, aggregate)

    return _mutation(
        session,
        idempotency_key,
        "UPDATE_ISSUE",
        payload,
        IssueResource,
        context,
        mutate,
        audit_action="TRANSITION" if payload.status is not None else "UPDATE",
        extra={"issueId": issue_id},
    )


@router.delete(
    "/api/issues/{issueId}",
    operation_id="deleteIssue",
    response_model=DeleteIssueResponse,
    responses=ERROR_RESPONSES,
)
def delete_issue(
    issue_id: Annotated[str, Path(alias="issueId")],
    payload: DeleteIssueRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> DeleteIssueResponse:
    decoded = _id(issue_id, "issue", "issueId")
    context = audit_context(request, idempotency_key=idempotency_key, reason=payload.reason)

    def mutate(_: AuditContext) -> DeleteIssueResponse:
        aggregate = IssueRepository(session).soft_delete(
            issue_id=decoded,
            base_resource_version=payload.baseResourceVersion,
            audit=context,
        )
        if aggregate.audit_id is None or aggregate.issue.deleted_at is None:
            raise RuntimeError("issue deletion did not produce its ledger records")
        return DeleteIssueResponse(
            operation="SOFT_DELETE",
            issue_id=issue_id,
            resource_version=aggregate.issue.resource_version,
            deleted_at=aggregate.issue.deleted_at,
            audit_id=encode_identifier("audit", aggregate.audit_id),
        )

    return _mutation(
        session,
        idempotency_key,
        "DELETE_ISSUE",
        payload,
        DeleteIssueResponse,
        context,
        mutate,
        audit_action="DELETE",
        extra={"issueId": issue_id},
    )


@router.post(
    "/api/issues/{issueId}/restore",
    operation_id="restoreIssue",
    response_model=RestoreIssueResponse,
    responses=ERROR_RESPONSES,
)
def restore_issue(
    issue_id: Annotated[str, Path(alias="issueId")],
    payload: RestoreIssueRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> RestoreIssueResponse:
    decoded = _id(issue_id, "issue", "issueId")
    context = audit_context(request, idempotency_key=idempotency_key, reason=payload.reason)

    def mutate(_: AuditContext) -> RestoreIssueResponse:
        aggregate = IssueRepository(session).restore(
            issue_id=decoded,
            base_resource_version=payload.baseResourceVersion,
            audit=context,
        )
        if aggregate.audit_id is None:
            raise RuntimeError("issue restore did not produce an audit row")
        return RestoreIssueResponse(
            operation="RESTORE",
            issue=issue_resource(session, aggregate),
            audit_id=encode_identifier("audit", aggregate.audit_id),
        )

    return _mutation(
        session,
        idempotency_key,
        "RESTORE_ISSUE",
        payload,
        RestoreIssueResponse,
        context,
        mutate,
        audit_action="RESTORE",
        extra={"issueId": issue_id},
    )


@router.delete(
    "/api/issues/{issueId}/permanent",
    operation_id="permanentlyDeleteIssue",
    response_model=PermanentDeleteIssueResponse,
    responses=ERROR_RESPONSES,
)
def permanently_delete_issue(
    issue_id: Annotated[str, Path(alias="issueId")],
    payload: PermanentDeleteIssueRequest,
    request: Request,
    session: SessionDependency,
    runtime: ObjectStorageRuntimeDependency,
) -> PermanentDeleteIssueResponse:
    decoded = _id(issue_id, "issue", "issueId")
    context = audit_context(request, idempotency_key=None, reason=payload.reason)
    with session.begin():
        issue = IssueRepository(session).get(
            decoded, for_update=True, include_deleted=True
        )
        if issue.resource_version != payload.baseResourceVersion:
            raise VersionConflictError(
                "ISSUE", issue.id, payload.baseResourceVersion, issue.resource_version
            )
        if issue.deleted_at is None:
            raise PermanentDeleteConflictError(
                "ISSUE", issue.id, "soft delete is required first"
            )
    deleted_attachment_count = delete_owned_attachment_objects(
        session,
        runtime,
        owner_type=AttachmentOwnerType.ISSUE,
        owner_ids=(decoded,),
        audit=context,
    )
    with session.begin():
        audit_id, metadata_count = IssueRepository(session).permanent_delete(
            issue_id=decoded,
            base_resource_version=payload.baseResourceVersion,
            audit=context,
        )
    if metadata_count != deleted_attachment_count:  # pragma: no cover
        raise RuntimeError("attachment metadata changed during permanent deletion")
    return PermanentDeleteIssueResponse(
        operation="PERMANENT_DELETE",
        issue_id=issue_id,
        deleted_attachment_count=metadata_count,
        audit_id=encode_identifier("audit", audit_id),
    )


@router.get(
    "/api/issues/{issueId}/links",
    operation_id="listIssueLinks",
    response_model=IssueLinkPage,
    responses=ERROR_RESPONSES,
)
def list_issue_links(
    issue_id: Annotated[str, Path(alias="issueId")],
    session: SessionDependency,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    include_deleted: Annotated[bool, Query(alias="includeDeleted")] = False,
) -> IssueLinkPage:
    aggregate = IssueRepository(session).get_aggregate(
        _id(issue_id, "issue", "issueId"), include_deleted=include_deleted
    )
    active_links = _active_links(session, aggregate.links)
    offset = _simple_offset(cursor)
    page = active_links[offset : offset + limit]
    return IssueLinkPage(
        items=[issue_link_resource(session, link) for link in page],
        next_cursor=_simple_cursor(offset + limit) if offset + limit < len(active_links) else None,
        total=len(active_links),
    )


@router.post(
    "/api/issues/{issueId}/links",
    operation_id="createIssueLink",
    response_model=IssueLinkResource,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def create_issue_link(
    issue_id: Annotated[str, Path(alias="issueId")],
    payload: CreateIssueLinkRequest,
    request: Request,
    session: SessionDependency,
    idempotency_header: IdempotencyHeader = None,
) -> IssueLinkResource:
    decoded = _id(issue_id, "issue", "issueId")
    key = _idempotency_key(payload.idempotencyKey, idempotency_header)
    context = audit_context(request, idempotency_key=key)

    def mutate(_: AuditContext) -> IssueLinkResource:
        link = _add_link(IssueRepository(session), decoded, payload.target, context)
        return issue_link_resource(session, link, actor_label=context.actor_label)

    return _mutation(
        session,
        key,
        "CREATE_ISSUE_LINK",
        payload,
        IssueLinkResource,
        context,
        mutate,
        audit_action="LINK",
        extra={"issueId": issue_id},
    )


@router.delete(
    "/api/issues/{issueId}/links/{issueLinkId}",
    operation_id="deleteIssueLink",
    response_model=DeleteIssueLinkResponse,
    responses=ERROR_RESPONSES,
)
def delete_issue_link(
    issue_id: Annotated[str, Path(alias="issueId")],
    issue_link_id: Annotated[str, Path(alias="issueLinkId")],
    payload: DeleteIssueLinkRequest,
    request: Request,
    session: SessionDependency,
) -> DeleteIssueLinkResponse:
    decoded_issue = _id(issue_id, "issue", "issueId")
    decoded_link = _id(issue_link_id, "issue_link", "issueLinkId")
    context = audit_context(request, idempotency_key=None, reason=payload.reason)
    with session.begin():
        repository = IssueRepository(session)
        issue = repository.get(decoded_issue, for_update=True)
        if issue.resource_version != payload.baseIssueResourceVersion:
            raise VersionConflictError(
                "ISSUE",
                issue.id,
                payload.baseIssueResourceVersion,
                issue.resource_version,
            )
        link = session.scalar(
            select(IssueLink)
            .where(IssueLink.id == decoded_link, IssueLink.issue_id == issue.id)
            .with_for_update()
        )
        if link is None:
            raise ResourceNotFoundError("ISSUE_LINK", decoded_link)
        already_unlinked = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.entity_type == "ISSUE",
                AuditLog.entity_id == issue.id,
                AuditLog.action == "UNLINK",
                AuditLog.after["link"]["id"].as_string() == str(link.id),
            )
        )
        if already_unlinked:
            raise ResourceNotFoundError("ISSUE_LINK", decoded_link)
        before = repository._snapshot(issue)
        link_snapshot = repository._link_snapshot(link)
        deleted_at = datetime.now(UTC)
        issue.resource_version += 1
        issue.updated_at = deleted_at
        session.flush()
        audit_id = append_audit(
            session,
            workspace_id=DEFAULT_WORKSPACE_ID,
            entity_type="ISSUE",
            entity_id=issue.id,
            action="UNLINK",
            before=before,
            after={**repository._snapshot(issue), "link": link_snapshot},
            context=context,
        )
    return DeleteIssueLinkResponse(
        issue_id=issue_id,
        issue_link_id=issue_link_id,
        issue_resource_version=issue.resource_version,
        deleted_at=deleted_at,
        audit_id=encode_identifier("audit", audit_id),
    )


@router.get(
    "/api/issues/{issueId}/comments",
    operation_id="listIssueComments",
    response_model=IssueCommentPage,
    responses=ERROR_RESPONSES,
)
def list_issue_comments(
    issue_id: Annotated[str, Path(alias="issueId")],
    session: SessionDependency,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    include_deleted: Annotated[bool, Query(alias="includeDeleted")] = False,
) -> IssueCommentPage:
    aggregate = IssueRepository(session).get_aggregate(
        _id(issue_id, "issue", "issueId"), include_deleted=include_deleted
    )
    offset = _simple_offset(cursor)
    page = aggregate.comments[offset : offset + limit]
    return IssueCommentPage(
        items=[issue_comment_resource(comment) for comment in page],
        next_cursor=_simple_cursor(offset + limit) if offset + limit < len(aggregate.comments) else None,
        total=len(aggregate.comments),
    )


@router.post(
    "/api/issues/{issueId}/comments",
    operation_id="createIssueComment",
    response_model=IssueCommentResource,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def create_issue_comment(
    issue_id: Annotated[str, Path(alias="issueId")],
    payload: CreateIssueCommentRequest,
    request: Request,
    session: SessionDependency,
    idempotency_header: IdempotencyHeader = None,
) -> IssueCommentResource:
    decoded = _id(issue_id, "issue", "issueId")
    key = _idempotency_key(payload.idempotencyKey, idempotency_header)
    context = audit_context(request, idempotency_key=key)

    def mutate(_: AuditContext) -> IssueCommentResource:
        comment = IssueRepository(session).add_comment(
            issue_id=decoded,
            body=payload.body,
            author_label=context.actor_label,
            audit=context,
        )
        return issue_comment_resource(comment)

    return _mutation(
        session,
        key,
        "CREATE_ISSUE_COMMENT",
        payload,
        IssueCommentResource,
        context,
        mutate,
        audit_action="COMMENT",
        extra={"issueId": issue_id},
    )


@router.get(
    "/api/issues/{issueId}/history",
    operation_id="listIssueHistory",
    response_model=IssueHistoryPage,
    responses=ERROR_RESPONSES,
)
def list_issue_history(
    issue_id: Annotated[str, Path(alias="issueId")],
    session: SessionDependency,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    include_deleted: Annotated[bool, Query(alias="includeDeleted")] = False,
) -> IssueHistoryPage:
    decoded = _id(issue_id, "issue", "issueId")
    IssueRepository(session).get(decoded, include_deleted=include_deleted)
    issue_rows = list(
        session.scalars(
            select(AuditLog)
            .where(
                AuditLog.entity_type == "ISSUE",
                AuditLog.entity_id == decoded,
                AuditLog.action != "IDEMPOTENCY_RECORDED",
            )
            .order_by(AuditLog.occurred_at, AuditLog.id)
        )
    )
    attachment_rows = list(
        session.scalars(
            select(AuditLog)
            .join(Attachment, Attachment.id == AuditLog.entity_id)
            .where(
                AuditLog.entity_type == "ATTACHMENT",
                AuditLog.action.in_(("CREATE", "DELETE_REQUEST")),
                Attachment.owner_type == "ISSUE",
                Attachment.owner_id == decoded,
            )
        )
    )
    rows = sorted(
        (*issue_rows, *attachment_rows),
        key=lambda row: (row.occurred_at, row.id),
    )
    offset = _simple_offset(cursor)
    return IssueHistoryPage(
        items=[
            issue_history_event(row, issue_id=decoded)
            for row in rows[offset : offset + limit]
        ],
        next_cursor=_simple_cursor(offset + limit) if offset + limit < len(rows) else None,
        total=len(rows),
    )


def _add_link(
    repository: IssueRepository,
    issue_id: uuid.UUID,
    target: Any,
    context: AuditContext,
) -> IssueLink:
    if target.type == "TEST_CASE":
        return repository.add_link(
            issue_id=issue_id,
            test_case_id=_id(target.testCaseId, "case", "target.testCaseId"),
            audit=context,
        )
    if target.type == "CYCLE_ITEM":
        return repository.add_link(
            issue_id=issue_id,
            cycle_item_id=_id(target.cycleItemId, "cycle_item", "target.cycleItemId"),
            audit=context,
        )
    return repository.add_link(
        issue_id=issue_id,
        execution_attempt_id=_id(
            target.executionAttemptId, "attempt", "target.executionAttemptId"
        ),
        audit=context,
    )


def _steps(values: list[Any]) -> tuple[IssueStepInput, ...]:
    return tuple(
        IssueStepInput(action=value.action, expected_result=value.expected_result)
        for value in values
    )


def _active_links(session: Session, links: tuple[IssueLink, ...]) -> tuple[IssueLink, ...]:
    if not links:
        return ()
    removed_ids: set[str] = set()
    rows = session.scalars(
        select(AuditLog).where(
            AuditLog.entity_type == "ISSUE",
            AuditLog.entity_id == links[0].issue_id,
            AuditLog.action == "UNLINK",
        )
    )
    for row in rows:
        snapshot = (row.after or {}).get("link")
        if isinstance(snapshot, dict) and isinstance(snapshot.get("id"), str):
            removed_ids.add(snapshot["id"])
    return tuple(link for link in links if str(link.id) not in removed_ids)


def _id(value: str, prefix: str, field: str) -> uuid.UUID:
    return decode_identifier(value, prefix=prefix, field=field)


def _idempotency_key(body: str, header: str | None) -> str:
    if header is not None and header != body:
        raise ValueError("Idempotency-Key header must match body idempotencyKey")
    return body


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
    extra: dict[str, Any] | None = None,
) -> Any:
    normalized = payload.model_dump(mode="json", by_alias=True)
    normalized.update(extra or {})
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation=operation,
            payload=normalized,
            response_model=response_model,
            context=context,
            action=mutate,
            audit_action=audit_action,
        )


def _fingerprint(values: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _encode_cursor(offset: int, fingerprint: str) -> str:
    raw = json.dumps({"offset": offset, "filter": fingerprint}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str, fingerprint: str) -> int:
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if (
            not isinstance(value, dict)
            or value.get("filter") != fingerprint
            or not isinstance(value.get("offset"), int)
            or value["offset"] < 0
        ):
            raise ValueError
        return value["offset"]
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError) as error:
        raise CycleCursorError("issue cursor is invalid") from error


def _simple_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def _simple_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        value = int(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if value < 0:
            raise ValueError
        return value
    except (ValueError, UnicodeDecodeError, binascii.Error) as error:
        raise CycleCursorError("issue cursor is invalid") from error
