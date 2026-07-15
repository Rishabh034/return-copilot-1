from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.rr_log_analyst.models.user_token import UserToken

# A timedelta rather than a bare number so the window length can't be applied
# with the wrong unit in one place and the right one in another.
WINDOW = timedelta(hours=24)
# The first turn of a window costs ~44k input tokens regardless of its length:
# the system prompt (~25k) plus 46 bound tool schemas (~12.5k). Later turns are
# charged only the growth over that, so this budget covers the initial context
# plus a handful of follow-ups.
DEFAULT_MAX_INPUT_TOKENS = 50_000
DEFAULT_MAX_OUTPUT_TOKENS = 3_000
DEFAULT_EXTRA_TOKEN = 10_000
DEFAULT_EXTRA_TOKEN_COUNT_REQUEST = 0
MIN_RANDOM_FACTOR = 0.1
MAX_RANDOM_FACTOR = 1.0

class UserTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        enterprise_id: int,
        is_internal_user: bool,
        internal_user_id: str | None,
        now: datetime | None = None,
        max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        extra_token: int = DEFAULT_EXTRA_TOKEN,
        extra_token_count_request: int = DEFAULT_EXTRA_TOKEN_COUNT_REQUEST,
    ) -> UserToken:
        now = now or datetime.now(timezone.utc)
        usertoken = UserToken(
            id=uuid.uuid4(),
            user_id=user_id,
            enterprise_id=enterprise_id,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            window_start=now,
            reset_at=now + WINDOW,
            extra_token=extra_token,
            extra_token_count_request=extra_token_count_request,
            extra_token_used=False,
            is_active=True,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
        )
        self._session.add(usertoken)
        await self._session.flush()
        return usertoken

    async def get_owned(
        self,
        *,
        user_id: int,
        enterprise_id: int,
        is_internal_user: bool,
        internal_user_id: str | None,
        for_update: bool = False,
    ) -> UserToken | None:
        statement = select(UserToken).where(
            UserToken.user_id == user_id,
            UserToken.enterprise_id == enterprise_id,
            UserToken.is_internal_user == is_internal_user,
            UserToken.internal_user_id == internal_user_id,
            UserToken.is_active.is_(True),
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        *,
        user_id: int,
        enterprise_id: int,
        is_internal_user: bool,
        internal_user_id: str | None,
        for_update: bool = False,
    ) -> UserToken:
        row = await self.get_owned(
            user_id=user_id,
            enterprise_id=enterprise_id,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
            for_update=for_update,
        )
        if row is None:
            try:
                async with self._session.begin_nested():
                    return await self.create(
                        user_id=user_id,
                        enterprise_id=enterprise_id,
                        is_internal_user=is_internal_user,
                        internal_user_id=internal_user_id,
                    )
            except IntegrityError:
                row = await self.get_owned(
                    user_id=user_id,
                    enterprise_id=enterprise_id,
                    is_internal_user=is_internal_user,
                    internal_user_id=internal_user_id,
                    for_update=for_update,
                )
                if row is None:
                    raise
        return row

    async def exists(
        self,
        user_id: int,
        enterprise_id: int,
        is_internal_user: bool,
        internal_user_id: str | None,
    ) -> bool:
        result = await self._session.execute(
            select(UserToken.id).where(
                UserToken.user_id == user_id,
                UserToken.enterprise_id == enterprise_id,
                UserToken.is_internal_user == is_internal_user,
                UserToken.internal_user_id == internal_user_id,
                UserToken.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    def _effective_limit(row: UserToken) -> int:
        base_limit = row.max_input_tokens + row.max_output_tokens
        return base_limit + (row.extra_token if row.extra_token_used else 0)

    async def can_start(
        self,
        *,
        user_id: int,
        enterprise_id: int,
        is_internal_user: bool,
        internal_user_id: str | None,
    ) -> bool:
        """Allow a request if the window expired or usage is still under the limit.

        Soft limit: if already at/over the limit and reset_at is still in the
        future, reject. One last request that crosses the limit is allowed
        when the user is still under before that request starts.
        """
        row = await self.get_or_create(
            user_id=user_id,
            enterprise_id=enterprise_id,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
            for_update=True,
        )
        now = datetime.now(timezone.utc)

        if now >= row.reset_at:
            await self._start_new_window(row, now=now)
            return True

        used = row.used_input_tokens + row.used_output_tokens
        return used < self._effective_limit(row)

    async def update_tokens(
        self,
        *,
        user_id: int,
        enterprise_id: int,
        is_internal_user: bool,
        internal_user_id: str | None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        side_input_tokens: int = 0,
    ) -> UserToken | None:
        """Settle Claude usage and extend reset_at when over the soft limit.

        ``input_tokens`` is the largest prompt Anthropic billed for this turn.
        Every call resends the system prompt, the tool schemas and the whole
        conversation, so charging that number as-is bills the same context
        again on every iteration and every turn. Only its growth over the
        biggest prompt already paid for in this window counts as new usage.
        ``side_input_tokens`` covers independent calls such as the scratchpad
        summaries, whose prompts stand alone and are charged in full.
        """
        row = await self.get_or_create(
            user_id=user_id,
            enterprise_id=enterprise_id,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
            for_update=True,
        )
        now = datetime.now(timezone.utc)

        if now >= row.reset_at:
            row = await self._start_new_window(row, now=now)

        prompt_tokens = max(0, input_tokens)
        prompt_growth = max(0, prompt_tokens - row.last_message_token_usage)
        row.last_message_token_usage = max(row.last_message_token_usage, prompt_tokens)

        row.used_input_tokens += prompt_growth + max(0, side_input_tokens)
        row.used_output_tokens += max(0, output_tokens)

        used_tokens = row.used_input_tokens + row.used_output_tokens
        effective_tokens = self._effective_limit(row)

        if used_tokens > effective_tokens and effective_tokens > 0:
            exceeded_tokens = used_tokens - effective_tokens
            factor = random.uniform(MIN_RANDOM_FACTOR, MAX_RANDOM_FACTOR)
            overage_ratio = exceeded_tokens / effective_tokens
            candidate_reset_at = (
                row.window_start + WINDOW + WINDOW * overage_ratio * factor
            )
            if candidate_reset_at > row.reset_at:
                row.reset_at = candidate_reset_at
                row.random_factor = factor

        await self._session.flush()
        return row

    async def _start_new_window(
        self,
        row: UserToken,
        *,
        now: datetime | None = None,
    ) -> UserToken:
        """Close the current quota row and create a fresh active window."""
        now = now or datetime.now(timezone.utc)
        row.is_active = False
        await self._session.flush()
        return await self.create(
            user_id=row.user_id,
            enterprise_id=row.enterprise_id,
            is_internal_user=row.is_internal_user,
            internal_user_id=row.internal_user_id,
            now=now,
            max_input_tokens=row.max_input_tokens,
            max_output_tokens=row.max_output_tokens,
            extra_token=row.extra_token,
            extra_token_count_request=row.extra_token_count_request,
        )

    async def add_extra_tokens(
        self,
        *,
        enterprise_id: int,
        user_id: int,
        is_internal_user: bool,
        internal_user_id: str | None,
    ) -> UserToken:
        row = await self.get_or_create(
            user_id=user_id,
            enterprise_id=enterprise_id,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
            for_update=True,
        )
        now = datetime.now(timezone.utc)
        if now >= row.reset_at:
            await self._start_new_window(row, now=now)
            raise ValueError("Token window has reset; extra tokens are not needed")

        used = row.used_input_tokens + row.used_output_tokens
        base_limit = row.max_input_tokens + row.max_output_tokens
        if used < base_limit:
            raise ValueError("Normal token limit has not been reached")
        if row.extra_token_used:
            raise ValueError("Extra tokens have already been claimed for this window")

        # Claiming the bonus only raises the effective limit for this window;
        # reset_at is left untouched so the claim neither adds nor removes
        # time the user has already accrued.
        row.extra_token_used = True
        row.extra_token_count_request += 1

        await self._session.flush()
        return row