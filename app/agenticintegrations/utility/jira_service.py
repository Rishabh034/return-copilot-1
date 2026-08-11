import base64
import json
import logging

import requests

from app.agenticintegrations.utility.load_creds import settings

logger = logging.getLogger(__name__)


class JiraService:
    def __init__(self):
        self.__jira_user = settings.AGENTIC_INTEGRATIONS_JIRA_USER
        self.__jira_token = settings.AGENTIC_INTEGRATIONS_JIRA_TOKEN

        self._auth_string = base64.b64encode(
            f"{self.__jira_user}:{self.__jira_token}".encode("utf-8")
        ).decode("utf-8")
        self._base_url = "https://clickpost.atlassian.net"

    def _get_auth_header(self):
        return {"Authorization": "Basic {}".format(self._auth_string)}

    def post_comment(self, ticket_id, comment_body):
        url = self._base_url + "/rest/api/3/issue/{}/comment".format(ticket_id)
        headers = self._get_auth_header()
        headers["Content-Type"] = "application/json"
        request_data = {
            "body": comment_body
        }

        try:
            response = requests.post(url=url, headers=headers, data=json.dumps(request_data), timeout=60)
            logger.info("Jira response status: %d, and body: %s", response.status_code, response.text)

            if response.status_code == 201:
                return True
        except requests.exceptions.Timeout:
            logger.info("Jira request to post comment timed out")

        return False


