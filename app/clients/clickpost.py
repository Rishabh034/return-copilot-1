from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Existing production Clickpost endpoints reused by the RR Log Analyst (read-only,
# Token auth). We reuse these rather than bespoke endpoints so we don't have to
# revalidate new APIs. See clickpost:
#   - clickpost/views/request_logs/request_logs_view.py (RequestLogs)
#   - dashboard/views/dashboard_selector_view.py (EnterpriseRecsInclusionFiltersConfigAPI)
#   - signup/views/account_details.py (CourierPartnerAccountDetailView)
_RR_LOG_PATH = "/api/v1/clickpost/request_logs"

_RULES_CONFIG_PATH = "/api/v1/selector-filters-config"

_ACCOUNTS_PATH = "/api/v1/dashboard/courier_accounts"

# service_id for the Recommendation service (signup.object.enterprise_service
# EnterpriseServiceConstant.RECOMMENDATION). request_logs is keyed by service.
_RECOMMENDATION_SERVICE_ID = 3


class ClickpostPermissionError(Exception):
    """The token authenticated but lacks the permission the endpoint requires.

    These reused endpoints gate on real dashboard permissions (request_logs ->
    MISCELLANEOUS_GET_LOG_VIEW, rules -> ALLOCATION_RULE_ORDERED_LISTS_RULE_VIEW),
    so a valid token whose user lacks them gets an HTTP 401/403.
    """


class ClickpostAPIError(Exception):
    """A Clickpost call failed (HTTP error, or a `meta.success=false` envelope)."""


class ClickpostClient:
    """Thin async wrapper over the Clickpost HTTP APIs used by the RR Log Analyst.

    These are existing production endpoints (request logs, selector filter config,
    courier accounts) on a single host (`base_url`), reused as-is.

    Authentication/scoping is per-request: every method takes a `token` that the
    caller obtained from the dashboard. It is forwarded verbatim as the
    `Authorization: Token <token>` header, which both authenticates the call and
    scopes it to the right enterprise. The client only performs reads.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- internal helpers ---------------------------------------------------

    async def _get(self, path: str, token: str, params: dict[str, Any]) -> Any:
        url = f"{self._base_url}{path}"
        # Drop None-valued params so we don't send empty query keys.
        clean = {k: v for k, v in params.items() if v is not None}
        logger.debug("Clickpost GET %s params=%s", url, clean)
        # The token authenticates + scopes the request; forward it as-is.
        resp = await self._client.get(
            url,
            params=clean,
            headers={"Authorization": f"Token {token}"} if token else None,
        )

        # The enterprise-profile decorator returns a real 401/403 when the
        # token's user lacks the endpoint's permission. Surface that distinctly
        # so the agent can tell the user "you don't have access" instead of
        # treating it as a generic failure.
        if resp.status_code in (401, 403):
            raise ClickpostPermissionError(
                f"Token lacks permission for {path} (HTTP {resp.status_code}). "
                "This endpoint requires a dashboard permission the current token "
                "does not have."
            )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ClickpostAPIError(f"Clickpost {path} failed: HTTP {resp.status_code}") from e

        data = resp.json()
        # These endpoints wrap responses in {"meta": {...success...}, "result": ...}
        # and return HTTP 200 even for app-level errors (e.g. unknown reference).
        # Surface that envelope failure as an error rather than silently empty data.
        if isinstance(data, dict):
            meta = data.get("meta")
            if isinstance(meta, dict) and meta.get("success") is False:
                message = meta.get("message") or data.get("error") or "Clickpost request failed"
                raise ClickpostAPIError(f"Clickpost {path}: {message}")
        return data

    # -- RR logs ------------------------------------------------------------

    async def get_rr_log(self, token: str, reference_number: str) -> dict:
        """Fetch the recommendation/allocation log for a single shipment.

        Reuses the request_logs endpoint for the Recommendation service; it
        returns the stored request/response envelope. Note: this endpoint only
        retains roughly the last 7 days of logs.
        """
        return await self._get(
            _RR_LOG_PATH,
            token,
            {
                "service_id": _RECOMMENDATION_SERVICE_ID,
                "reference_number": reference_number,
            },
        )

    # -- rules --------------------------------------------------------------

    async def list_rules(self, token: str, rule_type: str) -> Any:
        """List all rules of a given type ("FILTER" or "ORDERED_LIST")."""
        return await self._get(
            _RULES_CONFIG_PATH,
            token,
            {"rule_type": rule_type},
        )

    async def get_rule(self, token: str, rule_id: int) -> Any:
        """Fetch all rules for the enterprise (caller filters by id).

        The selector-filters-config endpoint filters by `rule_type` only, not by
        id, so we fetch the full set (no rule_type) and let the tool pick out the
        matching rule id.
        """
        return await self._get(
            _RULES_CONFIG_PATH,
            token,
            {},
        )

    # -- accounts -----------------------------------------------------------

    async def list_accounts(self, token: str) -> Any:
        """List all carrier accounts configured for the enterprise."""
        return await self._get(
            _ACCOUNTS_PATH,
            token,
            {},
        )
