from app.agenticintegrations.models.carrier_api_testing.api_test_log import APITestLog


class ResultCollector:
    def __init__(self):
        self.results = []

    def add(self, data):
        result = APITestLog(**data)
        self.results.append(result)

    def update(
            self,
            updates: dict
    ):
        updated = False
        url = updates["url"]
        for api_test_log in self.results:
            if api_test_log.url == url:
                for field, value in updates.items():
                    setattr(api_test_log, field, value)
                updated = True
                break

        return updated

    def get_results(self):
        return self.results




