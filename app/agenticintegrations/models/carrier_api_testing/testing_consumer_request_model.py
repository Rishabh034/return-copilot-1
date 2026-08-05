from pydantic import BaseModel


class TestingConsumerRequest(BaseModel):
    api_document_type: str
    api_document: str
    carrier_partner_name: str
    test_credentials: dict
    jira_ticket_id: str
