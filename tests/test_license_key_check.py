"""resolve_license_key's cache and fail-open behavior — pure in-memory logic,
exercised the same way tests/test_agent_auth.py exercises JWT verification: no
DB, no RUN_DB_TESTS gate, a stub in place of the real HTTP client.

The module docstring's claim ("fails open... nothing here can grant access it
would not") deserves precision about *what* actually gets cached, because the
three failure paths behave differently:

- Clickpost's own INVALID_LICENSE_KEY_STATUS: cached as a rejection, raises
  every subsequent call without re-hitting Clickpost.
- Any other envelope failure (ClickpostAPIError with a different meta_status),
  or a permission error: resolves to an *empty* identity that request proceeds
  with — but that empty result is still cached for the TTL, same as a real
  success would be.
- An unreachable Clickpost / timeout / malformed JSON (a bare exception):
  resolves to an empty identity too, but is deliberately *not* cached, so the
  very next call retries Clickpost instead of being stuck unresolved for the
  TTL.

That middle case is easy to get backwards, and a flaky 401 getting cached as
"no enterprise" for `integration_license_key_ttl_seconds` is a real, if minor,
consequence if it does.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.clients.clickpost import (
    INVALID_LICENSE_KEY_STATUS,
    ClickpostAPIError,
    ClickpostPermissionError,
)
from app.config import settings
from app.core.integrations.license_key_check import (
    EnterpriseIdentity,
    InvalidLicenseKeyError,
    reset_cache,
    resolve_license_key,
)


class _StubClient:
    """Pops one scripted outcome per call: an exception to raise, or a payload
    dict to return. Counts calls so a test can assert whether the cache
    actually skipped a Clickpost round-trip."""

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def get_request_user_detail(self, auth):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def run(coro):
    return asyncio.run(coro)


def resolve(client, key_hash, key="cpai_test"):
    return run(resolve_license_key(client, key, key_hash=key_hash))


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    reset_cache()
    monkeypatch.setattr(settings, "integration_preflight_enabled", True)
    yield
    reset_cache()


# --- the two short-circuits: no client, or preflight off -----------------------


def test_client_none_returns_an_empty_identity():
    identity = run(resolve_license_key(None, "cpai_test", key_hash="none-client"))
    assert identity == EnterpriseIdentity()


def test_preflight_disabled_never_touches_the_client(monkeypatch):
    monkeypatch.setattr(settings, "integration_preflight_enabled", False)

    class _ExplodesIfCalled:
        async def get_request_user_detail(self, auth):
            raise AssertionError("must not be called when preflight is disabled")

    identity = resolve(_ExplodesIfCalled(), "disabled")
    assert identity == EnterpriseIdentity()


# --- success ---------------------------------------------------------------


def test_a_valid_key_resolves_and_is_cached():
    payload = {"result": {"enterprise_id": 42, "enterprise_name": "Acme"}}
    client = _StubClient(payload)

    first = resolve(client, "valid-key")
    second = resolve(client, "valid-key")

    assert first == EnterpriseIdentity(enterprise_id=42, enterprise_name="Acme")
    assert second == first
    assert client.calls == 1, "the second call must be served from cache"


def test_a_missing_result_object_resolves_to_an_empty_identity_not_a_crash():
    client = _StubClient({})  # no "result" key at all
    assert resolve(client, "malformed-no-result") == EnterpriseIdentity()


def test_a_non_int_enterprise_id_is_dropped_without_a_crash():
    """Only the id field is unparseable; the name is independent and survives."""
    payload = {"result": {"enterprise_id": "not-an-int", "enterprise_name": "Acme"}}
    client = _StubClient(payload)
    assert resolve(client, "malformed-id") == EnterpriseIdentity(
        enterprise_id=None, enterprise_name="Acme"
    )


# --- the invalid-key rejection: cached, keeps raising ---------------------------


def test_an_invalid_key_is_cached_and_keeps_raising_without_recalling_clickpost():
    client = _StubClient(
        ClickpostAPIError("nope", meta_status=INVALID_LICENSE_KEY_STATUS)
    )

    with pytest.raises(InvalidLicenseKeyError):
        resolve(client, "invalid-key")

    with pytest.raises(InvalidLicenseKeyError):
        resolve(client, "invalid-key")

    assert client.calls == 1, "the rejection must be cached, not re-checked every call"


# --- the middle case: resolved-but-empty, and it IS cached ----------------------


def test_a_non_invalid_api_error_resolves_empty_and_is_still_cached():
    client = _StubClient(ClickpostAPIError("weird envelope", meta_status=999))

    first = resolve(client, "other-api-error")
    second = resolve(client, "other-api-error")

    assert first == EnterpriseIdentity()
    assert second == EnterpriseIdentity()
    assert client.calls == 1, (
        "an inconclusive-but-not-invalid API error is cached the same as a "
        "real success, per resolve_license_key's fall-through to _remember()"
    )


def test_a_permission_error_resolves_empty_and_is_still_cached():
    client = _StubClient(ClickpostPermissionError("401"))

    first = resolve(client, "permission-error")
    second = resolve(client, "permission-error")

    assert first == EnterpriseIdentity()
    assert second == EnterpriseIdentity()
    assert client.calls == 1


# --- the exception path: resolved-but-empty, and it is NOT cached --------------


def test_an_unreachable_clickpost_resolves_empty_and_is_never_cached():
    client = _StubClient(TimeoutError("connect timed out"), TimeoutError("again"))

    first = resolve(client, "unreachable")
    second = resolve(client, "unreachable")

    assert first == EnterpriseIdentity()
    assert second == EnterpriseIdentity()
    assert client.calls == 2, (
        "a transient failure must not stick a customer with 'unresolved' for "
        "the TTL — the next call has to retry Clickpost"
    )


# --- cache mechanics ---------------------------------------------------------


def test_reset_cache_clears_a_cached_rejection():
    client = _StubClient(
        ClickpostAPIError("nope", meta_status=INVALID_LICENSE_KEY_STATUS),
        {"result": {"enterprise_id": 7, "enterprise_name": "Retried"}},
    )

    with pytest.raises(InvalidLicenseKeyError):
        resolve(client, "reset-me")

    reset_cache()

    identity = resolve(client, "reset-me")
    assert identity == EnterpriseIdentity(enterprise_id=7, enterprise_name="Retried")
    assert client.calls == 2


def test_ttl_expiry_forces_a_fresh_lookup(monkeypatch):
    monkeypatch.setattr(settings, "integration_license_key_ttl_seconds", 1)
    payload = {"result": {"enterprise_id": 1, "enterprise_name": "First"}}
    reissued = {"result": {"enterprise_id": 1, "enterprise_name": "Second"}}
    client = _StubClient(payload, reissued)

    real_monotonic = time.monotonic
    clock = {"offset": 0.0}
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + clock["offset"])

    first = resolve(client, "ttl")
    assert first.enterprise_name == "First"
    assert client.calls == 1

    still_cached = resolve(client, "ttl")
    assert still_cached.enterprise_name == "First"
    assert client.calls == 1, "well within the TTL, still cached"

    clock["offset"] += 2  # past the 1-second TTL
    expired = resolve(client, "ttl")
    assert expired.enterprise_name == "Second"
    assert client.calls == 2, "past the TTL, the cache must not be trusted"
