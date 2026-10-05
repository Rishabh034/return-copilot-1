"""Let the suite import without production secrets.

`app/config.py` builds its `Settings` at import time and declares
`anthropic_api_key` and `database_url` as required with no default. Locally
those come from `.env`, which is gitignored, and on a server from AWS Secrets
Manager. CI has neither, so importing anything under `app.` raises
ValidationError before a single assertion runs - and because every test module
reaches `app.config` transitively, the whole suite errors rather than fails.

The fix is to notice that and supply placeholders, rather than to require CI to
hold real credentials for tests that never make a network call.

Two details worth keeping:

Placeholders are injected only if the real import genuinely fails. Environment
variables outrank `.env` in pydantic-settings, so unconditionally setting them
would override a developer's real DATABASE_URL and quietly point the
integration tests at a database that does not exist.

IMDS lookup is disabled up front. Without it boto3 spends several seconds per
process trying to reach the EC2 metadata endpoint before falling back, which
turns a fast suite into a slow one on any machine that is not an EC2 instance.
"""

import os

# boto3 (app/utilities/creds.py) probes the EC2 metadata service on import.
# Off an instance that is dead weight, and it is the difference between a
# ~10s suite and a ~2min one.
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

_PLACEHOLDERS = {
    "ANTHROPIC_API_KEY": "sk-ant-test-not-a-real-key",
    # Syntactically valid so SQLAlchemy can build an engine. Nothing connects
    # unless RUN_DB_TESTS=1, and that path supplies its own URL.
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
}


def _config_imports_cleanly() -> bool:
    try:
        import app.config  # noqa: F401
    except Exception:
        return False
    return True


if not _config_imports_cleanly():
    for key, value in _PLACEHOLDERS.items():
        os.environ.setdefault(key, value)


# The integration tests DELETE rows (scoped to synthetic enterprise ids, but
# still deletes) and assume a disposable schema. Where the database URL came
# from is not obvious: with no DATABASE_URL in env or .env, `app/config.py`
# quietly fills it from AWS Secrets Manager on any machine whose AWS
# credentials can read that secret - and that secret is a shared ClickPost RDS
# instance, not a test database. So RUN_DB_TESTS=1 is refused unless the URL
# that was actually resolved points at this machine or the compose `db`
# service. Set ALLOW_REMOTE_TEST_DB=1 to override for a remote throwaway DB.
_LOCAL_DB_HOSTS = {"localhost", "127.0.0.1", "::1", "db"}

if (
    os.environ.get("RUN_DB_TESTS") == "1"
    and os.environ.get("ALLOW_REMOTE_TEST_DB") != "1"
):
    import pytest
    from sqlalchemy.engine import make_url

    from app.config import settings

    _host = make_url(settings.database_url).host
    if _host not in _LOCAL_DB_HOSTS:
        pytest.exit(
            f"RUN_DB_TESTS=1 refused: DATABASE_URL resolves to host {_host!r}, "
            "not a local database. Point DATABASE_URL at a local Postgres, or "
            "set ALLOW_REMOTE_TEST_DB=1 if that host really is disposable.",
            returncode=2,
        )
