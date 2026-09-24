from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException

from app.config import settings
from app.core.auth.actor import resolve_enterprise_id, resolve_token, resolve_user_id


class AgentAuthError(Exception):
    """Agent-token auth failure.

    Rendered as HTTP 200 with the real status in the body (meta.status_code)
    instead of a 401 - some caller in front of this service treats non-200
    responses as a transport failure and drops the body.
    """

    def __init__(self, detail: str, status_code: int = 401):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


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

    # Present only once clickpost signs them (see selector/views/
    # ai_agent_token_view.py). Until then both are None here, and anything that
    # needs to know who a ClickPost staff member is falls back to an explicit
    # allowlist rather than trusting the unsigned request body.
    #
    # Worth knowing why they cannot simply be derived: internal staff obtain a
    # Token for the *enterprise's* user via InternalCustomAuthTokenView, so by
    # the time this JWT is minted request.user.id is the enterprise owner - id
    # 14 for the Demo enterprise, whose is_staff is false. The staff identity is
    # lost at that exchange and has to be re-asserted deliberately.
    is_staff: bool = False
    internal_user_id: str | None = None

    # Tri-state on purpose. None means clickpost did not sign the claim, which
    # is not the same as signing it false: the first has to fall back to the
    # request body, the second must override it. Collapsing them to a bool
    # would silently ignore a signed `false` and keep trusting the client.
    is_internal_user: bool | None = None

    # Deliberately NOT tri-state, unlike is_internal_user above. This one grants
    # sight of every colleague's spend, so an absent claim must deny rather than
    # fall back to anything — a token minted before clickpost signs it simply is
    # not an admin token.
    is_enterprise_admin: bool = False

    # Seats to divide the pool by, for track E. None until clickpost sends it;
    # counting our own tables would miss everyone who has not yet sent a message.
    seat_count: int | None = None


async def require_auth(
    authorization: str | None = Header(default=None),
) -> AuthContext:
    """Verify a JWT minted by clickpost's POST /api/v1/ai-agent/token/.

    Verification is a pure signature + claims check (RS256, public key only -
    this service can never mint a token, only clickpost holds the private
    key).
    """
    if not settings.agent_jwt_public_key:
        raise HTTPException(
            status_code=503,
            detail="Agent JWT auth not configured on this deployment "
            "(AGENT_JWT_PUBLIC_KEY unset).",
        )

    if not authorization:
        raise AgentAuthError(
            "Missing agent token. Provide Authorization: Bearer <jwt> "
            "(minted via clickpost POST /api/v1/ai-agent/token/).",
        )
    parts = authorization.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise AgentAuthError(
            "Malformed Authorization header; expected: Bearer <jwt>",
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
        raise AgentAuthError(
            "Agent token expired. Mint a new one from clickpost and retry.",
        )
    except jwt.InvalidTokenError:
        raise AgentAuthError("Invalid agent token.")

    try:
        signed_internal = claims.get("internal_user_id")
        signed_is_internal = claims.get("is_internal_user")
        signed_seats = claims.get("seat_count")
        return AuthContext(
            user_id=int(claims["user_id"]),
            enterprise_id=int(claims["enterprise_id"]),
            cp_token=str(claims["cp_token"]),
            is_staff=bool(claims.get("is_staff", False)),
            internal_user_id=str(signed_internal) if signed_internal else None,
            is_internal_user=(
                bool(signed_is_internal) if signed_is_internal is not None else None
            ),
            is_enterprise_admin=bool(claims.get("is_enterprise_admin", False)),
            seat_count=int(signed_seats) if signed_seats is not None else None,
        )
    except (KeyError, TypeError, ValueError):
        raise AgentAuthError("Agent token is missing required claims.")


async def resolve_identity(
    *,
    user_id: int,
    enterprise_id: int,
    authorization: str | None,
) -> tuple[int, int, str]:
    """Returns (user_id, enterprise_id, cp_token) for a request.

    shared by every router that needs a caller's identity: switches on
    settings.agent_jwt_enabled the same way everywhere, so a route can't be
    left trusting client-supplied user_id/enterprise_id by omission.
    - True:  identity comes from a verified clickpost-minted JWT
    - False: legacy body/query values, for local testing
    """
    user, enterprise, token, _ = await resolve_identity_context(
        user_id=user_id,
        enterprise_id=enterprise_id,
        authorization=authorization,
    )
    return user, enterprise, token


async def resolve_identity_context(
    *,
    user_id: int,
    enterprise_id: int,
    authorization: str | None,
) -> tuple[int, int, str, AuthContext | None]:
    """As resolve_identity, but also hands back the verified claims.

    For callers that need more than the three identity values — whether the
    turn is ClickPost support activity, which employee is acting — and must
    tell "clickpost signed this" apart from "the client asserted it". The
    context is None when JWT auth is disabled, i.e. local testing, where there
    is nothing signed to prefer.
    """
    if settings.agent_jwt_enabled:
        ctx = await require_auth(authorization=authorization)
        return ctx.user_id, ctx.enterprise_id, ctx.cp_token, ctx

    return (
        resolve_user_id(user_id),
        resolve_enterprise_id(enterprise_id),
        resolve_token(authorization=authorization),
        None,
    )
