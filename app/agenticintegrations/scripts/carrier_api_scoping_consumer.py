import json
import logging
import os
import sys
import time

logging.basicConfig(
    level="INFO",
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout
)

import boto3
from pydantic import ValidationError

module_path = os.path.join((os.path.dirname(os.getcwd())), 'clickpost-ai-projects')
sys.path.append(module_path)

from app.agenticintegrations.utility.load_creds import settings
from app.agenticintegrations.agent import Agent
from app.agenticintegrations.client import ClaudeClient
from app.agenticintegrations.models.carrier_api_scoping.scoping_consumer_request_model import ScopingConsumerRequest
from app.agenticintegrations.models.carrier_api_scoping.scoping_output_schema import SCOPING_OUTPUT_SCHEMA
from app.agenticintegrations.utility.jira_service import JiraService
from app.agenticintegrations.utility.kafka_service import KafkaService

logger = logging.getLogger(__name__)


class CarrierAPIScopingConsumer:
    def __init__(self):
        self._agent = Agent(
            system_prompt_name="system_prompt_scoping_agent",
            client=ClaudeClient(
                api_key=settings.AGENTIC_INTEGRATIONS_ANTHROPIC_API_KEY,
                model="claude-sonnet-4-6",
                max_tokens=4096
            ),
            output_schema=SCOPING_OUTPUT_SCHEMA
        )
        self._jira_service = JiraService()

    def _upload_to_s3(self, local_file, s3_key_name):
        s3 = boto3.client(
            's3',
            aws_access_key_id=settings.AGENTIC_INTEGRATIONS_AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AGENTIC_INTEGRATIONS_AWS_SECRET_ACCESS_KEY
        )

        s3.upload_file(
            local_file,
            settings.DEFAULT_S3_BUCKET,
            s3_key_name
        )

    def _prepare_scoping_output_file(self, scoping_output: dict, carrier_partner_name: str):
        file_path = f"/tmp/scoping_output_{carrier_partner_name}_{time.time()}.json"
        with open(file_path, "w") as fobj:
            json.dump(scoping_output, fobj)

        # Upload to S3
        s3_key = f"agentic_integrations/{carrier_partner_name.upper()}/scoping_output.json"
        self._upload_to_s3(local_file=file_path, s3_key_name=s3_key)
        scoping_output_file = f"https://{settings.DEFAULT_S3_BUCKET}.s3.ap-southeast-1.amazonaws.com/{s3_key}"

        return scoping_output_file

    @staticmethod
    def _paragraph(text: str) -> dict:
        return {
            "type": "paragraph",
            "content": [{"type": "text", "text": text}]
        }

    @staticmethod
    def _table_cell(text, header: bool = False) -> dict:
        return {
            "type": "tableHeader" if header else "tableCell",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": str(text)}]}]
        }

    def _prepare_open_queries_table(self, open_queries: list) -> dict:
        header_row = {
            "type": "tableRow",
            "content": [
                self._table_cell("Question", header=True),
                self._table_cell("Why Blocking", header=True),
                self._table_cell("Related Endpoint", header=True),
            ]
        }
        data_rows = [
            {
                "type": "tableRow",
                "content": [
                    self._table_cell(query.get("question", "")),
                    self._table_cell(query.get("why_blocking", "")),
                    self._table_cell(query.get("related_endpoint") or "N/A"),
                ]
            }
            for query in open_queries
        ]

        return {
            "type": "table",
            "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
            "content": [header_row] + data_rows
        }

    def _prepare_jira_comment(self, open_queries: list, scoping_output_file: str) -> dict:
        if open_queries:
            content = [self._prepare_open_queries_table(open_queries)]
        else:
            content = [self._paragraph(
                "No open queries found while scoping, integration is ready for code generation"
            )]

        content.append(self._paragraph(f"Scoping output: {scoping_output_file}"))

        return {
            "version": 1,
            "type": "doc",
            "content": content
        }

    def consume(self, topic_payload: dict):
        try:
            scoping_request = ScopingConsumerRequest(**topic_payload)
            logger.info("Processing scoping request for carrier: %s", scoping_request.carrier_partner_name)
            content = list()
            document_count = len(scoping_request.api_document)

            if scoping_request.api_document_type == "PDF":
                user_prompt = f"""
                The carrier documentation is available as {document_count} PDF file(s) which have been provided to you.
                Integration type: {scoping_request.integration_type}
                Integration region: {scoping_request.integration_region}
                Integration delivery type: {scoping_request.integration_delivery_type}
                The test credentials are as follows: <data>{json.dumps(scoping_request.test_credentials)}</data>
                """
                content = [
                    {
                        "type": "document",
                        "source": {
                            "type": "url",
                            "url": url
                        },
                        "title": f"Carrier documentation file {index} of {document_count}"
                    }
                    for index, url in enumerate(scoping_request.api_document, start=1)
                ]
                content.append({
                    "type": "text",
                    "text": user_prompt
                })
            elif scoping_request.api_document_type == "CURL":
                user_prompt = f"""
                The carrier documentation is available as {document_count} text file(s) containing the request cURLS,
                which have been provided to you.
                Integration type: {scoping_request.integration_type}
                Integration region: {scoping_request.integration_region}
                Integration delivery type: {scoping_request.integration_delivery_type}
                The test credentials are as follows: <data>{json.dumps(scoping_request.test_credentials)}</data>
                """
                content = [
                    {
                        "type": "document",
                        "source": {
                            "type": "url",
                            "url": url
                        },
                        "title": f"Carrier documentation file {index} of {document_count}"
                    }
                    for index, url in enumerate(scoping_request.api_document, start=1)
                ]
                content.append({
                    "type": "text",
                    "text": user_prompt
                })
            elif scoping_request.api_document_type == "LINK":
                documentation_links = "\n".join(
                    f"{index}. {url}" for index, url in enumerate(scoping_request.api_document, start=1)
                )
                user_prompt = f"""
                The carrier documentation is available as {document_count} documentation link(s) on the web.
                The documentation links are:
                {documentation_links}
                Integration type: {scoping_request.integration_type}
                Integration region: {scoping_request.integration_region}
                Integration delivery type: {scoping_request.integration_delivery_type}
                The test credentials are as follows: <data>{json.dumps(scoping_request.test_credentials)}</data>
                """
                content = [
                    {
                        "type": "text",
                        "text": user_prompt
                    }
                ]
            else:
                logger.info("No matching API document type found")

            if content:
                logger.info("Invoking scoping agent for carrier: %s (%d document(s))",
                            scoping_request.carrier_partner_name, document_count)
                result = self._agent.run_structured(user_input=content)
                logger.info("Received structured scoping output for carrier: %s",
                            scoping_request.carrier_partner_name)

                open_queries = result.get("open_queries", [])
                logger.info("Open queries found: %d", len(open_queries))
                result.pop("open_queries", None)

                scoping_output_file = self._prepare_scoping_output_file(
                    scoping_output=result, carrier_partner_name=scoping_request.carrier_partner_name)
                logger.info("Scoping output uploaded to: %s", scoping_output_file)

                jira_comment_data = self._prepare_jira_comment(open_queries, scoping_output_file)
                jira_comment_success = self._jira_service.post_comment(
                    ticket_id=scoping_request.jira_ticket_id, comment_body=jira_comment_data)
                logger.info("Jira comment post status: %s", str(jira_comment_success))

                print(json.dumps(result, indent=2))
        except ValidationError as err:
            logger.error("Invalid scoping request. Validation error details: %s", str(err))


def callback(body):
    consumer = CarrierAPIScopingConsumer()
    consumer.consume(json.loads(body))


KafkaService.start_consumer(
    topic="agentic_integrations_carrier_scoping",
    group_id="agentic_integrations_carrier_scoping_consumer",
    consumer_callback=callback
)

# Local testing
# consumer = CarrierAPIScopingConsumer()
# payload = {
#     "integration_type": "SPS",
#     "integration_region": "DOMESTIC",
#     "integration_delivery_type": "FORWARD",
#     "api_document_type": "PDF",
#     "api_document": [
#         "https://pyck-res-bucket.s3.ap-southeast-1.amazonaws.com/courier_documentation/beingships-documentation.pdf"
#     ],
#     "test_credentials": {
#         "api_key": "Lorem ipsum"
#     },
#     "carrier_partner_name": "Being Ships",
#     "jira_ticket_id": "CPD-9403"
# }
# consumer.consume(payload)
