from langchain_anthropic import ChatAnthropic

from app.config import settings


def create_chat_model(model: str | None = None) -> ChatAnthropic:
    kwargs: dict = {}
    if settings.rr_analyst_thinking_enabled:
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": settings.rr_analyst_thinking_budget_tokens,
        }
    return ChatAnthropic(
        model=model or settings.rr_analyst_model,
        api_key=settings.anthropic_api_key,
        max_tokens=settings.rr_analyst_max_output_tokens,
        streaming=True,
        stream_usage=True,
        **kwargs,
        temperature=settings.rr_analyst_temperature,
    )


def create_secondary_chat_model() -> ChatAnthropic:
    return ChatAnthropic(
        model=settings.rr_analyst_secondary_model,
        api_key=settings.anthropic_api_key,
        max_tokens=settings.rr_analyst_secondary_max_output_tokens,
        streaming=False,
        stream_usage=True,
        temperature=settings.rr_analyst_secondary_temperature,
    )
