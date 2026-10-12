from pydantic import BaseModel, field_validator


class OnboardingConsumerRequest(BaseModel):
    cp_id: int
    doc_links: list[str]
    existing_doc_links: str = ""
    courier_name: str | None = None
    additional_information: str | None = None

    @field_validator("existing_doc_links", mode="before")
    @classmethod
    def _normalize_existing_doc_links(cls, value):
        if value is None:
            return ""
        if isinstance(value, str):
                return value.strip()
        raise ValueError("existing_doc_links must be a string")
