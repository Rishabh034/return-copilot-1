"""Platform-wide routes — the ones that are not per-agent.

Budget, usage, admin and the service-to-service integrations API are
enterprise-wide concerns: they answer the same thing no matter which agent
asked. They stay mounted under the allocation agent's prefix because that is
where they are published today and no client should have to move; the path is
legacy, the routes are not.

The per-agent conversational routes are assembled by
`app.core.agent_api.build_agent_router` instead.
"""

from fastapi import APIRouter

from app.core.routers.admin import router as admin_router
from app.core.routers.budget_admin import router as budget_admin_router
from app.core.routers.integrations import router as integrations_router
from app.core.routers.usage import router as usage_router
from app.core.routers.user_chats import router as user_chats_router

platform_router = APIRouter()
platform_router.include_router(admin_router, prefix="/admin")
platform_router.include_router(budget_admin_router, prefix="/admin/budget")
platform_router.include_router(integrations_router, prefix="/integrations")
platform_router.include_router(usage_router, prefix="/usage")
platform_router.include_router(user_chats_router, prefix="/admin")

__all__ = ["platform_router"]
