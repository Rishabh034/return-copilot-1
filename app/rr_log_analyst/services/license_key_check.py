"""Resolve a Clickpost license key to its enterprise, and reject a bad one.

Two jobs in one ~130ms call to `/api/v1/request_user_detail`:

- **Reject early.** A typo'd or rotated key fails here for free rather than
  after a full agent run, and the audit trail records `invalid_license_key`
  instead of a confusing answer about missing data.
- **Name the enterprise.** That endpoint is the only one that hands back what
  the profile decorators otherwise resolve internally and keep, so an audit row
  can say *which* enterprise a question concerned rather than only that two
  questions concerned the same one.

Neither job makes this a security control. The license key is enforced by
Clickpost on every tool call whether or not we look at it first, and nothing
here can grant access it would not. So it fails open: if Clickpost is
unreachable or answers something unexpected, the request proceeds without an
enterprise attached. Only Clickpost's own INVALID_LICENSE_KEY refuses.

The result is cached per key hash for a few minutes, so a service making a
hundred calls about one enterprise pays for one lookup, not a hundred.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.clients.clickpost import (
    INVALID_LICENSE_KEY_STATUS,
    ClickpostAPIError,
    ClickpostAuth,
    ClickpostClient,
    ClickpostPermissionError,
)
from app.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnterpriseIdentity:
    """Who the license key belongs to. Audit-only — never reaches the prompt.

    Both fields are optional: a key can be accepted without being resolved,
    when the pre-flight fails open or Clickpost answers a shape we don't
    recognise.
    """

    enterprise_id: int | None = None
    enterprise_name: str | None = None


class InvalidLicenseKeyError(Exception):
    """Clickpost does not recognise this license key."""


# Sentinel distinguishing "known bad" from "not looked up yet".
_INVALID = object()

# key_hash -> (EnterpriseIdentity | _INVALID, expires_at monotonic).
# Process-local: a miss costs one cheap call, so nothing here is worth a
# shared store.
_cache: dict[str, tuple[object, float]] = {}


def _cached(key_hash: str):
    entry = _cache.get(key_hash)
    if entry is None:
        return None
    value, expires_at = entry
    if expires_at <= time.monotonic():
        _cache.pop(key_hash, None)
        return None
    return value


def _remember(key_hash: str, value: object) -> None:
    _cache[key_hash] = (
        value,
        time.monotonic() + settings.integration_license_key_ttl_seconds,
    )


def reset_cache() -> None:
    """Drop every cached result. For tests, and after rotating a key."""
    _cache.clear()


def _parse(payload: object) -> EnterpriseIdentity:
    """Pull the enterprise out of the response, tolerating a changed shape.

    A missing or renamed field costs an unattributed audit row, never a failed
    request — the caller already has a valid key at this point.
    """
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        logger.warning("request_user_detail returned no result object")
        return EnterpriseIdentity()

    raw_id = result.get("enterprise_id")
    try:
        enterprise_id = int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        logger.warning("request_user_detail enterprise_id not an int: %r", raw_id)
        enterprise_id = None

    name = result.get("enterprise_name")
    return EnterpriseIdentity(
        enterprise_id=enterprise_id,
        enterprise_name=str(name) if name else None,
    )


async def resolve_license_key(
    client: ClickpostClient | None, key: str, *, key_hash: str
) -> EnterpriseIdentity:
    """Return the key's enterprise, or raise InvalidLicenseKeyError.

    Returns an empty identity rather than raising whenever the key was not
    definitively rejected — see the module docstring on failing open.
    """
    if not settings.integration_preflight_enabled or client is None:
        return EnterpriseIdentity()

    cached = _cached(key_hash)
    if cached is _INVALID:
        raise InvalidLicenseKeyError("Invalid Clickpost license key.")
    if isinstance(cached, EnterpriseIdentity):
        return cached

    try:
        payload = await client.get_request_user_detail(ClickpostAuth(key=key))
    except ClickpostAPIError as exc:
        if exc.meta_status == INVALID_LICENSE_KEY_STATUS:
            _remember(key_hash, _INVALID)
            raise InvalidLicenseKeyError("Invalid Clickpost license key.") from exc
        # Some other envelope failure. The key authenticated, which is all this
        # asked; we just don't learn who it belongs to.
        logger.info("License key pre-flight inconclusive, proceeding: %s", exc)
        identity = EnterpriseIdentity()
    except ClickpostPermissionError:
        # A 401/403 means the credential reached the permission check, which a
        # rejected key never does. Treat as valid but unresolved.
        identity = EnterpriseIdentity()
    except Exception:
        # Unreachable Clickpost, timeout, malformed JSON. Not this function's
        # problem to report, and not cached either way.
        logger.warning("License key pre-flight failed; proceeding", exc_info=True)
        return EnterpriseIdentity()
    else:
        identity = _parse(payload)

    _remember(key_hash, identity)
    return identity
