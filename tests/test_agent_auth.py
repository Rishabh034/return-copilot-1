"""Agent JWT verification, exercised with a keypair generated per test run.

This is the answer to "how do we test authenticated endpoints in CI without a
Clickpost token that keeps expiring": we don't need one. The service verifies
RS256 signatures with a public key and can never mint — see
app/dependencies/auth.py:43 — so a test can hold the matching private key,
issue whatever claims it likes, and drive the real verification path. No shared
secret, nothing to rotate, and expiry becomes something we control rather than
something that breaks the pipeline on a Tuesday.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app.config import settings
from app.core.auth.agent_token import AgentAuthError, require_auth

ISSUER = "clickpost"
AUDIENCE = "clickpost-ai-agent"


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


# One keypair for the module: RSA generation is slow enough to matter per-test.
PRIVATE_PEM, PUBLIC_PEM = _keypair()
OTHER_PRIVATE_PEM, _ = _keypair()


def mint(private_pem: str = PRIVATE_PEM, **overrides) -> str:
    claims = {
        "user_id": 15,
        "enterprise_id": 15,
        "cp_token": "cp-token-for-tests",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    claims.update(overrides)
    for key in [k for k, v in claims.items() if v is None]:
        del claims[key]
    token = jwt.encode(claims, private_pem, algorithm="RS256")
    # PyJWT <2 returns bytes; requirements.txt pins 2.13.0, but a developer
    # machine may have an older one and the failure is opaque (the token gets
    # str()'d into "b'eyJ...'").
    return token.decode() if isinstance(token, bytes) else token


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "agent_jwt_public_key", PUBLIC_PEM)
    monkeypatch.setattr(settings, "agent_jwt_issuer", ISSUER)
    monkeypatch.setattr(settings, "agent_jwt_audience", AUDIENCE)


def auth(header: str | None):
    return asyncio.run(require_auth(authorization=header))


def test_valid_token_yields_the_signed_identity():
    ctx = auth(f"Bearer {mint()}")
    assert (ctx.user_id, ctx.enterprise_id) == (15, 15)
    assert ctx.cp_token == "cp-token-for-tests"


def test_identity_comes_from_the_token_not_the_caller():
    """The whole point of signing: claims win over anything a client sends."""
    ctx = auth(f"Bearer {mint(user_id=999, enterprise_id=888)}")
    assert (ctx.user_id, ctx.enterprise_id) == (999, 888)


def test_expired_token_is_rejected():
    stale = mint(exp=datetime.now(timezone.utc) - timedelta(seconds=1))
    with pytest.raises(AgentAuthError, match="expired"):
        auth(f"Bearer {stale}")


def test_token_signed_by_another_key_is_rejected():
    with pytest.raises(AgentAuthError, match="Invalid"):
        auth(f"Bearer {mint(OTHER_PRIVATE_PEM)}")


@pytest.mark.parametrize("field,value", [("aud", "someone-else"), ("iss", "not-clickpost")])
def test_wrong_audience_or_issuer_is_rejected(field, value):
    with pytest.raises(AgentAuthError, match="Invalid"):
        auth(f"Bearer {mint(**{field: value})}")


# options={"require": [...]} landed in PyJWT 2. requirements.txt pins 2.13.0 and
# CI runs that, so this is enforced where it matters; on an older local install
# the claim simply is not required and the test would fail for the library's
# reasons rather than ours.
@pytest.mark.skipif(
    tuple(int(part) for part in jwt.__version__.split(".")[:1]) < (2,),
    reason="PyJWT<2 ignores the `require` option",
)
@pytest.mark.parametrize("claim", ["exp", "aud", "iss"])
def test_required_registered_claims_must_be_present(claim):
    with pytest.raises(AgentAuthError, match="Invalid"):
        auth(f"Bearer {mint(**{claim: None})}")


@pytest.mark.parametrize("claim", ["user_id", "enterprise_id", "cp_token"])
def test_required_identity_claims_must_be_present(claim):
    with pytest.raises(AgentAuthError, match="missing required claims"):
        auth(f"Bearer {mint(**{claim: None})}")


@pytest.mark.parametrize("header", [None, "", "Bearer", "Token abc", "Bearer   "])
def test_malformed_authorization_headers_are_rejected(header):
    with pytest.raises(AgentAuthError):
        auth(header)


def test_unconfigured_deployment_reports_503_not_401(monkeypatch):
    """A missing public key is our misconfiguration, not the caller's fault."""
    monkeypatch.setattr(settings, "agent_jwt_public_key", None)
    with pytest.raises(HTTPException) as excinfo:
        auth(f"Bearer {mint()}")
    assert excinfo.value.status_code == 503
