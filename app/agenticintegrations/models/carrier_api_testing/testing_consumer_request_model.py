from pydantic import BaseModel


class TestingConsumerRequest(BaseModel):
    integration_type: str
    integration_region: str
    integration_delivery_type: str
    api_document_type: str
    api_document: list[str]
    carrier_partner_name: str
    test_credentials: dict
    jira_ticket_id: str
