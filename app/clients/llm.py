from langchain_anthropic import ChatAnthropic

from app.config import settings


def create_chat_model(model: str | None = None) -> ChatAnthropic:
    return ChatAnthropic(
        model=model or settings.rr_analyst_model,
        api_key=settings.anthropic_api_key,
        max_tokens=settings.rr_analyst_max_output_tokens,
        streaming=True,
        stream_usage=True,
    )
