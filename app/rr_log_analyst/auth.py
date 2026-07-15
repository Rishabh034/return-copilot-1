from __future__ import annotations

from fastapi import HTTPException

from app.config import settings


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