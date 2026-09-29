import json
import unittest
from unittest.mock import MagicMock, patch

from services import airline_iata_resolver


def _mock_response(codes):
    tool_call = MagicMock()
    tool_call.function.arguments = json.dumps({"iata_codes": codes})
    message = MagicMock()
    message.tool_calls = [tool_call]
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


class ResolveAirlineIataCodesCacheTests(unittest.TestCase):

    def setUp(self):
        # The cache is a process-lifetime module global; clear it so tests don't leak.
        airline_iata_resolver._cache.clear()
        self.addCleanup(airline_iata_resolver._cache.clear)

    @patch("services.airline_iata_resolver.client")
    def test_second_identical_call_makes_no_llm_call(self, mock_client):
        mock_client.chat.completions.create.return_value = _mock_response(["CX"])

        first = airline_iata_resolver.resolve_airline_iata_codes(["Cathay Pacific"])
        second = airline_iata_resolver.resolve_airline_iata_codes(["Cathay Pacific"])

        self.assertEqual(first, ["CX"])
        self.assertEqual(second, ["CX"])
        self.assertEqual(mock_client.chat.completions.create.call_count, 1)

    @patch("services.airline_iata_resolver.client")
    def test_overlapping_lists_only_send_uncached_names(self, mock_client):
        create = mock_client.chat.completions.create
        create.return_value = _mock_response(["CX"])

        airline_iata_resolver.resolve_airline_iata_codes(["Cathay Pacific"])
        create.reset_mock()
        create.return_value = _mock_response(["NH"])

        result = airline_iata_resolver.resolve_airline_iata_codes(["Cathay Pacific", "ANA"])

        self.assertEqual(result, ["CX", "NH"])
        user_message = create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("ANA", user_message)
        self.assertNotIn("Cathay Pacific", user_message)

    @patch("services.airline_iata_resolver.client")
    def test_unresolvable_name_is_not_retried(self, mock_client):
        # Model omits the name it cannot resolve.
        mock_client.chat.completions.create.return_value = _mock_response([])

        first = airline_iata_resolver.resolve_airline_iata_codes(["Mystery Air"])
        second = airline_iata_resolver.resolve_airline_iata_codes(["Mystery Air"])

        # Falls back to the original name, and the miss is cached so it isn't retried.
        self.assertEqual(first, ["Mystery Air"])
        self.assertEqual(second, ["Mystery Air"])
        self.assertEqual(mock_client.chat.completions.create.call_count, 1)

    @patch("services.airline_iata_resolver.client")
    def test_existing_codes_short_circuit_without_llm_call(self, mock_client):
        result = airline_iata_resolver.resolve_airline_iata_codes(["CX", "JL"])

        self.assertEqual(result, ["CX", "JL"])
        mock_client.chat.completions.create.assert_not_called()

    @patch("services.airline_iata_resolver.client")
    def test_empty_input_returns_empty(self, mock_client):
        self.assertEqual(airline_iata_resolver.resolve_airline_iata_codes([]), [])
        mock_client.chat.completions.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
