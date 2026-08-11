import logging

from app.agenticintegrations.models.carrier_api_testing.result_collector import ResultCollector

logger = logging.getLogger(__name__)


def run(tool_input: dict, additional_tool_params):
    collector: ResultCollector = additional_tool_params["collector"]
    updated = collector.update(updates=tool_input)

    if updated:
        logger.info("Test result updated with agent findings for endpoint: %s", tool_input["url"])
    else:
        logger.info("Test result could not be updated with agent findings, no matching result for endpoint: %s",
                    tool_input["url"])

    return {"logged": True}




