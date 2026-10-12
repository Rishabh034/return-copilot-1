"""Return Co-pilot — the agent's own contract, with no database and no network.

The platform suite (messages, budget, conversations) is parameterized by
AgentSpec and does not know this agent exists. These tests cover what only
this directory decides: which URL it owns, which config tool wins a turn,
how personal data is masked, and how a lookup is shaped before it reaches
the model.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from app.agents.return_exchange.pii import mask_pii
from app.agents.return_exchange.prompts import build_context_note, build_system_prompt
from app.agents.return_exchange.spec import SPEC, choose_config
from app.agents.return_exchange.tools import create_tools
from app.agents.return_exchange.tools.common import CONFIG_GROUP
from app.agents.return_exchange.tools.request_lookup import merge, rows, search
from app.clients.clickpost import ClickpostAuth
from app.core import registry
from app.core.runtime.policy import resolve_group_winners
from app.core.tool_context import ToolContext

IST = ZoneInfo("Asia/Kolkata")

CONFIG_TOOLS = {
    "get_enterprise_return_exchange_configs",
    "get_return_window_config",
    "get_exchange_window_config",
    "get_return_refund_config",
    "get_rejection_config",
    "get_warehouse_config",
    "get_qc_door_step_config",
}


def test_return_exchange_is_its_own_enabled_agent():
    spec = registry.get("return_exchange")
    assert spec is SPEC
    assert spec.enabled is True
    assert spec.slug == "return_exchange"
    assert spec.url_prefix == "return-exchange"
    assert spec.display_name == "Return Co-pilot"
    assert "return_exchange" in {item.slug for item in registry.enabled()}
    assert spec.clients["return_exchange"].__name__ == "build_client"


def test_system_prompt_is_stable_and_context_note_carries_the_caller():
    prompt = build_system_prompt()
    assert "Return Co-pilot" in prompt
    assert "never mutate data" in prompt
    assert "Return & Exchange Overview" in prompt
    assert "enterprise_user_profile_id" not in prompt

    note = build_context_note(
        enterprise_id=42,
        enterprise_name="Acme",
        message_time="2026-10-13T10:00:00",
        username="riya",
    )
    assert "Acme" in note
    assert "enterprise_user_profile_id=42" in note
    assert "riya" in note
    assert "2026-10-13T10:00:00" in note


def test_choose_config_prefers_the_aggregate_then_the_longest_phrase():
    both = {"get_return_window_config", "get_return_refund_config"}
    assert choose_config(
        both | {"get_enterprise_return_exchange_configs"},
        "what is the return window",
    ) == "get_enterprise_return_exchange_configs"
    assert choose_config(both, "what is the return window, and also the refund") == "get_return_window_config"
    assert choose_config(
        {"get_exchange_window_config", "get_return_window_config"},
        "exchange window and return window",
    ) == "get_exchange_window_config"
    assert choose_config({"get_qc_door_step_config", "get_warehouse_config"}, "doorstep quality check") == (
        "get_qc_door_step_config"
    )
    assert choose_config({"get_warehouse_config", "get_rejection_config"}, None) == "get_rejection_config"


def test_config_group_keeps_a_single_winner():
    requested = {
        CONFIG_GROUP: {
            "get_return_window_config",
            "get_return_refund_config",
            "get_warehouse_config",
        }
    }
    skipped = resolve_group_winners(
        requested,
        SPEC.tool_groups,
        "only the return window please",
    )
    assert skipped[CONFIG_GROUP] == {"get_return_refund_config", "get_warehouse_config"}


def test_mask_pii_hides_phones_and_emails_and_leaves_dates():
    text = "Call +91 98765 43210 or a@shop.com about order 12345 on 2026-10-13."
    masked = mask_pii(text)
    assert "98765" not in masked
    assert "***3210" in masked
    assert "a***@shop.com" in masked
    assert "a@shop.com" not in masked
    assert "12345" in masked
    assert "2026-10-13" in masked
    assert mask_pii("") == ""
    assert SPEC.output_filter.filter_final(text) == masked
    assert SPEC.output_filter.filter_stream(text) == (masked, "")
    assert SPEC.output_filter.stream_hold_chars >= 64


def test_search_prefers_reference_then_reverse_awb():
    assert search("  REF-1 ", "AWB", "REV", "ORD") == ("REFERENCE_NO", "REF-1")
    assert search(None, "AWB", " REV ", None) == ("REVERSE_AWB_NO", "REV")
    assert search(" ", None, None, "ORD-9") == ("ORDER_ID", "ORD-9")
    assert search(None, None, None, None) is None


def test_rows_and_merge_keep_one_row_per_request_id():
    assert rows(None) == []
    assert rows({"result": {"feed": [{"id": 1}, "skip", {"id": 2}]}}) == [{"id": 1}, {"id": 2}]

    merged = merge(
        [
            {"result": {"feed": [{"id": 1, "request_status": "OPEN"}, {"note": "no id"}]}},
            {"result": {"feed": [{"id": 1, "request_status": "LATER"}, {"id": 2}]}},
        ]
    )
    feed = merged["result"]["feed"]
    assert [row.get("id") for row in feed] == [1, None, 2]
    assert feed[0]["request_status"] == "OPEN"
    assert merged["result"]["count"] == 3


class _Client:
    def __init__(self, payloads: dict):
        self.payloads = payloads
        self.calls: list[tuple] = []

    async def get(self, name, token=None, **params):
        self.calls.append((name, token, params))
        return self.payloads[name]


def _tools(client) -> dict:
    auth = ClickpostAuth(token="dashboard-token")
    context = ToolContext(auth=auth, clients={"return_exchange": client})
    return {tool.name: tool for tool in create_tools(context)}


def test_built_tools_group_only_the_config_family():
    tools = _tools(_Client({}))
    assert CONFIG_TOOLS <= set(tools)
    assert {
        "get_return_exchange_feed",
        "get_return_item_status",
        "get_return_and_refund_logs",
        "get_payment_reversal_info",
        "get_exchange_catalogue",
        "get_refund_summary",
        "get_rne_overview_return_stats",
    } <= set(tools)
    for name in CONFIG_TOOLS:
        assert tools[name].agent_policy.group == CONFIG_GROUP
    assert not hasattr(tools["get_exchange_catalogue"], "agent_policy")


def test_feed_lookup_searches_both_modes_unless_one_is_named(monkeypatch):
    frozen = datetime(2026, 10, 13, 10, 0, tzinfo=IST)

    def _now():
        return frozen

    monkeypatch.setattr(
        "app.agents.return_exchange.tools.request_lookup.clock.now_ist",
        _now,
    )
    feed = {"result": {"feed": [{"id": 7, "reference_no": "REF-1"}]}}
    client = _Client({"feed": feed})
    tools = _tools(client)

    both = asyncio.run(tools["get_return_exchange_feed"].ainvoke({"reference_number": "REF-1"}))
    assert [call[2]["return_mode"] for call in client.calls] == ["REFUND", "EXCHANGE"]
    assert client.calls[0][1] == "dashboard-token"
    assert client.calls[0][2]["search_by"] == "REFERENCE_NO"
    assert client.calls[0][2]["start_date"] == "15-07-2026"
    assert both["result"]["result"]["count"] == 1

    client.calls.clear()
    asyncio.run(
        tools["get_return_exchange_feed"].ainvoke(
            {"awb": "AWB1", "return_mode": "exchange"}
        )
    )
    assert [call[2]["return_mode"] for call in client.calls] == ["EXCHANGE"]


def test_lookup_without_an_identifier_does_not_call_the_api():
    client = _Client({})
    tools = _tools(client)
    result = asyncio.run(tools["get_return_exchange_feed"].ainvoke({}))
    assert "error" in result
    assert client.calls == []

    status = asyncio.run(tools["get_return_item_status"].ainvoke({"return_id": "9"}))
    assert "return_id alone is not enough" in status["error"]
    assert client.calls == []


def test_item_status_uses_the_live_shipment_when_the_key_is_present():
    client = _Client({"item_status": {"milestone": "picked"}})
    tools = _tools(client)
    result = asyncio.run(
        tools["get_return_item_status"].ainvoke(
            {"domain": "acme", "reverse_awb": "REV1", "key": "uuid-1", "return_id": "9"}
        )
    )
    assert client.calls == [
        (
            "item_status",
            "dashboard-token",
            {"domain": "acme", "waybill": "REV1", "key": "uuid-1", "return_id": "9"},
        )
    ]
    assert result["result"] == {"milestone": "picked"}
