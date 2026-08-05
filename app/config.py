import os

from pydantic import field_validator
from pydantic_settings import BaseSettings

from app.utilities.creds import aws_secrets

# Seed the environment with values fetched from AWS Secrets Manager so
# BaseSettings can read them. This account's secrets are CLICKHELP_-prefixed
# and shared across every clickpost service (confirmed 2026-08-05 - DB_CREDS/
# ACCESS_KEYS/API_KEYS
_AWS_SECRET_TO_ENV = {
    "CLICKHELP_ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
    "CLICKHELP_DATABASE_URL": "DATABASE_URL",
    "CLICKHELP_POSTGRES_PASSWORD": "POSTGRES_PASSWORD",
    "CLICKHELP_AI_AGENT_JWT_PUBLIC_KEY": "AGENT_JWT_PUBLIC_KEY",
}

_from_aws = []
_from_env = []
for _aws_key, _target in _AWS_SECRET_TO_ENV.items():
    if _aws_key not in aws_secrets:
        continue
    # empty env values (e.g. a leftover `DATABASE_URL=` line in .env) count
    # as unset — never let them mask a real value from Secrets Manager
    if not os.environ.get(_target):
        os.environ[_target] = str(aws_secrets[_aws_key])
        _from_aws.append(_target)
    else:
        _from_env.append(_target)

if _from_aws:
    print(f"[CREDS] using AWS Secrets Manager values for: {sorted(_from_aws)}")
if _from_env:
    print(f"[CREDS] env/.env overrides AWS values for: {sorted(_from_env)}")


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
    rr_analyst_secondary_max_output_tokens: int = 4048
    rr_analyst_scratchpad_enabled: bool = True
    scratchpad_chunk_chars: int = 12_000  # max chars per raw data part / Haiku summarization call
    scratchpad_inline_max_chars: int = 80_000
    tool_result_query_max_chars: int = 8_000

    rr_analyst_dev_token: str | None = None

    # Agent JWT verification (RS256): tokens are minted by clickpost Django
    # (POST /api/v1/ai-agent/token/, behind its own DRF auth) and carry
    # user_id/enterprise_id/cp_token as signed claims. we only ever hold
    # the public half of the key pair - this service can verify a token but
    # never mint one. See app/dependencies/auth.py::require_auth.
    # false for local testing
    agent_jwt_enabled: bool = False
    agent_jwt_public_key: str | None = None  # PEM-encoded RSA public key
    agent_jwt_issuer: str = "clickpost"
    agent_jwt_audience: str = "clickpost-ai-agent"

    @field_validator("agent_jwt_public_key")
    @classmethod
    def _unescape_pem_newlines(cls, v: str | None) -> str | None:
        # Some secret stores/`.env` files can't hold real newlines, so the PEM
        # is flattened to a single line with literal `\n` — reverse that here.
        return v.replace("\\n", "\n") if v else v

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