import requests


def run(tool_input: dict, additional_tool_params: dict = None):
    request_type = tool_input["request_type"]
    url = tool_input["url"]
    headers = tool_input["headers"]
    body = tool_input["body"]

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

    return response_body



