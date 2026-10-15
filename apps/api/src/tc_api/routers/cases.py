"""Sprint 2 test-case ledger endpoints."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import unicodedata
import uuid
from dataclasses import replace
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Header, Path, Query, Request, status
from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from tc_api.dependencies import SessionDependency
from tc_api.errors import (
    ExpectedResultModeError,
    LabelConflictError,
    TestCaseCursorError,
)
from tc_api.idempotency import execute_idempotent
from tc_api.ids import decode_identifier, encode_identifier
from tc_api.models import (
    CopyTestCaseRequest,
    CopyTestCaseResponse,
    ErrorResponse,
    FieldDiffResponse,
    MoveTestCaseRequest,
    MoveTestCaseResponse,
    PermanentDeleteTestCaseRequest,
    PermanentDeleteTestCaseResponse,
    RestoreDeletedTestCaseRequest,
    RestoreDeletedTestCaseResponse,
    RestoreTestCaseVersionRequest,
    RestoreTestCaseVersionResponse,
    SoftDeleteTestCaseRequest,
    SoftDeleteTestCaseResponse,
    StepDiffResponse,
    TestCasePageResponse,
    TestCaseResourceResponse,
    TestCaseVersionDiffResponse,
    TestCaseVersionPageResponse,
    TestCaseVersionResponse,
    TestStepResponse,
    UpdateTestCaseRequest,
    CreateTestCaseRequest,
)
from tc_api.serializers import (
    audit_context,
    case_list_item_response,
    case_resource_response,
    copied_from_response,
    version_response,
)
from tc_backend.db.audit import AuditContext
from tc_backend.db.cases import CaseAggregate, CaseRepository, TestCaseContent, TestStepInput
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.errors import VersionConflictError
from tc_backend.db.models import (
    ExpectedResultMode,
    Tag,
    TestCase,
    TestCasePriority,
    TestCaseSourceType,
    TestCaseStatus,
    TestCaseTag,
    TestCaseVersion,
    WorkItem,
)

router = APIRouter(prefix="/api/test-cases", tags=["test-cases"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Test case or work item not found"},
    409: {"model": ErrorResponse, "description": "Version or mutation conflict"},
    422: {"model": ErrorResponse, "description": "Validation or cursor error"},
}
IdempotencyHeader = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


@router.get(
    "",
    operation_id="listTestCases",
    response_model=TestCasePageResponse,
    responses=ERROR_RESPONSES,
)
def list_test_cases(
    session: SessionDependency,
    search: Annotated[str | None, Query(min_length=1, max_length=200, pattern=r"\S")] = None,
    category_ids: Annotated[list[str] | None, Query(alias="categoryIds")] = None,
    work_item_ids: Annotated[list[str] | None, Query(alias="workItemIds")] = None,
    statuses: Annotated[list[Literal["ACTIVE", "DEPRECATED"]] | None, Query()] = None,
    priorities: Annotated[
        list[Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]] | None, Query()
    ] = None,
    labels: Annotated[list[str] | None, Query(max_length=50)] = None,
    label_match: Annotated[Literal["ANY", "ALL"], Query(alias="labelMatch")] = "ANY",
    include_deleted: Annotated[bool, Query(alias="includeDeleted")] = False,
    deleted_only: Annotated[bool, Query(alias="deletedOnly")] = False,
    sort: Literal["WORK_ITEM_ORDER", "UPDATED_AT_DESC", "KEY_ASC"] = "WORK_ITEM_ORDER",
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TestCasePageResponse:
    if label_match != "ANY" and not labels:
        raise ValueError("labelMatch requires labels")
    for field_name, values in (
        ("categoryIds", category_ids),
        ("workItemIds", work_item_ids),
        ("statuses", statuses),
        ("priorities", priorities),
    ):
        if values is not None and len(values) != len(set(values)):
            raise ValueError(f"{field_name} must contain unique values")
    category_uuids = [_id(value, "folder", "categoryIds") for value in category_ids or []]
    work_item_uuids = [_id(value, "work_item", "workItemIds") for value in work_item_ids or []]
    normalized_labels = [value.casefold() for value in _validated_labels(labels or [])]
    fingerprint = _filter_fingerprint(
        search=search,
        category_ids=sorted(category_ids or []),
        work_item_ids=sorted(work_item_ids or []),
        statuses=sorted(statuses or []),
        priorities=sorted(priorities or []),
        labels=sorted(normalized_labels),
        label_match=label_match,
        include_deleted=include_deleted,
        deleted_only=deleted_only,
        sort=sort,
    )
    offset = _decode_cursor(cursor, fingerprint) if cursor is not None else 0

    current = TestCaseVersion
    filters: list[Any] = [TestCase.workspace_id == DEFAULT_WORKSPACE_ID]
    if deleted_only:
        filters.append(TestCase.deleted_at.is_not(None))
    elif not include_deleted:
        filters.append(TestCase.deleted_at.is_(None))
    if category_uuids:
        filters.append(TestCase.category_id.in_(category_uuids))
    if work_item_uuids:
        filters.append(TestCase.work_item_id.in_(work_item_uuids))
    if statuses:
        filters.append(TestCase.status.in_(statuses))
    if priorities:
        filters.append(current.priority.in_(priorities))
    if search:
        for token in search.casefold().split():
            pattern = f"%{token}%"
            filters.append(or_(func.lower(TestCase.case_key).like(pattern), func.lower(current.summary).like(pattern)))
    if normalized_labels:
        label_predicates = [
            exists(
                select(1)
                .select_from(TestCaseTag)
                .join(Tag, Tag.id == TestCaseTag.tag_id)
                .where(TestCaseTag.test_case_id == TestCase.id, Tag.normalized_name == label)
            )
            for label in normalized_labels
        ]
        filters.append(and_(*label_predicates) if label_match == "ALL" else or_(*label_predicates))

    base = (
        select(TestCase, WorkItem)
        .join(current, current.id == TestCase.current_version_id)
        .join(WorkItem, WorkItem.id == TestCase.work_item_id)
        .where(*filters)
    )
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    ordered = _case_order(base, sort)
    rows = session.execute(ordered.offset(offset).limit(limit + 1)).all()
    page = rows[:limit]
    repository = CaseRepository(session)
    return TestCasePageResponse(
        items=[
            case_list_item_response(
                repository.get_aggregate(
                    row.TestCase.id,
                    include_deleted=include_deleted or deleted_only,
                ),
                row.WorkItem,
            )
            for row in page
        ],
        next_cursor=(
            _encode_cursor(offset + limit, fingerprint) if len(rows) > limit else None
        ),
        total=total,
    )


@router.post(
    "",
    operation_id="createTestCase",
    response_model=TestCaseResourceResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def create_test_case(
    payload: CreateTestCaseRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> TestCaseResourceResponse:
    work_item_id = _id(payload.workItemId, "work_item", "workItemId")
    content = _content(payload, status_value="ACTIVE")
    context = audit_context(request, idempotency_key=idempotency_key, reason="Initial creation")

    def mutate(_: AuditContext) -> TestCaseResourceResponse:
        aggregate = CaseRepository(session).create_case(
            work_item_id=work_item_id,
            content=content,
            change_reason="Initial creation",
            audit=context,
        )
        return case_resource_response(session, aggregate)

    return _idempotent_mutation(
        session, idempotency_key, "CREATE_TEST_CASE", payload, TestCaseResourceResponse, context, mutate, "CREATE"
    )


@router.get(
    "/{caseId}",
    operation_id="getTestCase",
    response_model=TestCaseResourceResponse,
    responses=ERROR_RESPONSES,
)
def get_test_case(
    case_id: Annotated[str, Path(alias="caseId")],
    session: SessionDependency,
    include_deleted: Annotated[bool, Query(alias="includeDeleted")] = False,
) -> TestCaseResourceResponse:
    return case_resource_response(
        session,
        CaseRepository(session).get_aggregate(
            _case_id(case_id), include_deleted=include_deleted
        ),
    )


@router.patch(
    "/{caseId}",
    operation_id="updateTestCase",
    response_model=TestCaseResourceResponse,
    responses=ERROR_RESPONSES,
)
def update_test_case(
    case_id: Annotated[str, Path(alias="caseId")],
    payload: UpdateTestCaseRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> TestCaseResourceResponse:
    decoded = _case_id(case_id)
    context = audit_context(request, idempotency_key=idempotency_key, reason=payload.changeReason)

    def mutate(_: AuditContext) -> TestCaseResourceResponse:
        repository = CaseRepository(session)
        repository._lock_library()  # Hold the aggregate lock across content-version translation.
        current = repository.get_aggregate(decoded)
        if current.current_version.version_number != payload.baseVersion:
            raise VersionConflictError(
                "TEST_CASE", decoded, payload.baseVersion, current.current_version.version_number
            )
        aggregate = repository.update_case(
            test_case_id=decoded,
            base_version=current.test_case.version,
            content=_content(payload, status_value=payload.status or current.test_case.status),
            change_reason=payload.changeReason,
            audit=context,
        )
        return case_resource_response(session, aggregate)

    return _idempotent_case_mutation(
        session, idempotency_key, "UPDATE_TEST_CASE", case_id, payload, TestCaseResourceResponse, context, mutate
    )


@router.get(
    "/{caseId}/versions/diff",
    operation_id="diffTestCaseVersions",
    response_model=TestCaseVersionDiffResponse,
    responses=ERROR_RESPONSES,
)
def diff_test_case_versions(
    case_id: Annotated[str, Path(alias="caseId")],
    session: SessionDependency,
    from_version: Annotated[int, Query(alias="fromVersion", ge=1)],
    to_version: Annotated[int, Query(alias="toVersion", ge=1)],
) -> TestCaseVersionDiffResponse:
    if from_version == to_version:
        raise ValueError("fromVersion and toVersion must differ")
    repository = CaseRepository(session)
    decoded = _case_id(case_id)
    before = repository.get_version(decoded, from_version)
    after = repository.get_version(decoded, to_version)
    before_fields = _diff_fields(before)
    after_fields = _diff_fields(after)
    return TestCaseVersionDiffResponse(
        test_case_id=case_id,
        from_version=from_version,
        to_version=to_version,
        field_changes=[
            FieldDiffResponse(field=cast(Any, name), before=before_fields[name], after=after_fields[name])
            for name in before_fields
            if before_fields[name] != after_fields[name]
        ],
        step_changes=_step_diff(before.steps, after.steps),
    )


@router.get(
    "/{caseId}/versions",
    operation_id="listTestCaseVersions",
    response_model=TestCaseVersionPageResponse,
    responses=ERROR_RESPONSES,
)
def list_test_case_versions(
    case_id: Annotated[str, Path(alias="caseId")],
    session: SessionDependency,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TestCaseVersionPageResponse:
    decoded = _case_id(case_id)
    fingerprint = hashlib.sha256(case_id.encode()).hexdigest()
    offset = _decode_cursor(cursor, fingerprint) if cursor else 0
    versions = CaseRepository(session).list_versions(decoded)
    page = versions[offset : offset + limit]
    return TestCaseVersionPageResponse(
        items=[version_response(session, item) for item in page],
        next_cursor=(
            _encode_cursor(offset + limit, fingerprint)
            if offset + limit < len(versions)
            else None
        ),
        total=len(versions),
    )


@router.get(
    "/{caseId}/versions/{version}",
    operation_id="getTestCaseVersion",
    response_model=TestCaseVersionResponse,
    responses=ERROR_RESPONSES,
)
def get_test_case_version(
    case_id: Annotated[str, Path(alias="caseId")],
    version: Annotated[int, Path(ge=1)],
    session: SessionDependency,
) -> TestCaseVersionResponse:
    return version_response(session, CaseRepository(session).get_version(_case_id(case_id), version))


@router.post(
    "/{caseId}/restore/{version}",
    operation_id="restoreTestCaseVersion",
    response_model=RestoreTestCaseVersionResponse,
    responses=ERROR_RESPONSES,
)
def restore_test_case_version(
    case_id: Annotated[str, Path(alias="caseId")],
    version: Annotated[int, Path(ge=1)],
    payload: RestoreTestCaseVersionRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> RestoreTestCaseVersionResponse:
    decoded = _case_id(case_id)
    context = audit_context(request, idempotency_key=idempotency_key, reason=payload.changeReason)

    def mutate(_: AuditContext) -> RestoreTestCaseVersionResponse:
        repository = CaseRepository(session)
        repository._lock_library()
        current = repository.get_aggregate(decoded)
        if current.current_version.version_number != payload.baseVersion:
            raise VersionConflictError(
                "TEST_CASE", decoded, payload.baseVersion, current.current_version.version_number
            )
        aggregate = repository.restore_version(
            test_case_id=decoded,
            base_version=current.test_case.version,
            restore_version=version,
            change_reason=payload.changeReason,
            audit=context,
        )
        return RestoreTestCaseVersionResponse(
            test_case=case_resource_response(session, aggregate),
            restored_from_version=version,
            audit_id=encode_identifier("audit", _audit_id(aggregate)),
        )

    return _idempotent_case_mutation(
        session, idempotency_key, "RESTORE_TEST_CASE_VERSION", case_id, payload, RestoreTestCaseVersionResponse, context, mutate,
        extra={"version": version},
    )


@router.post(
    "/{caseId}/move",
    operation_id="moveTestCase",
    response_model=MoveTestCaseResponse,
    responses=ERROR_RESPONSES,
)
def move_test_case(
    case_id: Annotated[str, Path(alias="caseId")],
    payload: MoveTestCaseRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> MoveTestCaseResponse:
    decoded = _case_id(case_id)
    target = _id(payload.targetWorkItemId, "work_item", "targetWorkItemId")
    context = audit_context(request, idempotency_key=idempotency_key, reason=payload.changeReason)

    def mutate(_: AuditContext) -> MoveTestCaseResponse:
        repository = CaseRepository(session)
        repository._lock_library()
        previous = repository.get_aggregate(decoded)
        previous_work_item_id = previous.test_case.work_item_id
        previous_category_id = previous.test_case.category_id
        previous_sort_order = previous.test_case.sort_order
        aggregate = repository.move_case(
            test_case_id=decoded,
            base_version=payload.baseResourceVersion,
            target_work_item_id=target,
            sort_order=payload.targetSortOrder,
            audit=context,
        )
        return MoveTestCaseResponse(
            operation="MOVE",
            test_case=case_resource_response(session, aggregate),
            previous_work_item_id=encode_identifier("work_item", previous_work_item_id),
            previous_category_id=encode_identifier("folder", previous_category_id),
            previous_sort_order=previous_sort_order,
            audit_id=(
                encode_identifier("audit", aggregate.audit_id)
                if aggregate.audit_id is not None
                else None
            ),
        )

    return _idempotent_case_mutation(
        session, idempotency_key, "MOVE_TEST_CASE", case_id, payload, MoveTestCaseResponse, context, mutate
    )


@router.post(
    "/{caseId}/copy",
    operation_id="copyTestCase",
    response_model=CopyTestCaseResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def copy_test_case(
    case_id: Annotated[str, Path(alias="caseId")],
    payload: CopyTestCaseRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> CopyTestCaseResponse:
    decoded = _case_id(case_id)
    target = _id(payload.targetWorkItemId, "work_item", "targetWorkItemId")
    context = audit_context(request, idempotency_key=idempotency_key, reason=payload.changeReason)

    def mutate(_: AuditContext) -> CopyTestCaseResponse:
        repository = CaseRepository(session)
        if payload.summary is None:
            aggregate = repository.copy_case(
                source_test_case_id=decoded,
                target_work_item_id=target,
                source_version=payload.sourceVersion,
                sort_order=payload.targetSortOrder,
                change_reason=payload.changeReason,
                audit=context,
            )
        else:
            repository._lock_library()
            repository._get_case(decoded, for_update=True)
            source = repository.get_version(decoded, payload.sourceVersion)
            target_row = repository.get_work_item(target, for_update=True)
            aggregate = repository._create_case_locked(
                work_item=target_row,
                content=replace(_content_from_snapshot(source), summary=payload.summary),
                change_reason=payload.changeReason,
                audit=context,
                sort_order=payload.targetSortOrder,
                copied_from_version_id=source.id,
            )
        copied_from = copied_from_response(session, aggregate.current_version)
        return CopyTestCaseResponse(
            operation="COPY",
            test_case=case_resource_response(session, aggregate),
            copied_from=copied_from,
            audit_id=encode_identifier("audit", _audit_id(aggregate)),
        )

    return _idempotent_case_mutation(
        session, idempotency_key, "COPY_TEST_CASE", case_id, payload, CopyTestCaseResponse, context, mutate,
        audit_action="COPY",
    )


@router.delete(
    "/{caseId}",
    operation_id="softDeleteTestCase",
    response_model=SoftDeleteTestCaseResponse,
    responses=ERROR_RESPONSES,
)
def soft_delete_test_case(
    case_id: Annotated[str, Path(alias="caseId")],
    payload: SoftDeleteTestCaseRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> SoftDeleteTestCaseResponse:
    decoded = _case_id(case_id)
    context = audit_context(request, idempotency_key=idempotency_key, reason=payload.reason)

    def mutate(_: AuditContext) -> SoftDeleteTestCaseResponse:
        aggregate = CaseRepository(session).soft_delete(
            test_case_id=decoded,
            base_version=payload.baseResourceVersion,
            audit=context,
        )
        return SoftDeleteTestCaseResponse(
            operation="SOFT_DELETE",
            test_case=case_resource_response(session, aggregate),
            audit_id=encode_identifier("audit", _audit_id(aggregate)),
        )

    return _idempotent_case_mutation(
        session, idempotency_key, "SOFT_DELETE_TEST_CASE", case_id, payload, SoftDeleteTestCaseResponse, context, mutate
    )


@router.post(
    "/{caseId}/restore",
    operation_id="restoreDeletedTestCase",
    response_model=RestoreDeletedTestCaseResponse,
    responses=ERROR_RESPONSES,
)
def restore_deleted_test_case(
    case_id: Annotated[str, Path(alias="caseId")],
    payload: RestoreDeletedTestCaseRequest,
    request: Request,
    session: SessionDependency,
    idempotency_key: IdempotencyHeader = None,
) -> RestoreDeletedTestCaseResponse:
    decoded = _case_id(case_id)
    target = (
        _id(payload.targetWorkItemId, "work_item", "targetWorkItemId")
        if payload.targetWorkItemId
        else None
    )
    context = audit_context(request, idempotency_key=idempotency_key, reason=payload.reason)

    def mutate(_: AuditContext) -> RestoreDeletedTestCaseResponse:
        aggregate = CaseRepository(session).restore_deleted(
            test_case_id=decoded,
            base_version=payload.baseResourceVersion,
            target_work_item_id=target,
            sort_order=payload.targetSortOrder,
            audit=context,
        )
        return RestoreDeletedTestCaseResponse(
            operation="RESTORE_DELETED",
            test_case=case_resource_response(session, aggregate),
            audit_id=encode_identifier("audit", _audit_id(aggregate)),
        )

    return _idempotent_case_mutation(
        session, idempotency_key, "RESTORE_DELETED_TEST_CASE", case_id, payload, RestoreDeletedTestCaseResponse, context, mutate
    )


@router.delete(
    "/{caseId}/permanent",
    operation_id="permanentlyDeleteTestCase",
    response_model=PermanentDeleteTestCaseResponse,
    responses=ERROR_RESPONSES,
)
def permanently_delete_test_case(
    case_id: Annotated[str, Path(alias="caseId")],
    payload: PermanentDeleteTestCaseRequest,
    request: Request,
    session: SessionDependency,
) -> PermanentDeleteTestCaseResponse:
    decoded = _case_id(case_id)
    context = audit_context(request, idempotency_key=None, reason=payload.reason)
    with session.begin():
        audit_id = CaseRepository(session).permanent_delete(
            test_case_id=decoded,
            base_version=payload.baseResourceVersion,
            audit=context,
        )
    return PermanentDeleteTestCaseResponse(
        operation="PERMANENT_DELETE",
        test_case_id=case_id,
        deleted_attachment_count=0,
        audit_id=encode_identifier("audit", audit_id),
    )


def _content(payload: CreateTestCaseRequest | UpdateTestCaseRequest, *, status_value: str) -> TestCaseContent:
    labels = _validated_labels(payload.labels)
    if payload.expectedResultMode == "CASE" and payload.source.type != "WORKER":
        raise ExpectedResultModeError(payload.expectedResultMode, payload.source.type)
    metadata = payload.source.model_dump(mode="json", exclude_none=True)
    return TestCaseContent(
        summary=payload.summary,
        precondition=payload.precondition,
        steps=tuple(
            TestStepInput(
                action=step.action,
                expected_result=step.expectedResult,
                precondition=step.precondition,
            )
            for step in payload.steps
        ),
        expected_result_mode=ExpectedResultMode(payload.expectedResultMode),
        expected_result=payload.caseExpectedResult,
        status=TestCaseStatus(status_value),
        priority=TestCasePriority(payload.priority),
        assignee_label=payload.ownerLabel,
        tag_names=tuple(labels),
        source_type=_backend_source(payload.source.type),
        source_metadata=metadata,
        generation_reason=payload.generationReason,
    )


def _content_from_snapshot(snapshot: Any) -> TestCaseContent:
    return TestCaseContent(
        summary=snapshot.summary,
        precondition=snapshot.precondition,
        steps=tuple(
            TestStepInput(
                action=step.action,
                expected_result=step.expected_result,
                precondition=step.precondition,
            )
            for step in snapshot.steps
        ),
        expected_result_mode=ExpectedResultMode(snapshot.expected_result_mode),
        expected_result=snapshot.expected_result,
        status=TestCaseStatus(snapshot.status),
        priority=TestCasePriority(snapshot.priority),
        assignee_label=snapshot.assignee_label,
        tag_names=snapshot.tag_names,
        source_type=TestCaseSourceType(snapshot.source_type),
        source_metadata=snapshot.source_metadata,
        generation_reason=snapshot.generation_reason,
    )


def _backend_source(source: str) -> TestCaseSourceType:
    return {
        "UI": TestCaseSourceType.MANUAL,
        "API": TestCaseSourceType.API,
        "SYSTEM": TestCaseSourceType.FILE_IMPORT,
        "SLACK": TestCaseSourceType.SLACK_AI,
        "WORKER": TestCaseSourceType.SLACK_AI,
    }[source]


def _validated_labels(values: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    result: list[str] = []
    for raw in values:
        clean = " ".join(unicodedata.normalize("NFKC", raw).strip().split())
        if not clean or len(clean) > 100:
            raise ValueError("labels must be non-blank and at most 100 characters")
        normalized = clean.casefold()
        if normalized in seen:
            raise LabelConflictError([seen[normalized], raw], normalized)
        seen[normalized] = raw
        result.append(clean)
    return result


def _case_order(statement: Select[Any], sort: str) -> Select[Any]:
    if sort == "UPDATED_AT_DESC":
        return statement.order_by(TestCase.updated_at.desc(), TestCase.id.desc())
    if sort == "KEY_ASC":
        return statement.order_by(TestCase.case_key.asc(), TestCase.id.asc())
    return statement.order_by(
        WorkItem.category_id.asc(), WorkItem.work_item_key.asc(), TestCase.sort_order.asc(), TestCase.id.asc()
    )


def _filter_fingerprint(**values: Any) -> str:
    canonical = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _encode_cursor(offset: int, fingerprint: str) -> str:
    raw = json.dumps({"offset": offset, "filter": fingerprint}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(value: str, fingerprint: str) -> int:
    try:
        padded = value + "=" * (-len(value) % 4)
        data = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if (
            not isinstance(data, dict)
            or data.get("filter") != fingerprint
            or not isinstance(data.get("offset"), int)
            or data["offset"] < 0
        ):
            raise ValueError
        return data["offset"]
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError) as error:
        raise TestCaseCursorError("test-case cursor is invalid") from error


def _diff_fields(snapshot: Any) -> dict[str, Any]:
    return {
        "summary": snapshot.summary,
        "precondition": snapshot.precondition,
        "expectedResultMode": snapshot.expected_result_mode,
        "caseExpectedResult": snapshot.expected_result,
        "priority": snapshot.priority,
        "ownerLabel": snapshot.assignee_label,
        "labels": list(snapshot.tag_names),
    }


def _step(value: Any) -> TestStepResponse:
    return TestStepResponse(
        sequence=value.sequence,
        precondition=value.precondition,
        action=value.action,
        expected_result=value.expected_result,
    )


def _step_diff(before: tuple[Any, ...], after: tuple[Any, ...]) -> list[StepDiffResponse]:
    changes: list[StepDiffResponse] = []
    before_by_content = {
        (item.precondition, item.action, item.expected_result): item for item in before
    }
    after_by_content = {
        (item.precondition, item.action, item.expected_result): item for item in after
    }
    moved_before: set[int] = set()
    moved_after: set[int] = set()
    for content, old in before_by_content.items():
        new = after_by_content.get(content)
        if new is not None and new.sequence != old.sequence:
            changes.append(StepDiffResponse(change_type="MOVED", before=_step(old), after=_step(new)))
            moved_before.add(old.sequence)
            moved_after.add(new.sequence)
    before_by_sequence = {item.sequence: item for item in before if item.sequence not in moved_before}
    after_by_sequence = {item.sequence: item for item in after if item.sequence not in moved_after}
    for sequence in sorted(before_by_sequence.keys() | after_by_sequence.keys()):
        old = before_by_sequence.get(sequence)
        new = after_by_sequence.get(sequence)
        if old is None:
            changes.append(StepDiffResponse(change_type="ADDED", before=None, after=_step(new)))
        elif new is None:
            changes.append(StepDiffResponse(change_type="REMOVED", before=_step(old), after=None))
        elif (old.action, old.expected_result) != (new.action, new.expected_result):
            changes.append(StepDiffResponse(change_type="CHANGED", before=_step(old), after=_step(new)))
    return changes


def _case_id(value: str) -> uuid.UUID:
    return _id(value, "case", "caseId")


def _id(value: str, prefix: str, field: str) -> uuid.UUID:
    return decode_identifier(value, prefix=prefix, field=field)


def _audit_id(aggregate: CaseAggregate) -> uuid.UUID:
    if aggregate.audit_id is None:
        raise RuntimeError("mutation did not produce an audit row")
    return aggregate.audit_id


def _idempotent_mutation(
    session: Session,
    key: str | None,
    operation: str,
    payload: Any,
    response_model: Any,
    context: AuditContext,
    mutate: Any,
    audit_action: str | None = None,
) -> Any:
    data = payload.model_dump(mode="json", by_alias=True)
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation=operation,
            payload=data,
            response_model=response_model,
            context=context,
            action=mutate,
            audit_action=audit_action,
        )


def _idempotent_case_mutation(
    session: Session,
    key: str | None,
    operation: str,
    case_id: str,
    payload: Any,
    response_model: Any,
    context: AuditContext,
    mutate: Any,
    audit_action: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Any:
    data = {"caseId": case_id, **payload.model_dump(mode="json", by_alias=True), **(extra or {})}
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation=operation,
            payload=data,
            response_model=response_model,
            context=context,
            action=mutate,
            audit_action=audit_action,
        )
