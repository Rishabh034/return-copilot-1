import base64
import logging

import requests

from app.agenticintegrations.utility.load_creds import settings

logger = logging.getLogger(__name__)


class JiraService:
    def __init__(self):
        self.__jira_user = settings.AGENTIC_INTEGRATIONS_JIRA_USER
        self.__jira_token = settings.AGENTIC_INTEGRATIONS_JIRA_TOKEN

        self._auth_string = base64.b64encode(f"{self.__jira_user}:{self.__jira_token}".encode("utf-8"))
        self._base_url = "https://clickpost.atlassian.net"

    def _get_auth_header(self):
        return {"Authorization": "Basic {}".format(self._auth_string)}

    def post_comment(self, ticket_id, comment_body):
        url = self._base_url + "/rest/api/3/issue/{}/comment".format(ticket_id)
        headers = self._get_auth_header().update({"Content-Type": "application/json"})
        data = {
            "body": comment_body
        }

        response = requests.post(url, headers, data=data)
        logger.info("Jira response status: %d, and body: %s", response.status_code, response.text)

        if response.status_code == 201:
            return True

        return False


