"""GET-only Clickpost client for Return & Exchange tools."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.clients.clickpost import ClickpostAPIError, ClickpostPermissionError

logger = logging.getLogger(__name__)

# Names the tools pass to get(). Blank values are dropped: an empty shipment key is rejected upstream.
PATHS = {
    "feed": "/api/v2/dashboard/return-exchange/feed",
    "item_status": "/api/v2/tracking-page/fetch_return_status",
    "refund_logs": "/api/v1/dashboard/fetch-return-and-refund-logs",
    "payment_reversal": "/api/v1/dashboard/fetch_payment_reversal_info",
    "refund_summary": "/api/v1/dashboard/return-exchange/summary",
    "enterprise_configs": "/api/v1/fetch-enterprise-return-exchange-configs",
    "return_window": "/api/v1/return-exchange-configuration/return-configuration/return-window",
    "exchange_window": "/api/v1/return-exchange-configuration/exchange-configuration/exchange-window",
    "refund_config": "/api/v1/return-exchange-configuration/general-configuration/return-refund-config",
    "rejection_config": "/api/v1/return-exchange-configuration/general-configuration/rejection-config",
    "warehouse_config": "/api/v1/return-exchange-configuration/general-configuration/warehouse-config",
    "qc_config": "/api/v1/return-exchange-configuration/general-configuration/qc-door-step-config",
    "return_stats": "/api/v1/dashboard/return-exchange/overview/return-stats",
    "catalogue": "/api/exchange/v1/products",
}


class ReturnExchangeClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout, headers={"Accept": "application/json"})

    async def aclose(self) -> None:
        await self.client.aclose()

    async def get(self, name: str, token: str | None = None, **params: Any) -> Any:
        path = PATHS[name]
        clean = {
            key: value
            for key, value in params.items()
            if value is not None and not (isinstance(value, str) and not value.strip())
        }
        headers = {"Authorization": f"Token {token}"} if token else None
        logger.debug("ReturnExchange GET %s params=%s", path, clean)
        resp = await self.client.get(f"{self.base_url}{path}", params=clean, headers=headers)
        if resp.status_code in (401, 403):
            raise ClickpostPermissionError(
                f"Token lacks permission for {path} (HTTP {resp.status_code})."
            )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ClickpostAPIError(
                f"Clickpost {path} failed: HTTP {resp.status_code} body={resp.text[:500]}"
            ) from exc
        if not resp.content or not resp.content.strip():
            return {}
        try:
            data = resp.json()
        except ValueError as exc:
            raise ClickpostAPIError(f"Clickpost {path} returned non-JSON") from exc
        meta = data.get("meta") if isinstance(data, dict) else None
        if isinstance(meta, dict) and meta.get("success") is False:
            message = meta.get("message") or data.get("error") or "Clickpost request failed"
            raise ClickpostAPIError(f"Clickpost {path}: {message}")
        return data
