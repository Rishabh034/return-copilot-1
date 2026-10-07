from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_RR_LOG_PATH = "/api/v2/clickpost/request_logs_v2"
_TRACK_ORDER_SHIPMENT_DETAILS_PATH = "/api/v1/dashboard/track-order-shipment-details/"

_RULES_CONFIG_PATH = "/api/v1/selector-filters-config"
_CARRIER_EXCEPTION = "/api/v1/attribute-rules-config"
_ACCOUNTS_PATH = "/api/v1/dashboard/courier_accounts"
_COURIER_PARTNERS_PATH = "/api/v1/dashboard/courier_partners/"

_RECOMMENDATION_CONFIG_PATH = "/api/v1/selector-recs-flow-config"
_RECOMMENDATION_API_PATH = "/api/v1/recommendation_api/"
_ACTIVITY_LOG_PATH = "/dashboard/api/v1/activity-log"
_REQUEST_USER_DETAIL_PATH = "/api/v1/request_user_detail"

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
    """A Clickpost call failed (HTTP error, or a `meta.success=false` envelope).

    `meta_status` is Clickpost's own status code from that envelope, which is
    not an HTTP status - these endpoints answer 200 and put the real verdict in
    the body (see utility/status_code.py in the clickpost repo; 301 is
    INVALID_LICENSE_KEY). None when the failure was at the HTTP layer instead,
    so a caller can tell "your credential is wrong" from "the call did not get
    through" without matching on message text.
    """

    def __init__(self, message: str, *, meta_status: int | None = None):
        self.meta_status = meta_status
        super().__init__(message)


# meta.status for an unknown or malformed license key.
INVALID_LICENSE_KEY_STATUS = 301


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

    async def _request(
        self,
        method: str,
        path: str,
        auth: ClickpostAuth,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        empty_body: Any = None,
    ) -> Any:
        """Send one request and unwrap Clickpost's response envelope.

        `empty_body` is returned as-is when the server answers with no content
        (the activity-log endpoint does this for an empty page); leave it None
        for endpoints that always return JSON.
        """
        url = f"{self._base_url}{path}"
        # Drop None-valued params so we don't send empty query keys.
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        clean.update(auth.params)
        logger.debug("Clickpost %s %s params=%s", method, url, clean)
        # The credential authenticates + scopes the request; forward it as-is.
        resp = await self._client.request(
            method,
            url,
            params=clean,
            json=json_body,
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

        if empty_body is not None and not resp.content.strip():
            return empty_body
        try:
            data = resp.json()
        except ValueError as e:
            raise ClickpostAPIError(f"Clickpost {path} returned a non-JSON response") from e

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
                raise ClickpostAPIError(
                    f"Clickpost {path}: {message}",
                    meta_status=meta.get("status"),
                )
        return data

    async def _get(self, path: str, auth: ClickpostAuth, params: dict[str, Any]) -> Any:
        return await self._request("GET", path, auth, params=params)

    async def _post(self, path: str, json_body: Any, *, auth: ClickpostAuth) -> Any:
        return await self._request("POST", path, auth, json_body=json_body)

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

    async def _get_request_log(
        self, auth: ClickpostAuth, reference_number: str, service_id: int
    ) -> dict:
        """Fetch the stored request/response envelope for one shipment.

        Reuses the request_logs endpoint, keyed by service. It only retains
        roughly the last 7 days of logs.
        """
        return await self._get(
            _RR_LOG_PATH,
            auth,
            {"service_id": service_id, "reference_number": reference_number},
        )

    async def get_rr_log(self, auth: ClickpostAuth, reference_number: str) -> dict:
        """Fetch the recommendation/allocation log for a single shipment."""
        return await self._get_request_log(auth, reference_number, _RECOMMENDATION_SERVICE_ID)

    async def get_order_creation_log(self, auth: ClickpostAuth, reference_number: str) -> dict:
        """Fetch the order-creation log for a single shipment."""
        return await self._get_request_log(auth, reference_number, _ORDER_CREATION_SERVICE_ID)

    async def get_request_user_detail(self, auth: ClickpostAuth) -> dict:
        """Resolve a credential to the enterprise it belongs to.

        The one endpoint that hands back what the profile decorators otherwise
        resolve and keep to themselves. Returns
        `{username, user_id, enterprise_id, enterprise_name, is_sub_user,
        business_mode}` for whichever credential is presented — including a bare
        license key, since its view takes `inject_enterprise_user_profile_v5`
        with no service and no permission, so there is nothing to be subscribed
        to or permitted for.

        An unknown or malformed key comes back as HTTP 200 carrying
        meta.status 301, which `_get` raises as ClickpostAPIError with
        `meta_status` set - so this both validates a key and names its
        enterprise in a single ~130ms call.
        """
        return await self._get(_REQUEST_USER_DETAIL_PATH, auth, {})

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

    async def list_all_rules(self, auth: ClickpostAuth) -> Any:
        """Fetch every rule for the enterprise, regardless of type.

        The selector-filters-config endpoint filters by `rule_type` only, not by
        id, so callers fetch the full set and pick out the rule they need.
        """
        return await self._get(_RULES_CONFIG_PATH, auth, {})

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
        return await self._request(
            "GET",
            _ACTIVITY_LOG_PATH,
            auth,
            params={
                "start_date": start_date,
                "end_date": end_date,
                "action_taken": action_taken,
                "page_size": page_size,
                "page": page,
            },
            empty_body={"data": [], "totalCount": 0, "pageSize": page_size},
        )

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
