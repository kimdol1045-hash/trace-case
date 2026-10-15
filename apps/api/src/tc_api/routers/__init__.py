"""Sprint 1 API routers."""

from tc_api.routers.audit import router as audit_router
from tc_api.routers.cases import router as cases_router
from tc_api.routers.cycles import router as cycles_router
from tc_api.routers.folders import router as folders_router
from tc_api.routers.generation import router as generation_router
from tc_api.routers.generation_failures import router as generation_failures_router
from tc_api.routers.generation_quality_review import (
    router as generation_quality_review_router,
)
from tc_api.routers.generation_suggestions import (
    router as generation_suggestions_router,
)
from tc_api.routers.issues import router as issues_router
from tc_api.routers.knowledge import router as knowledge_router
from tc_api.routers.object_storage import router as object_storage_router
from tc_api.routers.reports import router as reports_router
from tc_api.routers.workspace import router as workspace_router
from tc_api.routers.work_items import router as work_items_router
from tc_api.routers.slack import router as slack_router

__all__ = [
    "audit_router",
    "cases_router",
    "cycles_router",
    "folders_router",
    "generation_router",
    "generation_failures_router",
    "generation_quality_review_router",
    "generation_suggestions_router",
    "issues_router",
    "knowledge_router",
    "object_storage_router",
    "reports_router",
    "workspace_router",
    "work_items_router",
    "slack_router",
]
