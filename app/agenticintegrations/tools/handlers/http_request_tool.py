import logging

import requests

from app.agenticintegrations.models.carrier_api_testing.result_collector import ResultCollector

logger = logging.getLogger(__name__)


def run(tool_input: dict, additional_tool_params: dict = None):
    collector: ResultCollector = additional_tool_params["collector"]

    request_type = tool_input["request_type"]
    url = tool_input["url"]
    headers = tool_input["headers"]
    body = tool_input["body"]

    if headers.get("Content-Type") == "application/json":
        api_type = "json"
    elif headers.get("Content-Type") == "application/xml":
        api_type = "xml"
    else:
        api_type = "unknown"

    if request_type == "GET":
        response = requests.get(url=url, headers=headers, timeout=30)
        response_body = response.content.decode()
    elif request_type == "POST":
        response = requests.post(url=url, headers=headers, data=body, timeout=30)
        response_body = response.content.decode()
    elif request_type == "PUT":
        response = requests.put(url=url, headers=headers, data=body, timeout=30)
        response_body = response.content.decode()
    else:
        raise Exception("Invalid request type passed to tool, no handler found")

    test_result = {
        "url": url,
        "method": request_type,
        "request_headers": headers,
        "request_body": body,
        "api_type": api_type,
        "status_code": response.status_code,
        "response_body": response_body
    }
    # If the agent is retrying the request, update the test result for that endpoint
    # instead of creating a new one.
    updated = collector.update(updates=test_result)
    if not updated:
        logger.info("Test result created in tool for endpoint: %s", url)
        collector.add(data=test_result)
    else:
        logger.info("Agent retry - Test result updated in tool for endpoint: %s", url)

    return response_body



