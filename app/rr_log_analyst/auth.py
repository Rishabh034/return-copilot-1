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
