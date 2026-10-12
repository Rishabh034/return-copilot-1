"""Read-only Databricks SQL client for agent tools.

Why this exists: several tools read data through Clickpost APIs that time out
on large enterprises and only return the columns their maintainers chose to
expose. Querying the warehouse directly avoids both, and removes a dependency on
another team shipping an API change before a tool can read a new column.

Ported from clickpost's utility/databricks_util.py (DatabricksUtil /
DatabricksAnalyticsUtil), keeping only the read path. The write, COPY, CSV
export and Jobs API methods there are deliberately left out: this service
answers questions, it never mutates the warehouse.

The connector is synchronous, so every query runs in a worker thread
(asyncio.to_thread) to keep the event loop free while the warehouse works.
A connection is opened per query, as the clickpost util does: it costs a round
trip, but a long-lived connection's session can expire under an idle server and
fail the next tool call in a way that is hard to tell apart from a real error.

Auth: a service principal's OAuth client credentials (DATABRICKS_CLIENT_ID /
DATABRICKS_CLIENT_SECRET, from CLICKHELP_CLIENT_ID / CLICKHELP_SECRET_KEY in
Secrets Manager) when both are set, else a static DATABRICKS_ACCESS_TOKEN (a
personal token, for local dev). Same precedence as clickpost's
utility/databricks_oauth_token_provider.py.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import requests

from app.config import settings

logger = logging.getLogger(__name__)

# First keyword of a statement this client will run. A guard against a tool
# accidentally sending DML/DDL, not a security boundary: the real boundary is
# giving this service a token whose principal only has SELECT grants.
_READ_ONLY_STATEMENT = re.compile(r"^\s*(select|with|show|describe|desc|explain)\b", re.IGNORECASE)


class DatabricksNotConfiguredError(Exception):
    """Host, HTTP path or credentials are missing from env / Secrets Manager."""


class DatabricksQueryError(Exception):
    """The warehouse rejected or failed a query (includes statement timeouts)."""


_OAUTH_TOKEN_TIMEOUT_SECONDS = 15
# Databricks issues client-credentials tokens for ~1h. Re-exchange this long
# before expiry so a token can't lapse between fetching it and the query landing.
_REFRESH_BEFORE_EXPIRY_SECONDS = 300


class DatabricksOAuthTokenProvider:
    """Exchanges a service principal's client id/secret for a short-lived access
    token (OAuth client_credentials) and caches it until shortly before expiry.
    Thread-safe: queries run in worker threads and may ask for a token at once.
    """

    def __init__(
        self,
        host: str,
        client_id: str,
        client_secret: str,
        *,
        post: Callable[..., Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._host = host
        self._client_id = client_id
        self._client_secret = client_secret
        self._post = post or requests.post
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._token: str | None = None
        self._refresh_at = 0.0

    def get_token(self) -> str:
        with self._lock:
            if self._token is None or self._clock() >= self._refresh_at:
                response = self._post(
                    f"https://{self._host}/oidc/v1/token",
                    auth=(self._client_id, self._client_secret),
                    data={"grant_type": "client_credentials", "scope": "all-apis"},
                    timeout=_OAUTH_TOKEN_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                body = response.json()
                self._token = body["access_token"]
                self._refresh_at = self._clock() + int(body["expires_in"]) - _REFRESH_BEFORE_EXPIRY_SECONDS
            return self._token


class DatabricksClient:
    def __init__(
        self,
        *,
        host: str | None,
        http_path: str | None,
        access_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        statement_timeout_seconds: int = 60,
        max_rows: int = 5000,
    ) -> None:
        self._host = host
        self._http_path = http_path
        self._access_token = access_token
        self._oauth = (
            DatabricksOAuthTokenProvider(host, client_id, client_secret)
            if host and client_id and client_secret
            else None
        )
        self.statement_timeout_seconds = statement_timeout_seconds
        self.max_rows = max_rows

    @property
    def auth_mode(self) -> str:
        """"oauth" (service principal), "token" (static PAT) or "none"."""
        if self._oauth:
            return "oauth"
        return "token" if self._access_token else "none"

    @property
    def configured(self) -> bool:
        return bool(self._host and self._http_path and self.auth_mode != "none")

    def _token(self) -> str:
        return self._oauth.get_token() if self._oauth else self._access_token

    def _connect(self, statement_timeout_seconds: int):
        # Imported lazily so the app (and the test suite) still imports on a
        # machine without the connector installed; only a Databricks call needs it.
        from databricks import sql

        return sql.connect(
            server_hostname=self._host,
            http_path=self._http_path,
            access_token=self._token(),
            # Server-side cap: the warehouse cancels the statement itself, so a
            # runaway scan does not keep burning compute after we stop waiting.
            session_configuration={"STATEMENT_TIMEOUT": str(statement_timeout_seconds)},
            _use_cloud_fetch=True,
        )

    def query_sync(
        self,
        query: str,
        params: Mapping[str, Any] | None = None,
        *,
        max_rows: int | None = None,
        statement_timeout_seconds: int | None = None,
    ) -> list[dict[str, Any]]:
        """Run a read-only query and return rows as dicts, at most `max_rows` of them.

        Use named parameters (`WHERE enterprise_id = :enterprise_id`, with
        params={"enterprise_id": 42}) for every value, never string formatting;
        the values often come from the model.
        """
        if not self.configured:
            raise DatabricksNotConfiguredError(
                "Databricks is not configured: set DATABRICKS_DB_HOST_NAME, DATABRICKS_HTTP_PATH and "
                "either DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET or DATABRICKS_ACCESS_TOKEN"
            )
        if not _READ_ONLY_STATEMENT.match(query):
            raise ValueError("DatabricksClient only runs read-only statements (SELECT/WITH/SHOW/DESCRIBE)")

        row_cap = max_rows if max_rows is not None else self.max_rows
        timeout = statement_timeout_seconds or self.statement_timeout_seconds
        started = time.monotonic()
        try:
            with self._connect(timeout) as connection, connection.cursor() as cursor:
                cursor.execute(query, dict(params) if params else None)
                columns = [desc[0] for desc in cursor.description or []]
                # fetchmany, not fetchall: a tool that forgot a LIMIT must
                # not pull millions of rows into this process.
                rows = cursor.fetchmany(row_cap) if columns else []
        except (DatabricksNotConfiguredError, ValueError):
            raise
        except Exception as exc:
            logger.warning(
                "databricks query failed after %.2fs: %s", time.monotonic() - started, type(exc).__name__
            )
            raise DatabricksQueryError(str(exc)) from exc

        logger.info("databricks query returned %d rows in %.2fs", len(rows), time.monotonic() - started)
        return [dict(zip(columns, row)) for row in rows]

    async def query(
        self,
        query: str,
        params: Mapping[str, Any] | None = None,
        *,
        max_rows: int | None = None,
        statement_timeout_seconds: int | None = None,
    ) -> list[dict[str, Any]]:
        """Async wrapper over `query_sync` — what agent tools should call."""
        return await asyncio.to_thread(
            self.query_sync,
            query,
            params,
            max_rows=max_rows,
            statement_timeout_seconds=statement_timeout_seconds,
        )

    async def query_one(
        self,
        query: str,
        params: Mapping[str, Any] | None = None,
        *,
        statement_timeout_seconds: int | None = None,
    ) -> dict[str, Any] | None:
        """First row as a dict, or None — for aggregates and single-record lookups."""
        rows = await self.query(
            query, params, max_rows=1, statement_timeout_seconds=statement_timeout_seconds
        )
        return rows[0] if rows else None


_client: DatabricksClient | None = None


def get_databricks_client() -> DatabricksClient:
    """Process-wide client built from settings. Holds no connection, so sharing is safe."""
    global _client
    if _client is None:
        _client = DatabricksClient(
            host=settings.databricks_db_host_name,
            http_path=settings.databricks_http_path,
            access_token=settings.databricks_access_token,
            client_id=settings.databricks_client_id,
            client_secret=settings.databricks_client_secret,
            statement_timeout_seconds=settings.databricks_statement_timeout_seconds,
            max_rows=settings.databricks_max_rows,
        )
    return _client
