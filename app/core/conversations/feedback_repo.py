from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.conversations.feedback_model import Feedback


class FeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        conversation_id: uuid.UUID,
        seq: int,
        user_id: int,
        enterprise_id: int,
        positive: bool,
        comment: str | None,
        internal_user_id: str | None = None,
        is_internal_user: bool = False,
    ) -> Feedback:
        feedback = Feedback(
            conversation_id=conversation_id,
            seq=seq,
            user_id=user_id,
            enterprise_id=enterprise_id,
            positive=positive,
            comment=comment,
            internal_user_id=internal_user_id,
            is_internal_user=is_internal_user,
        )
        self._session.add(feedback)
        await self._session.flush()
        return feedback
