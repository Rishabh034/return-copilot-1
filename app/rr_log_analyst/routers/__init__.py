from fastapi import APIRouter

from app.rr_log_analyst.routers.admin import router as admin_router
from app.rr_log_analyst.routers.budget_admin import router as budget_admin_router
from app.rr_log_analyst.routers.common import router as common_router
from app.rr_log_analyst.routers.conversations import router as conversations_router
from app.rr_log_analyst.routers.feedback import router as feedback_router
from app.rr_log_analyst.routers.messages import router as messages_router

router = APIRouter()
router.include_router(admin_router, prefix="/admin")
router.include_router(budget_admin_router, prefix="/admin/budget")
router.include_router(conversations_router, prefix="/conversations")
router.include_router(feedback_router, prefix="/feedback")
router.include_router(messages_router)
router.include_router(common_router)
