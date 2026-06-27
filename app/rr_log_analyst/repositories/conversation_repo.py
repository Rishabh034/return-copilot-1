from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from langchain_core.messages import BaseMessage
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.rr_log_analyst.models.conversation import Conversation
from app.rr_log_analyst.models.message import Message
from app.rr_log_analyst.repositories.message_mapper import message_to_row, rows_to_messages


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        enterprise_id: int,
        conversation_id: uuid.UUID | None = None,
    ) -> Conversation:
        conversation = Conversation(
            id=conversation_id or uuid.uuid4(),
            user_id=user_id,
            enterprise_id=enterprise_id,
        )
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def get_owned(
        self,
        conversation_id: uuid.UUID,
        *,
        user_id: int,
        enterprise_id: int,
    ) -> Conversation | None:
        result = await self._session.execute(
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.enterprise_id == enterprise_id,
            )
            .options(selectinload(Conversation.messages))
        )
        return result.scalar_one_or_none()

    async def exists(self, conversation_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(Conversation.id).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none() is not None

    async def list_for_user(
        self,
        *,
        user_id: int,
        enterprise_id: int,
    ) -> list[Conversation]:
        result = await self._session.execute(
            select(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.enterprise_id == enterprise_id,
            )
            .options(selectinload(Conversation.messages))
            .order_by(Conversation.updated_at.desc())
        )
        return list(result.scalars().all())

    async def load_messages(self, conversation_id: uuid.UUID) -> list[BaseMessage]:
        result = await self._session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.seq)
        )
        return rows_to_messages(list(result.scalars().all()), conversation_id=str(conversation_id))

    async def save_messages(
        self,
        conversation: Conversation,
        messages: list[BaseMessage],
    ) -> None:
        await self._session.execute(
            delete(Message).where(Message.conversation_id == conversation.id)
        )
        for seq, message in enumerate(messages):
            self._session.add(message_to_row(conversation.id, seq, message))
        conversation.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def purge_stale(self, max_age: timedelta = timedelta(hours=24)) -> int:
        from app.rr_log_analyst.memory.tool_results import tool_result_store

        cutoff = datetime.now(timezone.utc) - max_age
        result = await self._session.execute(
            select(Conversation.id).where(Conversation.updated_at < cutoff)
        )
        stale_ids = [row[0] for row in result.all()]
        if not stale_ids:
            return 0

        for conversation_id in stale_ids:
            tool_result_store.purge_conversation(str(conversation_id))

        await self._session.execute(
            delete(Conversation).where(Conversation.id.in_(stale_ids))
        )
        return len(stale_ids)

    async def storage_stats(self) -> dict[str, int]:
        conversation_count = await self._session.scalar(
            select(func.count()).select_from(Conversation)
        )
        message_count = await self._session.scalar(
            select(func.count()).select_from(Message)
        )
        return {
            "conversation_count": conversation_count or 0,
            "message_count": message_count or 0,
            "storage_bytes": 0,
        }

    async def storage_stats_for_user(
        self,
        *,
        user_id: int,
        enterprise_id: int,
    ) -> dict[str, int]:
        conversation_count = await self._session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.enterprise_id == enterprise_id,
            )
        )
        message_count = await self._session.scalar(
            select(func.count())
            .select_from(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.user_id == user_id,
                Conversation.enterprise_id == enterprise_id,
            )
        )
        return {
            "conversation_count": conversation_count or 0,
            "message_count": message_count or 0,
            "storage_bytes": 0,
        }
