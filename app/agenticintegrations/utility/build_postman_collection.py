import uuid
from http.client import responses as HTTP_STATUS_TEXT
from urllib.parse import urlsplit, parse_qsl
from typing import Optional

from app.agenticintegrations.models.carrier_api_testing.api_test_log import APITestLog
from app.agenticintegrations.models.carrier_api_testing.result_collector import ResultCollector

SERVICE_DISPLAY_NAMES = {
    "order_creation": "Order Creation",
    "order_cancellation": "Order Cancellation",
    "tracking_polling": "Tracking - Polling",
    "tracking_webhook": "Tracking - Webhook",
}

SERVICE_ORDER = ["order_creation", "order_cancellation", "tracking_polling", "tracking_webhook"]


def _headers_to_postman(headers: Optional[dict]) -> list[dict]:
    if not headers:
        return []
    return [{"key": str(k), "value": str(v), "type": "text"} for k, v in headers.items()]


def _url_to_postman(url: str) -> dict:
    parts = urlsplit(url)
    path_segments = [seg for seg in parts.path.split("/") if seg]
    query_params = [{"key": k, "value": v} for k, v in parse_qsl(parts.query)]
    return {
        "raw": url,
        "protocol": parts.scheme or "https",
        "host": parts.netloc.split(".") if parts.netloc else [],
        "path": path_segments,
        **({"query": query_params} if query_params else {}),
    }


def _body_to_postman(body: Optional[str], api_type: str) -> dict:
    language = "xml" if api_type == "xml" else "json"
    return {
        "mode": "raw",
        "raw": body or "",
        "options": {"raw": {"language": language}},
    }


def _status_text(status_code: Optional[int]) -> str:
    if status_code is None:
        return "No Response"
    return HTTP_STATUS_TEXT.get(status_code, "Unknown")


def _build_request_item(result: APITestLog) -> dict:
    request_obj = {
        "method": result.method.upper(),
        "header": _headers_to_postman(result.request_headers),
        "body": _body_to_postman(result.request_body, result.api_type),
        "url": _url_to_postman(result.url),
    }

    item = {
        "name": result.endpoint_name,
        "request": request_obj,
        "response": [
            {
                "name": f"Saved Response - {'Success' if result.success else 'Failure'}",
                "originalRequest": request_obj,
                "status": _status_text(result.status_code),
                "code": result.status_code if result.status_code is not None else 0,
                "_postman_previewlanguage": "xml" if result.api_type == "xml" else "json",
                "header": [],
                "body": result.response_body or "",
            }
        ],
    }
    return item


def build_postman_collection(collector: ResultCollector, collection_name) -> dict:
    folders: dict[str, list[dict]] = {}
    for result in collector.results:
        folders.setdefault(result.service, []).append(_build_request_item(result))

    ordered_service_keys = [s for s in SERVICE_ORDER if s in folders]
    ordered_service_keys += [s for s in folders if s not in SERVICE_ORDER]  # any unexpected services, appended last

    collection_items = [
        {
            "name": SERVICE_DISPLAY_NAMES.get(service_key, service_key),
            "item": folders[service_key],
        }
        for service_key in ordered_service_keys
    ]

    return {
        "info": {
            "_postman_id": str(uuid.uuid4()),
            "name": f"{collection_name}",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": collection_items,
    }