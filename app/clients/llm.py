from langchain_anthropic import ChatAnthropic

from app.config import settings
from app.core.spec import RuntimeSettings


def create_chat_model(runtime: RuntimeSettings) -> ChatAnthropic:
    """The agent's primary model, configured entirely from its spec.

    Model, temperature, output budget and the thinking config all used to be
    read off the global `rr_analyst_*` settings here, so passing a different
    model id was not enough to give an agent different behaviour.
    """
    kwargs: dict = {}
    if runtime.thinking_enabled:
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": runtime.thinking_budget_tokens,
        }
    return ChatAnthropic(
        model=runtime.model,
        api_key=settings.anthropic_api_key,
        max_tokens=runtime.max_output_tokens,
        streaming=True,
        stream_usage=True,
        **kwargs,
        temperature=runtime.temperature,
    )


def create_secondary_chat_model(runtime: RuntimeSettings | None = None) -> ChatAnthropic:
    """The small model behind scratchpad summaries and conversation titling.

    `runtime` is optional so callers outside an agent turn - titling, for one -
    keep working off the global defaults until they are given a spec.
    """
    model = runtime.secondary_model if runtime else settings.rr_analyst_secondary_model
    temperature = (
        runtime.secondary_temperature if runtime else settings.rr_analyst_secondary_temperature
    )
    max_tokens = (
        runtime.secondary_max_output_tokens
        if runtime
        else settings.rr_analyst_secondary_max_output_tokens
    )
    return ChatAnthropic(
        model=model,
        api_key=settings.anthropic_api_key,
        max_tokens=max_tokens,
        streaming=False,
        stream_usage=True,
        temperature=temperature,
    )
