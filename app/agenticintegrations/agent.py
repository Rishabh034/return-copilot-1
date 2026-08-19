"""
Core agent loop: builds the system prompt, calls Claude, executes any
requested tools, feeds results back, and repeats until Claude stops
requesting tools (stop_reason != "tool_use").

Also supports a single-turn structured-output mode (see `run_structured`)
for agents that produce one schema-conforming result instead of calling
action tools.
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
        # A structured-output turn (see run_structured) doesn't call action tools.
        self.tools = [] if output_schema else load_tool_schemas(tool_names)
        self.messages: list[dict] = []
        self.system_prompt_name = system_prompt_name

    def reset(self) -> None:
        self.messages = []

    def run(self, user_input, additional_tool_params: dict) -> str:
        """Run one full user turn, including any tool-use round trips."""
        self.messages.append({"role": "user", "content": user_input})

        system_prompt = build_system_prompt(self.system_prompt_name)

        for iteration in range(MAX_TOOL_ITERATIONS):
            response = self.client.create_message(
                messages=self.messages,
                system=system_prompt,
                tools=self.tools,
            )

            # Always append the assistant turn (text + any tool_use blocks) as-is
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                if response.stop_reason == "max_tokens":
                    logger.warning("Hit MAX TOKENS without reaching a final answer")

                return self._extract_text(response.content)

            tool_use_blocks = [block for block in response.content if block.type == "tool_use"]
            logger.info("Model requested %d tool call(s): %s",
                        len(tool_use_blocks), [b.name for b in tool_use_blocks])

            tool_results = execute_tool_calls(tool_use_blocks, additional_tool_params)
            self.messages.append({"role": "user", "content": tool_results})

        logger.warning("Hit MAX_TOOL_ITERATIONS (%d) without a final answer", MAX_TOOL_ITERATIONS)
        return "I wasn't able to complete this after several tool calls — could you rephrase or narrow the request?"

    def run_structured(self, user_input) -> dict:
        """
        Run a single turn with the response constrained to `output_schema`
        via output_config, and return the parsed structured result. Requires
        the agent to have been constructed with an output_schema. No
        tool-execution loop.
        """
        if not self.output_schema:
            raise ValueError("Agent was not initialised with an output_schema")

        self.messages.append({"role": "user", "content": user_input})
        system_prompt = build_system_prompt(self.system_prompt_name)

        logger.info("Sending structured-output request to Claude (system_prompt=%s)", self.system_prompt_name)
        response = self.client.create_message(
            messages=self.messages,
            system=system_prompt,
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": self.output_schema,
                }
            },
        )
        logger.info("Received structured-output response from Claude (stop_reason=%s)", response.stop_reason)
        self.messages.append({"role": "assistant", "content": response.content})

        return json.loads(self._extract_text(response.content))

    @staticmethod
    def _extract_text(content_blocks) -> str:
        return "\n".join(block.text for block in content_blocks if block.type == "text").strip()

