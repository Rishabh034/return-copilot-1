from __future__ import annotations

import re
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools.context import ToolContext

_MAX_MATCHES = 5


class ResolveEnterpriseInput(BaseModel):
    name: str = Field(
        default="",
        description=(
            "Enterprise name to search (required unless enterprise_user_profile_id is set). "
            "Matched with case-insensitive regex against the full enterprise catalog internally — "
            "only matches are returned."
        ),
    )
    enterprise_user_profile_id: int | None = Field(
        default=None,
        description="Look up a specific enterprise by ID instead of searching by name.",
    )


def _shape_enterprise(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "enterprise_user_profile_id": entry.get("id"),
        "name": entry.get("name"),
        "is_active": entry.get("is_active"),
    }


def _compact_enterprise(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "enterprise_user_profile_id": entry.get("enterprise_user_profile_id"),
        "name": entry.get("name"),
    }


def _extract_enterprises(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    result = raw.get("result")
    if not isinstance(result, dict):
        return []
    enterprises = result.get("enterprises")
    if not isinstance(enterprises, list):
        return []
    return [
        shaped
        for entry in enterprises
        if isinstance(entry, dict)
        for shaped in [_shape_enterprise(entry)]
        if shaped.get("enterprise_user_profile_id") is not None
    ]


def _compile_name_pattern(name: str) -> re.Pattern[str]:
    query = name.strip()
    if not query:
        raise ValueError("name is required")

    parts = [re.escape(part) for part in re.split(r"[\s_.-]+", query) if part]
    if not parts:
        raise ValueError("name is required")
    pattern = r"[\s_.-]*".join(parts)
    return re.compile(pattern, re.IGNORECASE)


def _match_enterprises_by_name(
    enterprises: list[dict[str, Any]],
    name: str,
) -> list[dict[str, Any]]:
    pattern = _compile_name_pattern(name)
    return [
        entry
        for entry in enterprises
        if pattern.search(str(entry.get("name") or ""))
    ]


def _apply_resolved_enterprise(
    context: ToolContext,
    resolved: dict[str, Any] | None,
) -> None:
    if not resolved:
        return
    enterprise_id = resolved.get("enterprise_user_profile_id")
    if enterprise_id is not None:
        context.enterprise_user_profile_id = int(enterprise_id)
    resolved_name = resolved.get("name")
    if resolved_name:
        context.enterprise_name = str(resolved_name)


async def _load_enterprises(context: ToolContext) -> list[dict[str, Any]]:
    raw = await context.clickpost_client.get_enterprise_metadata(token=context.token)
    return _extract_enterprises(raw)


def _resolve_from_enterprises(
    enterprises: list[dict[str, Any]],
    *,
    name: str,
    enterprise_user_profile_id: int | None,
) -> dict[str, Any]:
    if enterprise_user_profile_id is not None:
        matches = [
            entry
            for entry in enterprises
            if entry.get("enterprise_user_profile_id") == enterprise_user_profile_id
        ]
        if not matches:
            return {
                "error": f"No enterprise found with enterprise_user_profile_id={enterprise_user_profile_id}",
            }
        return {"resolved": _compact_enterprise(matches[0])}

    query = name.strip()
    if not query:
        return {
            "error": (
                "Provide name= to search for an enterprise, or enterprise_user_profile_id= "
                "to look up by ID. The full enterprise catalog is not returned to save context."
            ),
        }

    matches = _match_enterprises_by_name(enterprises, query)
    if not matches:
        return {
            "error": f"No enterprise matched name '{query}'",
            "hint": "Try a shorter or alternate substring of the enterprise username.",
        }

    if len(matches) == 1:
        return {"resolved": _compact_enterprise(matches[0])}

    compact_matches = [_compact_enterprise(entry) for entry in matches[:_MAX_MATCHES]]
    response: dict[str, Any] = {
        "match_count": len(matches),
        "matches": compact_matches,
        "hint": "Multiple enterprises matched — pass a more specific name, or use enterprise_user_profile_id.",
    }
    if len(matches) > _MAX_MATCHES:
        response["truncated"] = True
    return response


async def resolve_enterprise(
    context: ToolContext,
    *,
    name: str = "",
    enterprise_user_profile_id: int | None = None,
) -> dict[str, Any]:
    enterprises = await _load_enterprises(context)
    if not enterprises:
        return {"error": "No enterprises returned from active-reports-metadata"}

    result = _resolve_from_enterprises(
        enterprises,
        name=name,
        enterprise_user_profile_id=enterprise_user_profile_id,
    )
    if "resolved" in result:
        _apply_resolved_enterprise(context, result["resolved"])
    return result


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def resolve_enterprise_tool(
        name: str = "",
        enterprise_user_profile_id: int | None = None,
    ) -> dict[str, Any]:
        return await resolve_enterprise(
            context,
            name=name,
            enterprise_user_profile_id=enterprise_user_profile_id,
        )

    return [
        StructuredTool.from_function(
            coroutine=resolve_enterprise_tool,
            name="resolve_enterprise",
            description=(
                "Resolve enterprise_user_profile_id and enterprise name. Fetches the full "
                "catalog internally via active-reports-metadata, regex-filters by name, and "
                "returns only matches (never the full list). Requires name= (e.g. 'pratechbrands') "
                "or enterprise_user_profile_id=."
            ),
            args_schema=ResolveEnterpriseInput,
        ),
    ]
