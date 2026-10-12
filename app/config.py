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
    "CLICKHELP_LANGFUSE_PUBLIC_KEY": "LANGFUSE_PUBLIC_KEY",
    "CLICKHELP_LANGFUSE_SECRET_KEY": "LANGFUSE_SECRET_KEY",
    # Not CLICKHELP_-prefixed: the same keys clickpost's scripts_settings_v2
    # reads for its Databricks jobs (app/clients/databricks.py).
    "DATABRICKS_DB_HOST_NAME": "DATABRICKS_DB_HOST_NAME",
    "DATABRICKS_HTTP_PATH": "DATABRICKS_HTTP_PATH",
    "DATABRICKS_ACCESS_TOKEN": "DATABRICKS_ACCESS_TOKEN",
    # This service's own Databricks service principal (OAuth M2M); preferred
    # over the shared token above whenever both halves are present.
    "CLICKHELP_CLIENT_ID": "DATABRICKS_CLIENT_ID",
    "CLICKHELP_SECRET_KEY": "DATABRICKS_CLIENT_SECRET",
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
    rr_analyst_temperature: float = 0.1
    rr_analyst_secondary_temperature :float = 0.3
    rr_analyst_max_output_tokens: int = 8192
    rr_analyst_max_iterations: int = 10
    rr_analyst_tool_timeout: int = 30

    rr_analyst_thinking_enabled: bool = False
    rr_analyst_thinking_budget_tokens: int = 2048

    prompt_caching_enabled: bool = True

    rr_analyst_secondary_model: str = "claude-haiku-4-5"
    rr_analyst_secondary_max_output_tokens: int = 4048
    rr_analyst_scratchpad_enabled: bool = True
    scratchpad_chunk_chars: int = 48_000  
    scratchpad_min_result_chars: int = 20_000
    scratchpad_passthrough_max_chars: int = 400
    scratchpad_max_concurrent_summaries: int = 8
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

    # Enterprise spend caps. Money is integer micro-dollars throughout
    # (1 micro = $0.000001); see app/core/billing/pricing.py.
    #
    # The reservation is a hold placed before a turn runs, since a turn's real
    # cost is unknown until it finishes. It is admission control only and never
    # decides what is charged - a wrong value makes a 429 arrive slightly early
    # or slightly late, never a wrong figure in the ledger. $0.50 sits well
    # above a typical turn (~$0.19-0.34) and well under the worst case (~$1.81).
    # Who may READ the cross-enterprise budget dashboard.
    #
    # False (default): an explicit agent_admin row is required, even from a
    # verified ClickPost staff member.
    # True: any active staff member reads it, no row needed.
    #
    # This exists so the decision is made here rather than inherited from
    # another team's deploy. Clickpost marks every internal account is_staff -
    # CSM, support, product, sales - so signing that claim would otherwise open
    # every customer's spend to the whole company the moment it ships. Writes
    # are unaffected: they always require a manager row.
    budget_dashboard_open_to_all_staff: bool = False

    budget_default_limit_micros: int = 25_000_000  # $25.00 per enterprise per month
    budget_reservation_estimate_micros: int = 500_000  # $0.50 held per turn
    # Off by default so deploying this feature blocks nobody. Enforcement is
    # switched on per enterprise via enterprise_budget.enforcement_enabled.
    budget_enforcement_default: bool = False

    # Integration API keys (/integrations/query). These callers are internal
    # ClickPost services, billed per key rather than per enterprise, so none of
    # the settings above apply to them.
    #
    # A key is created with no cap and stays uncapped until someone sets
    # integration_api_key.monthly_limit_micros, so the hold below is inert on
    # day one. It is still taken, because taking it only once a cap exists would
    # leave the first capped month running on an untested path.
    integration_reservation_estimate_micros: int = 500_000  # $0.50 held per request
    # Cheap Clickpost round trip that rejects a bad license key before it costs
    # a model run; see services/license_key_check.py for why it fails open.
    integration_preflight_enabled: bool = True
    integration_license_key_ttl_seconds: int = 300

    max_context_tokens: int = 200_000
    reserved_output_tokens: int = 16384
    protected_tail_messages: int = 4
    slack_token: str | None = None
    slack_channel: str = "clickhelp-conversations"
    # Incoming-webhook URL used to alert on unhandled backend exceptions
    # (app/utilities/alerts.py). 
    slack_error_webhook_url: str | None = None
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

    # LLM tracing via self-hosted Langfuse. Off by default so local dev and any
    # environment without a Langfuse instance/keys configured is unaffected.
    langfuse_enabled: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str = "http://10.100.41.62:3000"

    # Distributed tracing via OpenTelemetry (app/clients/observability.py),
    # exported over OTLP/gRPC to a collector (SigNoz): FastAPI request spans,
    # with httpx (ClickpostClient), SQLAlchemy query, and raw Anthropic SDK
    # (LLM call) spans nested underneath. Off by default so local dev without
    # a collector running is unaffected.
    otel_enabled: bool = False
    otel_service_name: str = "clickpost-ai-projects"
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"

    # Direct Databricks SQL reads for agent tools (app/clients/databricks.py),
    # used where a Clickpost API times out or doesn't expose the columns a tool
    # needs. Unset = the client reports itself unconfigured; nothing else breaks.
    databricks_db_host_name: str | None = None
    databricks_http_path: str | None = None
    databricks_access_token: str | None = None
    # Service principal OAuth credentials; used instead of the token when both are set.
    databricks_client_id: str | None = None
    databricks_client_secret: str | None = None
    # Enforced by the warehouse, which cancels the statement. Kept at the tool
    # timeout above: past it the runner has already given up on the tool, and
    # a query still running in its worker thread is only spending compute.
    databricks_statement_timeout_seconds: int = 30
    databricks_max_rows: int = 5000  # per query, so a missing LIMIT can't flood a tool

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()