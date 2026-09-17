import logging

import requests

from app.agenticintegrations.utility.load_creds import settings

logger = logging.getLogger(__name__)

_MAX_MARKDOWN_CHARS = 35_000


class SlackService:
    def __init__(self):
        self._webhook = getattr(settings, "ONBOARDING_SLACK_WEBHOOK_URL", None)


    def post_readme(self, courier_name, cp_id, readme_page_url=None):
        if not self._webhook:
            logger.error(
                "ONBOARDING_SLACK_WEBHOOK_URL is not set - skipping Slack publish for cp_id=%s",
                cp_id,
            )
            return False
        try:
            response = requests.post(
                self._webhook,
                json={
                    "CPID": str(cp_id),
                    "ReadmeDoc": readme_page_url,
                    "CourierName": courier_name or "",
                },
                timeout=10,
            )
            logger.info("Slack response status: %d", response.status_code)
            if response.status_code < 400:
                return True
        except requests.exceptions.Timeout:
            logger.info("Slack request to post onboarding README timed out")
        except requests.RequestException:
            logger.exception("Failed to post onboarding README to Slack for cp_id=%s", cp_id)

        return False
