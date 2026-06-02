from fastapi import APIRouter

from app.rr_log_analyst.routers.conversations import router as conversations_router
from app.rr_log_analyst.routers.messages import router as messages_router

router = APIRouter()
router.include_router(conversations_router, prefix="/conversations")
router.include_router(messages_router, prefix="/conversations")
