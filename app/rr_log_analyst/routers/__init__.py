from fastapi import APIRouter

from app.rr_log_analyst.routers.messages import router as messages_router

router = APIRouter()
router.include_router(messages_router)
