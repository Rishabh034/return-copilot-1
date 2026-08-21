from pydantic import BaseModel


class ScopingConsumerRequest(BaseModel):
    integration_type: str
    integration_region: str
    integration_delivery_type: str
    api_document_type: str
    api_document: list[str]
    test_credentials: dict
    carrier_partner_name: str
    jira_ticket_id: str
    additional_information: str | None = None
