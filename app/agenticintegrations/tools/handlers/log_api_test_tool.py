from app.agenticintegrations.models.carrier_api_testing.result_collector import ResultCollector


def run(tool_input: dict, additional_tool_params):
    collector: ResultCollector = additional_tool_params["collector"]
    collector.add(**tool_input)
    return {"logged": True}




