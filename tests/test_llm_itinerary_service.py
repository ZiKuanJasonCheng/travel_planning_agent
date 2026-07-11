import unittest
from unittest.mock import MagicMock


class CallLlmErrorTierTests(unittest.TestCase):
    def _service_with_client(self):
        from services.llm_itinerary_service import LLMItineraryService
        service = LLMItineraryService.__new__(LLMItineraryService)
        service.client = MagicMock()
        return service

    def test_openai_api_error_returns_llm_api_error_reason(self):
        from openai import APIError
        service = self._service_with_client()
        service.client.chat.completions.create.side_effect = APIError(
            "boom", MagicMock(), body=None
        )

        result = service._call_llm("prompt", context="generate_itinerary")

        self.assertEqual(result, [{"day": None, "activities": [], "reason": "LLM API error"}])

    def test_unexpected_error_returns_unknown_error_reason(self):
        service = self._service_with_client()
        service.client.chat.completions.create.side_effect = ValueError("boom")

        result = service._call_llm("prompt", context="generate_itinerary")

        self.assertEqual(result, [{"day": None, "activities": [], "reason": "Unknown error"}])

    def test_no_client_still_returns_empty_list(self):
        """No API key configured is intentional dev-mode behavior, not an error."""
        from services.llm_itinerary_service import LLMItineraryService
        service = LLMItineraryService.__new__(LLMItineraryService)
        service.client = None

        result = service.generate_itinerary(destination="Tokyo", days=3)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
