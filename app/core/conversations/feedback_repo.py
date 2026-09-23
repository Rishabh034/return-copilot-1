from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.conversations.conversation_model import Conversation
from app.core.conversations.feedback_model import Feedback
from app.exceptions import NotFoundError


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
        """Record feedback on one turn, after confirming the caller owns it.

        `submit_feedback` already checks ownership via `ConversationRepository
        .get_owned` before calling this, so this is a second, independent
        check rather than the only one — a repository that trusts its caller
        to have checked is one refactor away from a customer's feedback
        landing on another enterprise's conversation. Deliberately not
        `get_owned` itself: that also filters out stale conversations, and a
        turn from an old conversation should still be gradeable.
        """
        owned = await self._session.execute(
            select(Conversation.id).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.enterprise_id == enterprise_id,
                Conversation.internal_user_id == internal_user_id,
                Conversation.is_internal_user == is_internal_user,
            )
        )
        if owned.scalar_one_or_none() is None:
            raise NotFoundError("Conversation not found")

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
