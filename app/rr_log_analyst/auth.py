from __future__ import annotations

from fastapi import HTTPException

from app.config import settings
from app.exceptions import BadRequestError


def resolve_token(
    authorization: str | None = None,
) -> str:
    """Resolve Clickpost token from header, explicit value, or dev fallback."""
    if authorization:
        parts = authorization.split(maxsplit=1)
        if len(parts) == 2 and parts[0].lower() == "token" and parts[1].strip():
            return parts[1].strip()
            
    raise HTTPException(
        status_code=401,
        detail="Missing token. Provide Authorization: Token <token> or a token in the request body.",
    )


def resolve_user_id(user_id: int) -> int:
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    return user_id


def resolve_enterprise_id(enterprise_id: int) -> int:
    if enterprise_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid enterprise_id")
    return enterprise_id

def resolve_internal_user_id(internal_user_id: str | None) -> str | None:
    if internal_user_id is not None and not internal_user_id.strip():
        raise HTTPException(status_code=400, detail="Invalid internal_user_id")
    return internal_user_id.strip() if internal_user_id else None

def resolve_is_internal_user(is_internal_user: bool) -> bool:
    if not isinstance(is_internal_user, bool):
        raise HTTPException(status_code=400, detail="Invalid is_internal_user value")
    return is_internal_user


def resolve_internal_actor(
    *,
    ctx,
    body_is_internal_user: bool,
    body_internal_user_id: str | None,
) -> tuple[bool, str | None]:
    """Decide whether this turn is ClickPost support activity, and whose.

    This is a money decision, not a display one: an internal turn is recorded
    against the enterprise but excluded from its spend pool, so whoever
    controls this flag controls whether the turn counts against the cap. Taken
    from the request body alone — which is where it came from before clickpost
    signed anything — a customer can send `is_internal_user: true` and spend
    against no budget at all.

    So a signed claim wins outright whenever clickpost sent one, and the body
    is used only when it did not. `ctx.is_internal_user` is None precisely in
    that case, which is why it is tri-state rather than a bool: a signed
    `false` has to beat a body `true`, and cannot if the two are conflated.

    Falling back keeps this deployable before clickpost ships the claim —
    behaviour is then byte-for-byte what it was — and closes the gap by itself
    the moment tokens start carrying it, with nothing to re-deploy here.
    """
    if ctx is not None and ctx.is_internal_user is not None:
        if not ctx.is_internal_user:
            return False, None
        # A signed internal turn still needs a person to attribute it to.
        # `internal_user_id` is only signed for actual staff, so its absence
        # here means the two claims disagree — refuse rather than bill the
        # customer for support work or drop the attribution.
        if not ctx.internal_user_id:
            raise HTTPException(
                status_code=400,
                detail="Token marks this an internal turn but carries no internal_user_id.",
            )
        return True, ctx.internal_user_id

    is_internal = resolve_is_internal_user(body_is_internal_user)
    internal_user_id = resolve_internal_user_id(body_internal_user_id)
    if not is_internal:
        return False, None
    if internal_user_id is None:
        raise BadRequestError("Internal user ID is required")
    return True, internal_user_id