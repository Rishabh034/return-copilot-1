"""
Core agent loop: builds the system prompt, calls Claude, executes any
requested tools, feeds results back, and repeats until Claude stops
requesting tools (stop_reason != "tool_use").

Also supports a structured-output mode (see `run_structured`) that runs the
same tool-execution loop but constrains the final, no-more-tools turn to a
given output_schema instead of freeform text.
"""

from __future__ import annotations

import json
import logging

from .client import ClaudeClient
from .prompts.loader import build_system_prompt
from .tools.registry import load_tool_schemas, execute_tool_calls

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 50  # guard against infinite tool-call loops


class Agent:
    def __init__(
        self,
        system_prompt_name: str,
        client: ClaudeClient | None = None,
        tool_names: list[str] | None = None,
        output_schema: dict | None = None,
    ) -> None:
        self.client = client or ClaudeClient()
        self.output_schema = output_schema
        # A structured-output turn (see run_structured) doesn't call client-executed action
        # tools by default, but may still opt into server-side tools (e.g. web_search) via
        # an explicit tool_names list.
        if tool_names is not None:
            self.tools = load_tool_schemas(tool_names)
        else:
            self.tools = [] if output_schema else load_tool_schemas(tool_names)
        self.messages: list[dict] = []
        self.system_prompt_name = system_prompt_name
        self.container_id: str | None = None

    def reset(self) -> None:
        self.messages = []
        self.container_id = None

    def run(self, user_input, additional_tool_params: dict) -> str:
        """Run one full user turn, including any tool-use round trips."""
        content = self._run_loop(user_input, output_config=None, additional_tool_params=additional_tool_params)
        if content is None:
            return "I wasn't able to complete this after several tool calls — could you rephrase or narrow the request?"
        return self._extract_text(content)

    def run_structured(self, user_input) -> dict:
        """
        Run to completion with the final answer constrained to `output_schema`
        via output_config, and return the parsed structured result. Requires
        the agent to have been constructed with an output_schema. Any
        client-executed tools configured (e.g. fetch_documentation_tool) are
        handled via the same tool-use round trips as run() — output_config
        only constrains the turn where the model stops calling tools.
        """
        if not self.output_schema:
            raise ValueError("Agent was not initialised with an output_schema")

        logger.info("Sending structured-output request to Claude (system_prompt=%s)", self.system_prompt_name)
        output_config = {"format": {"type": "json_schema", "schema": self.output_schema}}
        content = self._run_loop(user_input, output_config=output_config, additional_tool_params={})
        if content is None:
            raise RuntimeError(f"Hit MAX_TOOL_ITERATIONS ({MAX_TOOL_ITERATIONS}) without a structured result")

        logger.info("Received structured-output response from Claude")
        return json.loads(self._extract_final_json_text(content))

    def _run_loop(self, user_input, output_config: dict | None, additional_tool_params: dict):
        """
        Shared tool-execution loop: sends the turn, executes any requested
        client tools and feeds results back, repeating until Claude stops
        requesting tools (stop_reason != "tool_use"). Returns the final
        response's content blocks, or None if MAX_TOOL_ITERATIONS was hit.
        """
        self.messages.append({"role": "user", "content": user_input})
        system_prompt = build_system_prompt(self.system_prompt_name)

        for iteration in range(MAX_TOOL_ITERATIONS):
            response = self.client.create_message(
                messages=self.messages,
                system=system_prompt,
                tools=self.tools,
                container=self.container_id,
                output_config=output_config,
            )
            self._track_container(response)
            self._log_server_tool_activity(response)

            # Always append the assistant turn (text + any tool_use blocks) as-is
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                if response.stop_reason == "max_tokens":
                    logger.warning("Hit MAX TOKENS without reaching a final answer")

                return response.content

            tool_use_blocks = [block for block in response.content if block.type == "tool_use"]
            logger.info("Model requested %d tool call(s): %s",
                        len(tool_use_blocks), [b.name for b in tool_use_blocks])

            tool_results = execute_tool_calls(tool_use_blocks, additional_tool_params)
            self.messages.append({"role": "user", "content": tool_results})

        logger.warning("Hit MAX_TOOL_ITERATIONS (%d) without a final answer", MAX_TOOL_ITERATIONS)
        return None

    def _track_container(self, response) -> None:
        """
        Server-side tools backed by code execution (e.g. web_search) tie their
        pending state to a container. If a response carries one, subsequent
        requests in this conversation must echo it back, or the API rejects
        them with "container_id is required when there are pending tool uses...".
        """
        container = getattr(response, "container", None)
        if container is not None:
            self.container_id = container.id

    @staticmethod
    def _log_server_tool_activity(response) -> None:
        """
        Console-only visibility into server-side tool calls (web_search,
        web_fetch). These arrive as server_tool_use / *_tool_result blocks,
        not tool_use blocks, so the client-tool logging in run() never sees
        them — without this, a turn that spends a long time on server-side
        tool calls logs nothing at all. Does not affect self.messages,
        control flow, or any returned value.
        """
        for block in response.content:
            if block.type == "server_tool_use":
                logger.info("Server tool call: %s(%s)", block.name, block.input)
            elif block.type in ("web_search_tool_result", "web_fetch_tool_result"):
                content = block.content
                if isinstance(content, list):
                    logger.info("%s returned %d result(s)", block.type, len(content))
                else:
                    logger.info("%s error: %s", block.type, getattr(content, "error_code", content))

    @staticmethod
    def _extract_final_json_text(content_blocks) -> str:
        """
        Return the last text block in the response. When a server-side tool
        (e.g. web_search) runs before the final answer, the model may emit
        text blocks alongside the tool-use/tool-result blocks — only the
        last text block is guaranteed to be the schema-conforming output.
        """
        text_blocks = [block.text for block in content_blocks if block.type == "text"]
        if not text_blocks:
            raise ValueError("No text block found in structured-output response")
        return text_blocks[-1]

    @staticmethod
    def _extract_text(content_blocks) -> str:
        return "\n".join(block.text for block in content_blocks if block.type == "text").strip()

