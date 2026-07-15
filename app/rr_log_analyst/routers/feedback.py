from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies.auth import resolve_identity
from app.exceptions import NotFoundError
from app.rr_log_analyst.auth import (
    resolve_internal_user_id,
    resolve_is_internal_user,
)
from app.rr_log_analyst.memory.slack_notifier import post_feedback
from app.rr_log_analyst.repositories import ConversationRepository
from app.rr_log_analyst.repositories.feedback_repo import FeedbackRepository
from app.rr_log_analyst.schemas.feedback import (
    FeedbackResponse,
    SubmitFeedbackRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def _resolve_identity(
    body: SubmitFeedbackRequest,
    authorization: str | None = Header(default=None),
) -> tuple[int, int, str]:
    return await resolve_identity(
        user_id=body.user_id,
        enterprise_id=body.enterprise_id,
        authorization=authorization,
    )


@router.post("/", response_model=FeedbackResponse)
async def submit_feedback(
    body: SubmitFeedbackRequest,
    identity: tuple[int, int, str] = Depends(_resolve_identity),
    session: AsyncSession = Depends(get_db),
):
    user_id, enterprise_id, _ = identity
    internal_user_id = resolve_internal_user_id(body.internal_user_id)
    is_internal_user = resolve_is_internal_user(body.is_internal_user)

    # Ownership + existence check: only accept feedback on a conversation the
    # caller actually owns. seq is stored as an opaque turn anchor.
    conversation_repo = ConversationRepository(session)
    conversation = await conversation_repo.get_owned(
        uuid.UUID(body.conversation_id),
        user_id=user_id,
        enterprise_id=enterprise_id,
        internal_user_id=internal_user_id,
        is_internal_user=is_internal_user,
    )
    if conversation is None:
        raise NotFoundError("Conversation not found")

    feedback_repo = FeedbackRepository(session)
    feedback = await feedback_repo.create(
        conversation_id=conversation.id,
        seq=body.seq,
        user_id=user_id,
        enterprise_id=enterprise_id,
        positive=body.positive,
        comment=body.comment,
        internal_user_id=internal_user_id,
        is_internal_user=is_internal_user,
    )
    await session.commit()

    logger.info(
        "Feedback stored id=%s conversation_id=%s seq=%d positive=%s",
        feedback.id,
        conversation.id,
        body.seq,
        body.positive,
    )

    # Best-effort Slack notification — a Slack failure must not fail the request.
    await asyncio.to_thread(
        post_feedback,
        conversation_id=str(conversation.id),
        seq=body.seq,
        enterprise_id=enterprise_id,
        user_id=user_id,
        positive=body.positive,
        comment=body.comment,
        is_internal_user=is_internal_user,
        internal_user_id=internal_user_id,
    )
    return FeedbackResponse(
        id=str(feedback.id),
        conversation_id=str(feedback.conversation_id),
        seq=feedback.seq,
        positive=feedback.positive,
        comment=feedback.comment,
        created_at=feedback.created_at,
        is_internal_user=feedback.is_internal_user,
        internal_user_id=feedback.internal_user_id,
    )
