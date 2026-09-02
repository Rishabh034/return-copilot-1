"""Authenticate a calling service by its integration API key.

Distinct from app/dependencies/auth.py, which resolves a *user* — a dashboard
token or a clickpost-minted agent JWT. There is no user here: the caller is
another service, and the key answers "who is calling and who pays", not "who is
this person and what may they see". What the request is allowed to *read* is
decided entirely by the Clickpost license key it carries in its body.
"""

from __future__ import annotations

import logging

from fastapi import Header

from app.db import AsyncSessionLocal
from app.exceptions import AuthError
from app.rr_log_analyst.models.integration_api_key import (
    IntegrationApiKey,
    hash_api_key,
)
from app.rr_log_analyst.repositories import IntegrationKeyRepository

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias=API_KEY_HEADER),
) -> IntegrationApiKey:
    """Resolve the header to an active key row, or refuse the request.

    The lookup is by hash, so the plaintext key exists only for the length of
    this function and is never written anywhere — not to the database, and not
    to a log line, which is what `key_prefix` is for.
    """
    if not x_api_key or not x_api_key.strip():
        raise AuthError(f"Missing API key. Provide the {API_KEY_HEADER} header.")

    async with AsyncSessionLocal() as session:
        key = await IntegrationKeyRepository(session).get_active_by_hash(
            hash_api_key(x_api_key)
        )

    if key is None:
        # Never echo the key back, not even truncated: an unknown key may be a
        # near-miss of a real one, and the response is the wrong place to
        # confirm any part of it.
        logger.warning("Rejected integration request: unknown or revoked API key")
        raise AuthError("Invalid or revoked API key.")

    return key
