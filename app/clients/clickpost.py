from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Existing production Clickpost endpoints reused by the RR Log Analyst (read-only,
# Token auth). We reuse these rather than bespoke endpoints so we don't have to
# revalidate new APIs. See clickpost:
#   - clickpost/views/request_logs/request_logs_view.py (RequestLogs)
#   - dashboard/views/dashboard_selector_view.py (EnterpriseRecsInclusionFiltersConfigAPI)
#   - dashboard/views/... (EnterpriseRecommendationFlowConfig — selector-recs-flow-config)
#   - dashboard/views/dashboard_selector_view.py (EnterpriseLoadDistribution*ConfigAPI)
#   - signup/views/account_details.py (CourierPartnerAccountDetailView)
_RR_LOG_PATH = "/api/v2/clickpost/request_logs_v2"
_TRACK_ORDER_SHIPMENT_DETAILS_PATH = "/api/v1/dashboard/track-order-shipment-details/"

_RULES_CONFIG_PATH = "/api/v1/selector-filters-config"
_CARRIER_EXCEPTION = "/api/v1/attribute-rules-config"
_ACCOUNTS_PATH = "/api/v1/dashboard/courier_accounts"
_COURIER_PARTNERS_PATH = "/api/v1/dashboard/courier_partners/"

_RECOMMENDATION_CONFIG_PATH = "/api/v1/selector-recs-flow-config"
_RECOMMENDATION_API_PATH = "/api/v1/recommendation_api/"
_ACTIVITY_LOG_PATH = "/dashboard/api/v1/activity-log"
_ACTIVE_REPORTS_METADATA_PATH = "/api/v1/reports/active-reports-metadata"

_LOAD_DISTRIBUTION_CONFIG_PATH = "/api/v1/selector-load-distribution-config"
_LOAD_DISTRIBUTION_REVERSE_CONFIG_PATH = "/api/v1/selector-load-distribution-reverse-config"
_LOAD_DISTRIBUTION_FILTER_CONFIG_PATH = "/api/v1/dashboard/selector-load-distribution-v2-config"

_CHECK_SERVICEABILITY_PATH = "/api/v1/pincode_search_overall"


# service_id for the Recommendation service (signup.object.enterprise_service
# EnterpriseServiceConstant.RECOMMENDATION). request_logs is keyed by service.
_RECOMMENDATION_SERVICE_ID = 3
_ORDER_CREATION_SERVICE_ID = 1


@dataclass(frozen=True)
class ClickpostAuth:
    """How a Clickpost call identifies itself: a dashboard token or a license key.

    Clickpost's inject_enterprise_user_profile_v4/v5 decorators accept either.
    With no Authorization header they fall back to the `key` query param and
    look the enterprise up by its unique_id, skipping the per-user permission
    check entirely. Integration callers only ever have a key.
    """

    token: str | None = None
    key: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self.token}"} if self.token else {}

    @property
    def params(self) -> dict[str, str]:
        return {"key": self.key} if self.key else {}

    def __str__(self) -> str:
        if self.token:
            return f"token {self.token[:4]}...{self.token[-4:]}"
        return f"key {self.key[:8]}..." if self.key else "anonymous"


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

    Authentication/scoping is per-request: every method takes a `ClickpostAuth`
    holding either a dashboard token (sent as `Authorization: Token <token>`) or
    an enterprise license key (sent as `?key=<uuid>`). Either one both
    authenticates the call and scopes it to the right enterprise. The client only
    performs reads.
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

    async def _get(self, path: str, auth: ClickpostAuth, params: dict[str, Any]) -> Any:
        url = f"{self._base_url}{path}"
        # Drop None-valued params so we don't send empty query keys.
        clean = {k: v for k, v in params.items() if v is not None}
        clean.update(auth.params)
        logger.debug("Clickpost GET %s params=%s", url, clean)
        # The credential authenticates + scopes the request; forward it as-is.
        resp = await self._client.get(
            url,
            params=clean,
            headers=auth.headers or None,
        )

        # The enterprise-profile decorator returns a real 401/403 when the
        # token's user lacks the endpoint's permission. Surface that distinctly
        # so the agent can tell the user "you don't have access" instead of
        # treating it as a generic failure.
        if resp.status_code in (401, 403):
            raise ClickpostPermissionError(
                f"Credential lacks permission for {path} (HTTP {resp.status_code}). "
                "This endpoint requires a dashboard permission the current "
                "credential does not have."
            )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ClickpostAPIError(f"Clickpost {path} failed: HTTP {resp.status_code}") from e

        data = resp.json()
        logger.info("Clickpost response for %s: auth=%s status=%s meta=%s",
                    path, auth, resp.status_code,
                    data.get("meta") if isinstance(data, dict) else "non-dict")
        # These endpoints wrap responses in {"meta": {...success...}, "result": ...}
        # and return HTTP 200 even for app-level errors (e.g. unknown reference).
        # Surface that envelope failure as an error rather than silently empty data.
        if isinstance(data, dict):
            meta = data.get("meta")
            if isinstance(meta, dict) and meta.get("success") is False:
                message = meta.get("message") or data.get("error") or "Clickpost request failed"
                raise ClickpostAPIError(f"Clickpost {path}: {message}")
        return data

    async def _post(self, path: str, json_body: Any, *, auth: ClickpostAuth) -> Any:
        """POST variant of _get."""
        url = f"{self._base_url}{path}"
        logger.debug("Clickpost POST %s", url)
        resp = await self._client.post(
            url,
            json=json_body,
            params=auth.params,
            headers=auth.headers or None,
        )

        if resp.status_code in (401, 403):
            raise ClickpostPermissionError(
                f"Authentication failed for {path} (HTTP {resp.status_code}). "
                "Check the API key or token."
            )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ClickpostAPIError(
                f"Clickpost {path} failed: HTTP {resp.status_code}"
            ) from e

        data = resp.json()
        logger.info(
            "Clickpost POST response for %s: status=%s meta=%s",
            path,
            resp.status_code,
            data.get("meta") if isinstance(data, dict) else "non-dict",
        )
        if isinstance(data, dict):
            meta = data.get("meta")
            if isinstance(meta, dict) and meta.get("success") is False:
                message = (
                    meta.get("message") or data.get("error") or "Clickpost request failed"
                )
                raise ClickpostAPIError(f"Clickpost {path}: {message}")
        return data

    # -- RR logs ------------------------------------------------------------

    async def search_shipment_details(
        self,
        auth: ClickpostAuth,
        *,
        awb_list: list[str] | None = None,
        order_id_list: list[str] | None = None,
        shipment_registration_start_date_ist: str,
        shipment_registration_end_date_ist: str,
        page_number: int = 1,
        page_size: int = 100,
    ) -> dict:
        """Resolve AWB or order_id to shipment metadata (including reference_number).

        Reuses the dashboard track-order shipment-details endpoint.
        """
        body: dict[str, Any] = {
            "request_source": "dashboard",
            "sort_order": "desc",
            "sort_by": "shipmentRegisteredDate",
            "page_number": page_number,
            "page_size": page_size,
            "shipment_registration_start_date_ist": shipment_registration_start_date_ist,
            "shipment_registration_end_date_ist": shipment_registration_end_date_ist,
        }
        if awb_list:
            body["awb_list"] = awb_list
        if order_id_list:
            body["order_id_list"] = order_id_list
        return await self._post(
            _TRACK_ORDER_SHIPMENT_DETAILS_PATH,
            json_body=body,
            auth=auth,
        )

    async def get_rr_log(self, auth: ClickpostAuth, reference_number: str) -> dict:
        """Fetch the recommendation/allocation log for a single shipment.

        Reuses the request_logs endpoint for the Recommendation service; it
        returns the stored request/response envelope. Note: this endpoint only
        retains roughly the last 7 days of logs.
        """
        return await self._get(
            _RR_LOG_PATH,
            auth,
            {
                "service_id": _RECOMMENDATION_SERVICE_ID,
                "reference_number": reference_number,
            },
        )

    async def get_order_creation_log(self, auth: ClickpostAuth, reference_number: str) -> dict:
        """Fetch the recommendation/allocation log for a single shipment.

        Reuses the request_logs endpoint for the Recommendation service; it
        returns the stored request/response envelope. Note: this endpoint only
        retains roughly the last 7 days of logs.
        """
        return await self._get(
            _RR_LOG_PATH,
            auth,
            {
                "service_id": _ORDER_CREATION_SERVICE_ID,
                "reference_number": reference_number,
            },
        )

    async def get_enterprise_metadata(self, auth: ClickpostAuth) -> dict:
        """List enterprises (id + name) from active-reports-metadata.

        Reuses GET /api/v1/reports/active-reports-metadata?filters=enterprise.
        """
        return await self._get(
            _ACTIVE_REPORTS_METADATA_PATH,
            auth,
            {"filters": "enterprise"},
        )

    async def get_rr_config(self, auth: ClickpostAuth) -> dict:
        """Fetch the enterprise's Recommendation Flow Config.

        Returns EnterpriseRecommendationFlowConfig — boolean toggles for which
        pipeline stages (filters, OL, PBA/recs, ODA, load distribution, rule
        iteration, etc.) are active for this enterprise.
        """
        return await self._get(
            _RECOMMENDATION_CONFIG_PATH,
            auth,
            {},
        )
    # -- rules --------------------------------------------------------------

    async def list_rules(self, auth: ClickpostAuth, rule_type: str) -> Any:
        """List all rules of a given type ("FILTER" or "ORDERED_LIST")."""
        return await self._get(
            _RULES_CONFIG_PATH,
            auth,
            {"rule_type": rule_type},
        )

    async def get_rule(self, auth: ClickpostAuth, rule_id: str) -> Any:
        """Fetch all rules for the enterprise (caller filters by id).

        The selector-filters-config endpoint filters by `rule_type` only, not by
        id, so we fetch the full set (no rule_type) and let the tool pick out the
        matching rule id.
        """
        return await self._get(
            _RULES_CONFIG_PATH,
            auth,
            {},
        )

    # -- accounts -----------------------------------------------------------

    async def list_accounts(self, auth: ClickpostAuth) -> Any:
        """List all carrier accounts configured for the enterprise."""
        return await self._get(
            _ACCOUNTS_PATH,
            auth,
            {},
        )

    async def list_courier_partners(self, auth: ClickpostAuth) -> Any:
        """List all courier partners (global CP catalog with name, cpId, country, type)."""
        return await self._get(_COURIER_PARTNERS_PATH, auth, {})

    # -- load distribution --------------------------------------------------

    async def get_load_distribution_config(self, auth: ClickpostAuth) -> Any:
        """Fetch forward load distribution config (account-level % and daily caps)."""
        return await self._get(_LOAD_DISTRIBUTION_CONFIG_PATH, auth, {})

    async def get_load_distribution_reverse_config(self, auth: ClickpostAuth) -> Any:
        """Fetch reverse (RVP) load distribution config."""
        return await self._get(_LOAD_DISTRIBUTION_REVERSE_CONFIG_PATH, auth, {})

    async def get_load_distribution_filter_config(self, auth: ClickpostAuth, filter_id: int) -> Any:
        """Fetch per-filter (lane-level) load distribution config for a Filter rule."""
        return await self._get(
            _LOAD_DISTRIBUTION_FILTER_CONFIG_PATH,
            auth,
            {"filter_id": filter_id},
        )

    # -- recommendation engine ----------------------------------------------

    async def run_recommendation(self, auth: ClickpostAuth, orders: list[dict]) -> Any:
        """Run the recommendation engine for one or more orders."""
        return await self._post(
            _RECOMMENDATION_API_PATH,
            json_body=orders,
            auth=auth,
        )

    # -- activity / audit logs ----------------------------------------------

    async def get_audit_logs(
        self,
        auth: ClickpostAuth,
        start_date: str,
        end_date: str,
        action_taken: str,
        page_size: int = 100,
        page: int = 1,
    ) -> Any:
        """Fetch one page of dashboard activity logs (GET /dashboard/api/v1/activity-log)."""
        url = f"{self._base_url}{_ACTIVITY_LOG_PATH}"
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "action_taken": action_taken,
            "page_size": page_size,
            "page": page,
        }
        params.update(auth.params)
        logger.debug("Clickpost GET %s params=%s", url, params)
        resp = await self._client.get(url, params=params, headers=auth.headers or None)

        if resp.status_code in (401, 403):
            raise ClickpostPermissionError(
                f"Authentication failed for activity-log (HTTP {resp.status_code})."
            )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ClickpostAPIError(
                f"Clickpost activity-log failed: HTTP {resp.status_code}"
            ) from e

        if not resp.content.strip():
            return {"data": [], "totalCount": 0, "pageSize": page_size}

        try:
            data = resp.json()
        except ValueError as e:
            raise ClickpostAPIError(
                "Clickpost activity-log returned a non-JSON response"
            ) from e

        if isinstance(data, dict):
            meta = data.get("meta")
            if isinstance(meta, dict) and meta.get("success") is False:
                message = meta.get("message") or data.get("error") or "Clickpost request failed"
                raise ClickpostAPIError(f"Clickpost activity-log: {message}")
        return data


    # -- serviceability ------------------------------------------------------
    
    async def check_serviceability(self, auth: ClickpostAuth, pincode: str, is_pickup_pincode: bool, is_rvp: bool) -> Any:
        """Check serviceability for a given pincode."""
        
        params = {
            "pincode": pincode,
            "is_pickup_pincode": is_pickup_pincode,
            "is_rvp": is_rvp
        }
        
        return await self._get(
            _CHECK_SERVICEABILITY_PATH,
            auth,
            params,
        )
        
        
    # -- Attribute Layer / carrier exception rules --------------------------

    async def get_carrier_exception_rules(self, auth: ClickpostAuth) -> Any:
        """List Attribute Layer (carrier exception / attribute-rules) configs for the enterprise."""
        return await self._get(
            _CARRIER_EXCEPTION,
            auth,
            {},
        )
