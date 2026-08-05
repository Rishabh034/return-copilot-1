import os

from dotenv import load_dotenv

from app.utilities.secrets_manager_client import get_secret

load_dotenv()

# Keys that must be served by AWS Secrets Manager once removed from .env
REQUIRED_AWS_KEYS = ["CLICKHELP_ANTHROPIC_API_KEY", "CLICKHELP_POSTGRES_PASSWORD", "CLICKHELP_DATABASE_URL"]

# Fetch secrets from multiple AWS Secrets Manager entries
aws_secrets = {}

for secret_name in ["DB_CREDS", "ACCESS_KEYS", "API_KEYS"]:
    try:
        aws_secrets.update(get_secret(secret_name))
    except Exception as e:
        print(f"[CREDS] secret={secret_name}: FETCH FAILED reason={type(e).__name__}")

if not aws_secrets:
    print("[CREDS] AWS Secrets Manager loaded nothing; relying on env/.env only")

_missing = [k for k in REQUIRED_AWS_KEYS if k not in aws_secrets]
if _missing:
    print(f"[CREDS] WARNING: required keys not found in AWS Secrets Manager: {_missing}; app will break if they are also absent from env/.env")


def get_credential(key):
    """
    Returns the credential by checking:
    1. Environment variable (.env)
    2. AWS Secrets Manager fallback
    """
    return os.environ.get(key) or aws_secrets.get(key)
