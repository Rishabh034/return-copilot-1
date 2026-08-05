from app.agenticintegrations.models.carrier_api_testing.api_test_log import APITestLog


class ResultCollector:
    def __init__(self):
        self.results = []

    def add(self, **kwargs):
        result = APITestLog(**kwargs)
        self.results.append(result)

    def get_results(self):
        return self.results




