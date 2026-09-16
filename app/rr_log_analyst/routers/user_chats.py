"""Chat-shaped admin reads: who is chatting, and what was said.

Every route here is gated on ClickPost staff. The enterprise is taken from the
verified JWT, never from the query string: a copied curl cannot be pointed at
another tenant. To look at a different customer, mint a token for that
enterprise.

Transcript reads return customer message bodies and are deliberately not
recorded — there is no audit trail of who read which conversation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies.admin import require_clickpost_staff
from app.dependencies.auth import AuthContext
from app.exceptions import NotFoundError
from app.rr_log_analyst.repositories.message_mapper import rows_to_transcript
from app.rr_log_analyst.repositories.user_chats import (
    ChatMetricsRepository,
    UserChats,
    max_page,
    MAX_LIMIT
)

logger = logging.getLogger(__name__)

# Every route here is open to any ClickPost staff member on the allowlist,
# transcripts included.
router = APIRouter(dependencies=[Depends(require_clickpost_staff)])


def _window(frm: date | None, to: date | None) -> tuple[datetime | None, datetime | None]:
    """Resolve the date filter.

    `to` is inclusive of the whole day, matching `budget_admin._window` — a
    caller asking for "1 Aug to 20 Aug" means through the end of the 20th. Note
    this differs from `usage._resolve_window`, which treats `to` as exclusive;
    the two surfaces disagree and this one deliberately follows the admin side.
    """
    if frm is None and to is None:
        return None, None
    start = datetime.combine(frm, time.min, tzinfo=timezone.utc) if frm else None
    end = datetime.combine(to, time.max, tzinfo=timezone.utc) if to else None
    if start and end and end <= start:
        raise HTTPException(status_code=422, detail="`to` must be after `from`.")
    return start, end


@router.get("/user-chats")
async def list_user_chats(
    enterprise_id:int,
    user_id: int | None = None,
    is_internal_user: bool | None = None,
    internal_user_id: str | None = None,
    frm: date | None = Query(None, alias="from"),
    to: date | None = Query(
        None, description="Inclusive of the whole day, matching /admin/budget."
    ),
    # Matches MAX_LIMIT in the repository. If this were the larger of the
    # two, the API would accept a page size it then silently clamps.
    limit: int = Query(50, ge=1, le=50),
    page: int = Query(1, ge=1, le=10000,description="1-based. Past the end returns an empty page."),
    caller: AuthContext = Depends(require_clickpost_staff),
    session: AsyncSession = Depends(get_db),
):
    """Chats for the JWT's enterprise, optionally narrowed to a single user.

    `chats` and `turns` are reported separately and are not interchangeable: a
    chat opened and abandoned counts as one chat and zero turns. `turns` is the
    figure that lines up with `llm_usage_ledger`; `messages` never does.

    Identity is a three-part key — `user_id`, `is_internal_user` and
    `internal_user_id`. ClickPost staff share one Django account, so filtering
    on `user_id` alone merges every employee into one row.
    """
    if internal_user_id is not None and not internal_user_id.strip():
        internal_user_id = None

    start, end = _window(frm, to)
    repo = UserChats(session)

    total = await repo.count_chats(
        enterprise_id=enterprise_id,
        user_id=user_id,
        is_internal_user=is_internal_user,
        internal_user_id=internal_user_id,
        frm=start,
        to=end,
    )
    max_pages = max_page(total,limit)
    if(page>max_pages):
        raise HTTPException(status_code=422 , detail=f"Page value cannot be more than max_pages ({max_pages}).")

    chats = await repo.list_chats(
        enterprise_id=enterprise_id,
        user_id=user_id,
        is_internal_user=is_internal_user,
        internal_user_id=internal_user_id,
        frm=start,
        to=end,
        limit=limit,
        page=page,
    )

    return {
        "enterprise_id": enterprise_id,
        "from": start,
        "to": end,
        "chat_count": total,
        "returned": len(chats),
        "page": page,
        "page_size": min(limit,MAX_LIMIT),
        # Always >= 1, so an empty result is page 1 of 1 rather than page 1 of
        # 0. Computed from the same filtered total as the rows below it.
        "max_page": max_pages,
        "has_more": page < max_pages,
        "chats": [
            {
                "conversation_id": str(chat.conversation_id),
                "user_id": chat.user_id,
                "is_internal_user": chat.is_internal_user,
                "internal_user_id": chat.internal_user_id,
                "title": chat.title,
                "created_at": chat.created_at,
                "updated_at": chat.updated_at,
                "messages": chat.message_count,
                "turns": chat.turn_count,
                "total_tokens": chat.total_tokens,
                "cost_micros": chat.cost_micros,
                "cost_usd": round(chat.cost_micros / 1_000_000, 6),
            }
            for chat in chats
        ],
    }


@router.get("/user-chats/{conversation_id}")
async def get_user_chat(
    conversation_id: uuid.UUID,
    include_raw: bool = False,
    caller: AuthContext = Depends(require_clickpost_staff),
    session: AsyncSession = Depends(get_db),
):
    """One chat's full transcript, only if it belongs to the JWT's enterprise.

    Same 404 for missing and for another tenant, so a guessed UUID does not
    confirm that a conversation exists elsewhere.

    Served entirely from our own tables. The `metrics` block carries tokens
    (with the cache split), settled cost, tool usage and a derived response
    time — see ChatMetricsRepository for what each is and is not.
    """
    repo = UserChats(session)
    conversation = await repo.get_chat(conversation_id)
    if conversation is None:
        raise NotFoundError("Conversation not found")

    entries = rows_to_transcript(list(conversation.messages), include_raw=include_raw)
    metrics = await ChatMetricsRepository(session).summary(conversation_id)

    # Actor fields dropped along with the ctx dependency above; the format
    # string has to match or logging raises on every read.
    logger.info(
        "Transcript read conversation_id=%s enterprise_id=%s messages=%d",
        conversation_id,
        conversation.enterprise_id,
        len(entries),
    )
    
    return {
        "conversation_id": str(conversation.id),
        "enterprise_id": conversation.enterprise_id,
        "user_id": conversation.user_id,
        "is_internal_user": conversation.is_internal_user,
        "internal_user_id": conversation.internal_user_id,
        "title": conversation.title,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "turns": metrics["turns"],
        "metrics": metrics,
        "messages": entries,
    }
