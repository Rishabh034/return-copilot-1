"""
Central place that:
  1. Assembles the `tools=[...]` list sent to the Messages API from schemas/*.json
  2. Dispatches incoming `tool_use` blocks to the right Python handler

Add a new tool by dropping a schema in tools/schemas/ and a matching
function in tools/handlers/, then registering it in TOOL_HANDLERS below.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from app.agenticintegrations.tools.handlers import http_request_tool, log_api_test_tool

logger = logging.getLogger(__name__)

SCHEMAS_DIR = Path(__file__).parent / "tools" / "schemas"

# Map tool name (must match "name" field in the schema JSON) -> handler function.
# Handler signature: (tool_input: dict) -> str | dict  (JSON-serializable result)
TOOL_HANDLERS = {
    "http_request_tool": http_request_tool.run,
    "log_api_test_tool": log_api_test_tool.run
}


class ToolExecutionError(Exception):
    def __init__(self, tool_name: str, message: str):
        self.tool_name = tool_name
        super().__init__(f"[{tool_name}] {message}")


def load_tool_schemas(names: list[str] | None = None) -> list[dict]:
    """
    Load tool schemas from tools/schemas/*.json.
    If `names` is given, only load those (lets an agent expose a subset of tools).
    """
    schemas = []
    for path in sorted(SCHEMAS_DIR.glob("*.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        if names is None or schema["name"] in names:
            schemas.append(schema)

    if names:
        loaded = {s["name"] for s in schemas}
        missing = set(names) - loaded
        if missing:
            raise ValueError(f"No schema found for tools: {missing}")

    return schemas


def execute_tool(tool_name: str, tool_input: dict, additional_tool_params: dict) -> str:
    """
    Dispatch a single tool_use block to its handler and return a string
    suitable for a tool_result content block.
    """
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        raise ToolExecutionError(tool_name, "no handler registered")

    try:
        result = handler(tool_input, additional_tool_params)
    except Exception as e:  # noqa: BLE001 - want to surface any handler failure to the model
        logger.exception("Tool '%s' raised an exception", tool_name)
        raise ToolExecutionError(tool_name, str(e)) from e

    if isinstance(result, (dict, list)):
        return json.dumps(result)
    return str(result)


def execute_tool_calls(tool_use_blocks: list[Any], additional_tool_params: dict) -> list[dict]:
    """
    Given the tool_use content blocks from a Claude response, run each one
    and return a list of tool_result content blocks ready to send back.
    """
    results = []
    for block in tool_use_blocks:
        try:
            output = execute_tool(block.name, block.input, additional_tool_params)
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })
        except ToolExecutionError as e:
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(e),
                "is_error": True,
            })
    return results

