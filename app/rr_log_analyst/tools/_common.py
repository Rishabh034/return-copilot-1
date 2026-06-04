from __future__ import annotations

from typing import Any


def as_list(raw: Any) -> list:
    """Coerce a Clickpost list response into a plain list.

    Different endpoints wrap collections differently — a bare list, or under
    `results` / `data` / `rules` / `accounts`. This normalizes all of those.
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("results", "data", "rules", "accounts", "result"):
            val = raw.get(key)
            if isinstance(val, list):
                return val
        # A single object — wrap it.
        if raw:
            return [raw]
    return []


def shape_rule(rule: dict) -> dict:
    """Pick the fields of a filter / ordered-list rule the analyst cares about."""
    return {
        "id": rule.get("id"),
        "name": rule.get("name"),
        "rule_type": rule.get("rule_type"),
        "priority": rule.get("priority"),
        "enabled": rule.get("enabled"),
        "account_list": rule.get("account_list") or [],
        "rule": rule.get("rule") or [],
    }
