from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    clickpost_base_url: str = "https://www.clickpost.in"
    clickpost_api_key: str = ""

    rr_analyst_model: str = "claude-sonnet-4-20250514"
    rr_analyst_max_output_tokens: int = 4096
    rr_analyst_max_iterations: int = 10
    rr_analyst_tool_timeout: int = 30

    max_context_tokens: int = 200_000
    reserved_output_tokens: int = 4096
    protected_tail_messages: int = 4

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
