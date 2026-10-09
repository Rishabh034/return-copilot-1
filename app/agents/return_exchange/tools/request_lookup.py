from __future__ import annotations

from datetime import timedelta
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app import clock
from app.agents.return_exchange.tools._common import rne_client, token
from app.core.tool_context import ToolContext

_LOOKBACK_DAYS = 90
_MODES = ("REFUND", "EXCHANGE")
_STATUS_FIELDS = (
    "id", "reference_no", "request_status", "return_milestone", "refund_status",
    "refund_mode", "return_mode", "awb_no", "reverse_awb_no", "created_at", "tracking_link",
)
_NEED_KEY = (
    "Live shipment status needs domain, waybill, and shipment key together. "
    "Otherwise pass reference_number, awb, or order_id and status comes from the feed."
)


class IdentifierInput(BaseModel):
    reference_number: str | None = Field(default=None, description="Return/exchange reference number.")
    awb: str | None = Field(default=None, description="Forward AWB.")
    reverse_awb: str | None = Field(default=None, description="Reverse AWB, if known.")
    order_id: str | None = Field(default=None, description="Merchant order id.")
    return_mode: str | None = Field(default=None, description="REFUND or EXCHANGE. Omit to search both.")


class StatusInput(IdentifierInput):
    return_id: str | None = Field(default=None, description="Request id. Not enough on its own.")
    domain: str | None = Field(default=None, description="Tracking-page domain. Required with waybill and key.")
    key: str | None = Field(default=None, description="Shipment UUID. Required with domain and waybill.")


class LogsInput(IdentifierInput):
    return_id: str | None = Field(default=None, description="Request id from the feed, if known.")


class PaymentReversalInput(BaseModel):
    start_date: str | None = Field(default=None, description="Optional start, YYYY-MM-DD or DD-MM-YYYY.")
    end_date: str | None = Field(default=None, description="Optional end, YYYY-MM-DD or DD-MM-YYYY.")
    page: int = Field(default=1, description="Page number.")


def _kept(**kwargs: Any) -> dict[str, Any]:
    return {key: value for key, value in kwargs.items() if value is not None and str(value).strip()}


def _feed_window() -> tuple[str, str, str]:
    now = clock.now_ist()
    start = (now.date() - timedelta(days=_LOOKBACK_DAYS)).strftime("%d-%m-%Y")
    end = (now.date() + timedelta(days=1)).strftime("%d-%m-%Y")
    return start, end, now.isoformat(timespec="seconds")


def _search(reference_number, awb, reverse_awb, order_id) -> tuple[str, str] | None:
    for kind, value in (
        ("REFERENCE_NO", reference_number),
        ("REVERSE_AWB_NO", reverse_awb),
        ("AWB_NO", awb),
        ("ORDER_ID", order_id),
    ):
        if value and str(value).strip():
            return kind, str(value).strip()
    return None


def _rows(raw: Any) -> list[dict[str, Any]]:
    result = raw.get("result") if isinstance(raw, dict) else None
    feed = result.get("feed") if isinstance(result, dict) else None
    return [row for row in feed if isinstance(row, dict)] if isinstance(feed, list) else []


def _merge(raws: list[Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raws:
        for row in _rows(raw):
            rid = str(row["id"]) if row.get("id") is not None else ""
            if rid and rid in seen:
                continue
            if rid:
                seen.add(rid)
            rows.append(row)
    base = next((dict(raw) for raw in raws if isinstance(raw, dict)), {})
    result = dict(base.get("result") or {}) if isinstance(base.get("result"), dict) else {}
    result.update(feed=rows, count=len(rows), current_page=1)
    base["result"] = result
    base.setdefault("meta", {"success": True, "status": 200, "message": "SUCCESS"})
    return base


def _tool(coroutine, name: str, description: str, schema) -> StructuredTool:
    return StructuredTool.from_function(
        coroutine=coroutine, name=name, description=description, args_schema=schema
    )


def build_tools(context: ToolContext) -> list[StructuredTool]:
    client = rne_client(context)

    async def _lookup(reference_number=None, awb=None, reverse_awb=None, order_id=None, return_mode=None):
        found = _search(reference_number, awb, reverse_awb, order_id)
        if not found:
            return {"error": "Provide reference_number, awb, reverse_awb, or order_id"}, None
        search_by, search_values = found
        start_date, end_date, queried_at = _feed_window()
        mode = str(return_mode or "").strip().upper()
        modes = (mode,) if mode in _MODES else _MODES
        query = {
            "search_by": search_by,
            "search_values": search_values,
            "start_date": start_date,
            "end_date": end_date,
            "page": 1,
            "page_size": 10,
        }
        raws = [
            await client.get("feed", token(context), **query, return_mode=item) for item in modes
        ]
        meta = {
            **query,
            "return_modes_searched": list(modes),
            "queried_at": queried_at,
            "timezone": "Asia/Kolkata",
        }
        return meta, _merge(raws) if len(raws) > 1 else raws[0]

    async def get_return_exchange_feed(**kwargs) -> dict[str, Any]:
        query, raw = await _lookup(**kwargs)
        return query if raw is None else {"query": query, "result": raw}

    async def get_return_item_status(
        reference_number=None, awb=None, reverse_awb=None, order_id=None,
        return_id=None, return_mode=None, domain=None, key=None,
    ) -> dict[str, Any]:
        resolved = str(return_id).strip() if return_id else None
        waybill = (reverse_awb or awb or "").strip() or None
        domain_clean = (domain or "").strip() or None
        key_clean = (key or "").strip() or None
        if domain_clean and waybill and key_clean:
            params = _kept(domain=domain_clean, waybill=waybill, key=key_clean, return_id=resolved)
            raw = await client.get("item_status", token(context), **params)
            return {"query": params, "result": raw, "queried_at": clock.now_ist().isoformat(timespec="seconds")}

        if not (reference_number or awb or reverse_awb or order_id):
            error = _NEED_KEY if not resolved else f"return_id alone is not enough. {_NEED_KEY}"
            payload = {"error": error}
            if resolved:
                payload["query"] = {"return_id": resolved}
            return payload

        feed_query, feed_raw = await _lookup(
            reference_number=reference_number, awb=awb, reverse_awb=reverse_awb,
            order_id=order_id, return_mode=return_mode,
        )
        if feed_raw is None:
            return feed_query
        rows = _rows(feed_raw)
        if resolved:
            rows = [row for row in rows if str(row.get("id")) == resolved] or rows
        if not rows:
            return {
                "query": feed_query,
                "error": "No return/exchange found for the given identifier",
                "feed_result": feed_raw,
            }
        row = rows[0]
        return {
            "query": {"return_id": row.get("id"), "source": "feed_status_snapshot"},
            "result": {field: row.get(field) for field in _STATUS_FIELDS},
            "queried_at": feed_query.get("queried_at"),
            "note": "Shipment key was missing, so this is the feed status snapshot.",
            "resolved_via_feed": feed_query,
        }

    async def get_return_and_refund_logs(
        return_id=None, reference_number=None, awb=None, reverse_awb=None, order_id=None, return_mode=None,
    ) -> dict[str, Any]:
        resolved = str(return_id).strip() if return_id else None
        feed_query = None
        if not resolved:
            feed_query, feed_raw = await _lookup(
                reference_number=reference_number, awb=awb, reverse_awb=reverse_awb,
                order_id=order_id, return_mode=return_mode,
            )
            if feed_raw is None:
                return feed_query
            resolved = next((str(row["id"]) for row in _rows(feed_raw) if row.get("id") is not None), None)
            if not resolved:
                return {"query": feed_query, "error": "No return/exchange found to fetch logs for", "feed_result": feed_raw}
        raw = await client.get("refund_logs", token(context), return_id=resolved)
        payload = {
            "query": {"return_id": resolved},
            "result": raw,
            "queried_at": clock.now_ist().isoformat(timespec="seconds"),
        }
        if feed_query:
            payload["resolved_via_feed"] = feed_query
        return payload

    async def get_payment_reversal_info(start_date=None, end_date=None, page: int = 1) -> dict[str, Any]:
        params = _kept(start_date=start_date, end_date=end_date, page=page)
        raw = await client.get("payment_reversal", token(context), **params)
        return {"query": params, "result": raw, "hint": "Read-only. Describe dashboard steps if a reversal is stuck."}

    return [
        _tool(get_return_exchange_feed, "get_return_exchange_feed", "PRIMARY lookup by reference_number, AWB, reverse_awb, or order_id. Searches REFUND and EXCHANGE unless return_mode is set. Use the returned id for status and logs.", IdentifierInput),
        _tool(get_return_item_status, "get_return_item_status", _NEED_KEY, StatusInput),
        _tool(get_return_and_refund_logs, "get_return_and_refund_logs", "Return and refund timeline. Pass return_id, or a reference, AWB, or order_id to resolve it.", LogsInput),
        _tool(get_payment_reversal_info, "get_payment_reversal_info", "Payment reversals for a stuck refund. Dates optional. Read-only.", PaymentReversalInput),
    ]
