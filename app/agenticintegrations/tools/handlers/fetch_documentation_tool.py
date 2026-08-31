import logging

import requests

logger = logging.getLogger(__name__)


def run(tool_input: dict, additional_tool_params: dict = None):
    url = tool_input["url"]
    response = requests.get(url=url, timeout=30)
    logger.info("Fetched documentation content from: %s (status %d)", url, response.status_code)
    return response.content.decode(errors="replace")
