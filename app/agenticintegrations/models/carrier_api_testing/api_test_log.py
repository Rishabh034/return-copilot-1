from dataclasses import dataclass


@dataclass
class APITestLog:
    endpoint_name: str
    api_type: str
    url: str
    service: str
    method: str
    request_headers: dict
    request_body: str
    status_code: int
    response_body: str
    success: bool
    failure_reason: str
    resolution_steps: str



