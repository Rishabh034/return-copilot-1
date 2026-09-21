import os
from pathlib import Path

from dotenv import load_dotenv

from app.utilities.secrets_manager_client import get_secret

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

def _load_secrets():
    app_env = os.environ.get("APP_ENVIRONMENT", "TEST")
    secrets = {}
    secret_names = [
        "AGENTIC_INTEGRATIONS_ANTHROPIC_API_KEY",
        "AGENTIC_INTEGRATIONS_AWS_ACCESS_KEY_ID",
        "AGENTIC_INTEGRATIONS_AWS_SECRET_ACCESS_KEY",
        "AGENTIC_INTEGRATIONS_JIRA_USER",
        "AGENTIC_INTEGRATIONS_JIRA_TOKEN",
        "README_API_KEY",
        "ONBOARDING_SLACK_WEBHOOK_URL",
        "CLICKPOST_REPO_PATH",
    ]

    if app_env == "PROD":
        secrets.update(get_secret(secret_name="API_KEYS"))

    else:
        for name in secret_names:
            secrets.update({name: os.environ.get(name)})

    return secrets


class Settings:
    """Plain namespace holding config values, loaded once at import time."""
    pass


settings = Settings()

_secrets = _load_secrets()
for _key, _value in _secrets.items():
    setattr(settings, _key, _value)

# Set additional configs
settings.DEFAULT_S3_BUCKET = "pyck-res-bucket"
settings.ONBOARDING_CONTEXT_MAX_CHARS = 120_000
settings.README_API_BASE_URL = "https://api.readme.com/v2"
settings.README_DOCS_BASE_URL = "https://docs.clickpost.ai"
settings.README_BRANCH = "stable"
settings.README_CATEGORY_FROM_SLUG = "create-an-courier-partner-account"
settings.README_CATEGORY_SLUG = None
settings.README_CATEGORY_URI = None
settings.README_PARENT_SLUG = None
settings.README_PUBLISH_HIDDEN = True
settings.README_PUBLISH_ENABLED = True
# clean up temp names so they don't leak into `from myconfig.settings import *`
del _secrets, _key, _value



