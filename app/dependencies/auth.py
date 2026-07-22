from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException

from app.config import settings


@dataclass(frozen=True)
class AuthContext:
    """Verified identity from a clickpost-minted agent JWT.

    Every field is a signed claim clickpost put there after its own DRF auth
    succeeded — never re-derive user_id/enterprise_id from the request body
    once a router takes its identity from this instead.
    """

    user_id: int
    enterprise_id: int
    cp_token: str  # the user's clickpost DRF token, forwarded on tool calls


async def require_auth(
    authorization: str | None = Header(default=None),
) -> AuthContext:
    """Verify a JWT minted by clickpost's POST /api/v1/ai-agent/token/.

    Verification is a pure signature + claims check (RS256, public key only —
    this service can never mint a token, only clickpost holds the private
    key). No DB or network call, so it's cheap on every request. A valid
    signature is proof clickpost's own auth already accepted this user;
    user_id/enterprise_id below come from the signed claims, not the caller.

    Not yet wired into routers — see settings.agent_jwt_enabled.
    """
    if not settings.agent_jwt_public_key:
        raise HTTPException(
            status_code=503,
            detail="Agent JWT auth not configured on this deployment "
            "(AGENT_JWT_PUBLIC_KEY unset).",
        )

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Missing agent token. Provide Authorization: Bearer <jwt> "
            "(minted via clickpost POST /api/v1/ai-agent/token/).",
        )
    parts = authorization.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=401,
            detail="Malformed Authorization header; expected: Bearer <jwt>",
        )
    token = parts[1].strip()

    try:
        claims = jwt.decode(
            token,
            settings.agent_jwt_public_key,
            algorithms=["RS256"],
            audience=settings.agent_jwt_audience,
            issuer=settings.agent_jwt_issuer,
            options={"require": ["exp", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Agent token expired. Mint a new one from clickpost and retry.",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid agent token.")

    try:
        return AuthContext(
            user_id=int(claims["user_id"]),
            enterprise_id=int(claims["enterprise_id"]),
            cp_token=str(claims["cp_token"]),
        )
    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=401, detail="Agent token is missing required claims."
        )
