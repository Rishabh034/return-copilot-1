import json

PREFERRED_ROLE_ARNS = [
    "arn:aws:iam::869420678547:role/Role-ECR-Image-pullaccess",
    "arn:aws:iam::869420678547:role/aws-elasticbeanstalk-ec2-role",
    "arn:aws:iam::869420678547:role/aws-secret-manager-role-for-ec2"
]

PREFERRED_ROLE_NAMES = {arn.split("/")[-1] for arn in PREFERRED_ROLE_ARNS}

IMDS_BASE_URL = "http://169.254.169.254/latest"
IMDS_TOKEN_TTL = "21600"


def _get_imds_v2_token(timeout=2):
    from urllib.request import Request, urlopen
    req = Request(
        f"{IMDS_BASE_URL}/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": IMDS_TOKEN_TTL},
    )

    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _get_imds_text(path, token, timeout=2):
    from urllib.request import Request, urlopen
    req = Request(
        f"{IMDS_BASE_URL}/{path}",
        headers={"X-aws-ec2-metadata-token": token},
    )

    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _is_preferred_instance_role():
    from urllib.error import URLError, HTTPError
    """
    Checks if the attached EC2 IAM role is in the allowed list.
    Does NOT fetch credentials.
    """

    try:
        token = _get_imds_v2_token()

        role_name = _get_imds_text(
            "meta-data/iam/security-credentials/",
            token
        ).strip()

        if not role_name:
            print("IMDS reachable, but no IAM role attached.")
            return False

        if role_name not in PREFERRED_ROLE_NAMES:
            print(f"IAM role not allowed. Allowed={sorted(PREFERRED_ROLE_NAMES)}, Found={role_name}")
            return False

        print(f"Using allowed EC2 role: {role_name}")

        return True

    except (URLError, HTTPError, TimeoutError, OSError) as e:
        print(f"IMDS check failed. Falling back to default credentials. reason={type(e).__name__}")
        return False

    except Exception as e:
        print(f"Unexpected IMDS error. Falling back. reason={type(e).__name__}")
        return False


def _build_secretsmanager_client(region_name="ap-south-1"):
    import boto3
    """
    Builds Secrets Manager client safely.
    Uses default boto3 credential chain (auto-refresh),
    but only if role is allowed.
    """

    session = boto3.session.Session()

    if _is_preferred_instance_role():
        print("Using preferred EC2 role via boto3 default chain.")

        return session.client(
            "secretsmanager",
            region_name=region_name
        )

    print("Using fallback boto3 credential chain.")

    return session.client(
        "secretsmanager",
        region_name=region_name
    )


def get_secret(secret_name, region_name="ap-south-1"):
    client = _build_secretsmanager_client(
        region_name=region_name
    )

    response = client.get_secret_value(
        SecretId=secret_name
    )

    secret_dict = json.loads(
        response["SecretString"]
    )

    print(f"Successfully fetched secret={secret_name} from region={region_name}")

    return secret_dict
