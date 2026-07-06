import json
import unittest
from unittest.mock import MagicMock, patch


def _mock_openai_response(round_trip_index, outbound_index, inbound_index, reason):
    args = json.dumps({
        "round_trip_index": round_trip_index,
        "outbound_index": outbound_index,
        "inbound_index": inbound_index,
        "reason": reason,
    })
    tool_call = MagicMock()
    tool_call.function.arguments = args
    message = MagicMock()
    message.tool_calls = [tool_call]
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


class SelectFlightsTests(unittest.TestCase):
    @patch("services.llm_flight_selector_service.OpenAI")
    def test_selects_round_trip_index(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            round_trip_index=1, outbound_index=None, inbound_index=None,
            reason="Cheapest direct option",
        )

        from services.llm_flight_selector_service import select_flights
        result = select_flights(
            round_trip_candidates=[{"price": 700}, {"price": 500}],
            outbound_preference={}, inbound_preference={},
        )

        self.assertEqual(result["round_trip_index"], 1)
        self.assertIsNone(result["outbound_index"])
        self.assertEqual(result["reason"], "Cheapest direct option")

    @patch("services.llm_flight_selector_service.OpenAI")
    def test_selects_outbound_and_inbound_indices(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            round_trip_index=None, outbound_index=0, inbound_index=2,
            reason="Best fit for both legs",
        )

        from services.llm_flight_selector_service import select_flights
        result = select_flights(
            outbound_candidates=[{"price": 300}],
            inbound_candidates=[{"price": 200}, {"price": 250}, {"price": 210}],
            outbound_preference={}, inbound_preference={},
        )

        self.assertEqual(result["outbound_index"], 0)
        self.assertEqual(result["inbound_index"], 2)
        self.assertIsNone(result["round_trip_index"])


if __name__ == "__main__":
    unittest.main()
