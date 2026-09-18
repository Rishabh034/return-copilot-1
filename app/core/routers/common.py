from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.db import AsyncSessionLocal
from app.core.auth.agent_token import resolve_identity
from app.exceptions import BadRequestError

from app.core.auth.actor  import(
    resolve_internal_user_id,
    resolve_is_internal_user
)

from app.core.repositories import UserTokenRepository
from app.core.conversations.common_schema import ExtraTokensInput
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

async def _resolve_identity(
    body: ExtraTokensInput,
    authorization: str | None = Header(default=None),
) -> tuple[int, int, str]:
    return await resolve_identity(
        user_id=body.user_id,
        enterprise_id=body.enterprise_id,
        authorization=authorization,
    
    )

@router.post("/tokens/demand")
async def claim_extra_tokens(
    body:ExtraTokensInput,
    request:Request,
    identity:tuple[int, int, str] = Depends(_resolve_identity),
):
    user_id,enterprise_id,token = identity
    is_internal_user = resolve_is_internal_user(body.is_internal_user)
    internal_user_id = resolve_internal_user_id(body.internal_user_id)
    
    if not is_internal_user:
        internal_user_id = None
    
    if is_internal_user and internal_user_id is None:
        raise BadRequestError("Internal user ID is required")

    logger.info(
        "Extra tokens claim requested user_id=%s enterprise_id=%s "
        "internal_user_id=%s is_internal_user=%s",
        user_id,
        enterprise_id,
        internal_user_id,
        is_internal_user,
    )
    
    async with AsyncSessionLocal() as session:
        token_repo = UserTokenRepository(session)
        try:
            row = await token_repo.add_extra_tokens(
                enterprise_id=enterprise_id,
                user_id=user_id,
                is_internal_user=is_internal_user,
                internal_user_id=internal_user_id
            )
        except ValueError as exc:
            await session.rollback()
            logger.warning(
                "Extra tokens claim rejected user_id=%s enterprise_id=%s "
                "internal_user_id=%s is_internal_user=%s reason=%s",
                user_id,
                enterprise_id,
                internal_user_id,
                is_internal_user,
                exc,
            )
            raise HTTPException(status_code=409,detail=str(exc)) from exc

        await session.commit()
        logger.info(
            "Extra tokens claimed user_id=%s enterprise_id=%s "
            "internal_user_id=%s is_internal_user=%s "
            "extra_token=%s extra_token_count_request=%s",
            user_id,
            enterprise_id,
            internal_user_id,
            is_internal_user,
            row.extra_token,
            row.extra_token_count_request,
        )
