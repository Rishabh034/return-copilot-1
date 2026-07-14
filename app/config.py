from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Required
    anthropic_api_key: str
    database_url: str
    clickpost_base_url: str = "https://internal.clickpost.in"

    rr_analyst_model: str = "claude-sonnet-4-6"
    rr_analyst_max_output_tokens: int = 8192
    rr_analyst_max_iterations: int = 10
    rr_analyst_tool_timeout: int = 30

    rr_analyst_thinking_enabled: bool = True
    rr_analyst_thinking_budget_tokens: int = 2048

    rr_analyst_secondary_model: str = "claude-haiku-4-5"
    rr_analyst_secondary_max_output_tokens: int = 8192
    rr_analyst_scratchpad_enabled: bool = True
    scratchpad_chunk_chars: int = 12_000  # max chars per raw data part / Haiku summarization call
    scratchpad_inline_max_chars: int = 80_000
    tool_result_query_max_chars: int = 8_000

    rr_analyst_dev_token: str | None = None

    max_context_tokens: int = 200_000
    reserved_output_tokens: int = 16384
    protected_tail_messages: int = 4
    slack_token: str | None = None
    slack_channel: str = "clickhelp-conversations"
    allowed_origins: str = "https://alerts.clickpost.in"  # comma-separated list of allowed CORS origins

    # Log shipping: write logs to hourly-rotated files and upload them to S3.
    # Off by default so local dev stays stdout-only; set LOG_TO_FILE=true in .env
    # on the server. AWS credentials come from boto3's default chain
    # (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION env vars,
    # or the EC2 instance role).
    log_to_file: bool = False
    log_dir: str = "logs"
    log_s3_bucket: str = "clickpost-ai-agent-recom-clickhelp-logs"
    log_s3_prefix: str = "logs/clickpost-ai-projects"
    log_upload_interval_minutes: int = 10
    capture_access_logs: bool = True  # route uvicorn access/server logs through our logger

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()