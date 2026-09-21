import json
import logging
import os
import re
import sys

logging.basicConfig(
    level="INFO",
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)

module_path = os.path.join((os.path.dirname(os.getcwd())), "clickpost-ai-projects")
sys.path.append(module_path)

from pydantic import ValidationError

from app.agenticintegrations.client import ClaudeClient
from app.agenticintegrations.models.carrier_onboarding.onboarding_consumer_request_model import (
    OnboardingConsumerRequest,
)
from app.agenticintegrations.prompts.loader import load_base_prompt
from app.agenticintegrations.utility.context_builder import build_courier_context
from app.agenticintegrations.utility.kafka_service import KafkaService
from app.agenticintegrations.utility.load_creds import settings
from app.agenticintegrations.utility.locator import CourierNotFoundError, locate_courier
from app.agenticintegrations.utility.readme_client import (
    ExistingPage,
    ReadMeError,
    ReadMeService,
)
from app.agenticintegrations.utility.repo import ClickpostRepoError, pull_clickpost_repo, require_clickpost_repo
from app.agenticintegrations.utility.slack_service import SlackService

logger = logging.getLogger(__name__)


class OnboardingReadmeConsumer:
    def __init__(self):
        self._client = ClaudeClient(
            api_key=getattr(settings, "AGENTIC_INTEGRATIONS_ANTHROPIC_API_KEY", None),
            model="claude-sonnet-4-6",
            max_tokens=4096,
        )
        self._slack_service = SlackService()
        self.readme_service = ReadMeService()

    @staticmethod
    def _slugify(value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
        return slug.strip("-")

    @staticmethod
    def _doc_link_block(doc_links: list[str]) -> str:
        if len(doc_links) == 1:
            return doc_links[0]
        return "\n".join(f"- {link}" for link in doc_links)

    def _system_prompt(self, cpid: int, doc_links: list[str]) -> str:
        prompt = load_base_prompt("onboarding_readme_system")
        return prompt.replace("{cpid}", str(cpid)).replace("{doc_link}", self._doc_link_block(doc_links))

    def _user_content(
        self,
        courier_name: str,
        cpid: int,
        courier_context: str,
        doc_links: list[str],
        existing_markdown: str | None = None,
    ) -> str:
        parts = [
            f"Courier partner: {courier_name} (CPID: {cpid})\n",
            f"## Known context\n{courier_context}\n",
            f"## Documentation links\n{self._doc_link_block(doc_links)}\n",
        ]
        if existing_markdown:
            parts.append(
                "## Existing README (already published — update in place, keep the same URL)\n"
                "Read this document, keep accurate content, fill any missing sections or "
                "information from the context bundle and partner docs, and output the full "
                "updated markdown.\n\n"
                f"{existing_markdown}\n"
            )
        return "\n".join(parts)

    @staticmethod
    def _extract_text(content_blocks) -> str:
        return "\n".join(block.text for block in content_blocks if block.type == "text").strip()

    def _generate_readme(self, courier_name, cpid, courier_context, doc_links, existing_markdown=None):
        user_prompt = self._user_content(
            courier_name, cpid, courier_context, doc_links, existing_markdown
        )
        logger.info(
            "Invoking onboarding readme model for cp_id=%s update_existing=%s",
            cpid,
            bool(existing_markdown),
        )
        response = self._client.create_message(
            messages=[{"role": "user", "content": user_prompt}],
            system=self._system_prompt(cpid, doc_links),
            temperature=0.2,
        )
        return self._extract_text(response.content)

    def _load_existing_page(self, existing_doc_link: str) -> ExistingPage | None:
        if not existing_doc_link:
            return None
        return self.readme_service.resolve_existing_page(existing_doc_link)

    def consume(self, topic_payload: dict):
        try:
            request = OnboardingConsumerRequest(**topic_payload)
            logger.info("Processing onboarding README request for cp_id=%s", request.cp_id)

            repo = require_clickpost_repo()
            pull_clickpost_repo(repo)
            location = locate_courier(repo, request.cp_id)
            courier_name = location.display_name
            courier_context = build_courier_context(repo, location)
            if request.additional_information:
                courier_context = (
                    f"{courier_context}\n\n## Additional information\n{request.additional_information}\n"
                )
            logger.info("Courier context: %s", courier_context)
            existing_page = self._load_existing_page(request.existing_doc_links)
            if existing_page:
                logger.info(
                    "Updating existing onboarding README for cp_id=%s slug=%s url=%s has_body=%s",
                    request.cp_id,
                    existing_page.slug,
                    existing_page.page_url,
                    bool(existing_page.markdown_text),
                )

            markdown = self._generate_readme(
                courier_name,
                request.cp_id,
                courier_context,
                request.doc_links,
                existing_markdown=existing_page.markdown_text if existing_page else None,
            )

            readme_page_url = None
            if settings.README_PUBLISH_ENABLED:
                if existing_page:
                    published = self.readme_service.update_page(
                        title=courier_name,
                        markdown_text=markdown,
                        slug=existing_page.slug,
                    )
                else:
                    published = self.readme_service.create_page(
                        title=courier_name,
                        markdown_text=markdown,
                        slug=self._slugify(courier_name) or f"cpid-{request.cp_id}",
                    )
                readme_page_url = published.page_url
                logger.info(
                    "Published onboarding README for cp_id=%s to %s updated=%s hidden=%s",
                    request.cp_id,
                    published.page_url,
                    published.updated,
                    settings.README_PUBLISH_HIDDEN,
                )
            else:
                logger.info("README_PUBLISH_ENABLED is false - skipping ReadMe hub publish")

            slack_ok = self._slack_service.post_readme(
                courier_name=courier_name,
                cp_id=request.cp_id,
                readme_page_url=readme_page_url,
            )
            logger.info("Slack post status: %s", str(slack_ok))
        except ValidationError as err:
            logger.error("Invalid onboarding request. Validation error details: %s", str(err))
        except (ClickpostRepoError, CourierNotFoundError, ReadMeError) as err:
            logger.error("Onboarding README job failed: %s", err)
        except Exception:
            logger.exception("Unexpected onboarding README failure")


def callback(body):
    consumer = OnboardingReadmeConsumer()
    consumer.consume(json.loads(body))


if __name__ == "__main__":
    KafkaService.start_consumer(
        topic="carrier_onboarding_readme",
        group_id="carrier_onboarding_readme_consumer",
        consumer_callback=callback,
        max_poll_interval_ms=900_000,
    )

# Local testing
# consumer = OnboardingReadmeConsumer()
# payload = {
#     "cp_id": 730,
#     "doc_links": ["https://staging.supermile.ai/api/docs/"],
#     "existing_doc_links": "",
# }
# consumer.consume(payload)
