from dataclasses import dataclass


@dataclass
class APITestLog:
    api_type: str
    url: str
    method: str
    request_headers: dict
    request_body: str
    status_code: int
    response_body: str
    endpoint_name: str = None
    service: str = None
    success: bool = None
    failure_reason: str = None
    resolution_steps: str = None



