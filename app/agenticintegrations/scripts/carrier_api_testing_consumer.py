import csv
import json
import logging
import os
import sys
import time
from typing import List

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
from app.agenticintegrations.models.carrier_api_testing.testing_consumer_request_model import TestingConsumerRequest
from app.agenticintegrations.models.carrier_api_testing.api_test_log import APITestLog
from app.agenticintegrations.models.carrier_api_testing.result_collector import ResultCollector
from app.agenticintegrations.utility.build_postman_collection import build_postman_collection
from app.agenticintegrations.utility.jira_service import JiraService
from app.agenticintegrations.utility.kafka_service import KafkaService

logger = logging.getLogger(__name__)


class CarrierAPITestingConsumer:
    def __init__(self):
        self._agent = Agent(
            client=ClaudeClient(
                api_key=settings.AGENTIC_INTEGRATIONS_ANTHROPIC_API_KEY,
                model="claude-sonnet-4-6",
                max_tokens=4096
            )
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

    def _prepare_failed_requests_file(self, result_collector: ResultCollector, carrier_partner_name: str):
        results: List[APITestLog] = result_collector.get_results()
        failed_requests_file = "None"
        failed_requests_data = []
        for log in results:
            if not log.success:
                failed_requests_data.append([
                    log.url,
                    log.request_headers,
                    log.request_body,
                    log.status_code,
                    log.response_body,
                    log.failure_reason,
                    log.resolution_steps
                ])

        if failed_requests_data:
            file_path = f"/tmp/failed_requests_data_{carrier_partner_name}_{time.time()}.csv"
            with open(file_path, "r") as fobj:
                writer = csv.writer(fobj)

                writer.writerow([
                    "Request endpoint",
                    "Request headers",
                    "Request body",
                    "Response status code",
                    "Response body",
                    "Failure reason",
                    "Resolution steps"
                ])

                writer.writerows(failed_requests_data)

            # Upload to S3
            s3_key = f"agentic_integrations/{carrier_partner_name.upper()}/failed_requests_data_" \
                     f"{carrier_partner_name}_{time.time()}.csv"
            self._upload_to_s3(local_file=file_path, s3_key_name=s3_key)
            failed_requests_file = f"https://{settings.DEFAULT_S3_BUCKET}.s3.ap-southeast-1.amazonaws.com/{s3_key}"

        return failed_requests_file

    def _prepare_json_collection(self, collector: ResultCollector, carrier_partner_name: str):
        json_collection = build_postman_collection(collector=collector, collection_name=carrier_partner_name)
        file_path = f"/tmp/postman_collection_{carrier_partner_name}_{time.time()}.json"
        with open(file_path, "w") as fobj:
            json.dump(json_collection, fobj)

        # Upload to S3
        s3_key = f"agentic_integrations/{carrier_partner_name.upper()}/" \
                 f"postman_collection_{carrier_partner_name}_{time.time()}.json"
        self._upload_to_s3(local_file=file_path, s3_key_name=s3_key)
        postman_collection_file = f"https://{settings.DEFAULT_S3_BUCKET}.s3.ap-southeast-1.amazonaws.com/{s3_key}"

        return postman_collection_file

    def _prepare_jira_comment(self, failed_requests_file, postman_collection, agent_summary):
        return {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": f"{agent_summary}\nPostman collection: {postman_collection}\n"
                                    f"Failed requests: {failed_requests_file}"
                        }
                    ]
                }
            ]
        }

    def consume(self, topic_payload: dict):
        try:
            testing_request = TestingConsumerRequest(**topic_payload)
            result_collector = ResultCollector()
            content = list()
            if testing_request.api_document_type == "PDF":
                user_prompt = f"""
                The carrier documentation is available as a PDF which has been provided to you. 
                The test credentials are as follows: <data>{json.dumps(testing_request.test_credentials)}</data>
                """
                content = [
                    {
                        "type": "document",
                        "source": {
                            "type": "url",
                            "url": testing_request.api_document
                        }
                    },
                    {
                        "type": "text",
                        "text": user_prompt
                    }
                ]
            elif testing_request.api_document_type == "CURL":
                user_prompt = f"""
                The carrier documentation is available as a text file containing the request cURLS,
                which has been provided to you. The test crendentials are as follows: 
                <data>{json.dumps(testing_request.test_credentials)}</data>
                """
                content = [
                    {
                        "type": "document",
                        "source": {
                            "type": "url",
                            "url": testing_request.api_document
                        }
                    },
                    {
                        "type": "text",
                        "text": user_prompt
                    }
                ]
            elif testing_request.api_document_type == "LINK":
                user_prompt = f"""
                The carrier documentation is available as a documentation link on the web.
                The documentation link is: {testing_request.api_document}.
                The test credentials are as follows: <data>{json.dumps(testing_request.test_credentials)}</data>
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
                result = self._agent.run(
                    user_input=content,
                    additional_tool_params={"collector": result_collector}
                )
                failed_requests_file = self._prepare_failed_requests_file(
                    result_collector=result_collector, carrier_partner_name=testing_request.carrier_partner_name)
                postman_collection = self._prepare_json_collection(
                    collector=result_collector, carrier_partner_name=testing_request.carrier_partner_name)

                logger.info("Failed requests_file: %s", failed_requests_file)
                logger.info("Postman collection: %s", postman_collection)
                logger.info("Final output of the agent: %s", result)

                jira_comment_data = self._prepare_jira_comment(failed_requests_file, postman_collection, result)
                jira_comment_success = self._jira_service.post_comment(
                    ticket_id=testing_request.jira_ticket_id, comment_body=jira_comment_data)

                logger.info("Jira comment post status: %s", str(jira_comment_success))
        except ValidationError as err:
            logger.error("Invalid testing request. Validation error details: %s", str(err))


def callback(body):
    consumer = CarrierAPITestingConsumer()
    consumer.consume(json.loads(body))


KafkaService.start_consumer(
    topic="agentic_integrations_carrier_test",
    group_id="agentic_integrations_carrier_test_consumer",
    consumer_callback=callback
)

# Local testing
# consumer = CarrierAPITestingConsumer()
# payload = {
#     "api_document_type": "PDF",
#     "api_document":
#         "https://pyck-res-bucket.s3.ap-southeast-1.amazonaws.com/courier_documentation/beingships-documentation.pdf",
#     "test_credentials": {
#         "api_key": "Lorem ipsum"
#     },
#     "carrier_partner_name": "Being Ships",
#     "jira_ticket_id": "CPD-9403"
# }
# consumer.consume(payload)




