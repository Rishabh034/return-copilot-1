import os

from app.utilities.secrets_manager_client import get_secret


def _load_secrets():
    app_env = os.environ.get("APP_ENVIRONMENT", "TEST")
    secrets = {}
    secret_names = [
        "AGENTIC_INTEGRATIONS_ANTHROPIC_API_KEY",
        "AGENTIC_INTEGRATIONS_AWS_ACCESS_KEY_ID",
        "AGENTIC_INTEGRATIONS_AWS_SECRET_ACCESS_KEY",
        "AGENTIC_INTEGRATIONS_JIRA_USER",
        "AGENTIC_INTEGRATIONS_JIRA_TOKEN"
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

# clean up temp names so they don't leak into `from myconfig.settings import *`
del _secrets, _key, _value



