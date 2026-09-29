import json
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch


def _near_future_date() -> str:
    return (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")


def _mock_openai_response(round_trip_index, outbound_index, inbound_index, reason):
    args = json.dumps({
        "round_trip_index": round_trip_index,
        "outbound_index": outbound_index,
        "inbound_index": inbound_index,
        "reason": reason,
    })
    tool_call = MagicMock()
    tool_call.id = "call_select"
    tool_call.function.name = "select_flights"
    tool_call.function.arguments = args
    message = MagicMock()
    message.tool_calls = [tool_call]
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    response.usage.prompt_tokens = 120
    response.usage.completion_tokens = 18
    return response


def _tool_call(name, arguments, call_id="call-1"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = arguments
    tc.model_dump.return_value = {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }
    return tc


def _response_with_tool_calls(*tool_calls):
    message = MagicMock()
    message.tool_calls = list(tool_calls)
    message.content = None
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    response.model = "gpt-4o"
    response.usage.prompt_tokens = 100
    response.usage.completion_tokens = 10
    return response


_CONVERT_CALL = _tool_call(
    "convert_currency",
    json.dumps({"amount": 20000, "from_currency": "JPY", "to_currency": "USD"}),
)


class ConvertCurrencyToolLoopTests(unittest.TestCase):
    """The selector may call convert_currency before committing to a selection."""

    def setUp(self):
        patcher = patch("services.currency.get_rates", return_value={"USD": 1.0, "JPY": 200.0})
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("services.llm_flight_selector_service.OpenAI")
    def test_conversion_then_selection_resolves_to_selection(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = [
            _response_with_tool_calls(_CONVERT_CALL),
            _mock_openai_response(0, None, None, "Converted price is within budget"),
        ]

        from services.llm_flight_selector_service import select_flights
        result = select_flights(
            round_trip_candidates=[{"price": 20000, "currency": "JPY"}],
            outbound_preference={}, inbound_preference={},
        )

        self.assertEqual(result["round_trip_index"], 0)
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)

    @patch("services.llm_flight_selector_service.OpenAI")
    def test_conversion_result_is_fed_back_to_the_model(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = [
            _response_with_tool_calls(_CONVERT_CALL),
            _mock_openai_response(0, None, None, "ok"),
        ]

        from services.llm_flight_selector_service import select_flights
        select_flights(
            round_trip_candidates=[{"price": 20000, "currency": "JPY"}],
            outbound_preference={}, inbound_preference={},
        )

        second_messages = mock_client.chat.completions.create.call_args_list[1].kwargs["messages"]
        tool_messages = [m for m in second_messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        payload = json.loads(tool_messages[0]["content"])
        # 20,000 JPY at 200 JPY/USD is 100 USD.
        self.assertEqual(payload["result"], 100.0)

    @patch("services.llm_flight_selector_service.OpenAI")
    def test_iteration_cap_forces_the_terminal_tool(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        # The model keeps converting and never selects on its own; the cap must
        # force the terminal tool on the final allowed iteration.
        mock_client.chat.completions.create.side_effect = [
            _response_with_tool_calls(_CONVERT_CALL),
            _response_with_tool_calls(_CONVERT_CALL),
            _mock_openai_response(1, None, None, "forced"),
        ]

        from services.llm_flight_selector_service import select_flights
        result = select_flights(
            round_trip_candidates=[{"price": 500}, {"price": 700}],
            outbound_preference={}, inbound_preference={},
        )

        self.assertEqual(result["round_trip_index"], 1)
        last_kwargs = mock_client.chat.completions.create.call_args_list[-1].kwargs
        self.assertEqual(
            last_kwargs["tool_choice"],
            {"type": "function", "function": {"name": "select_flights"}},
        )

    @patch("services.llm_flight_selector_service.OpenAI")
    def test_raises_when_no_terminal_call_ever_arrives(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _response_with_tool_calls(_CONVERT_CALL)

        from services.llm_flight_selector_service import select_flights
        with self.assertRaises(ValueError):
            select_flights(
                round_trip_candidates=[{"price": 500}],
                outbound_preference={}, inbound_preference={},
            )


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

    @patch("services.llm_flight_selector_service.get_weather_service")
    @patch("services.llm_flight_selector_service.get_airport_coords")
    @patch("services.llm_flight_selector_service.OpenAI")
    def test_includes_weather_in_prompt_when_available(
        self, mock_openai_cls, mock_get_coords, mock_get_weather_service
    ):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            round_trip_index=0, outbound_index=None, inbound_index=None,
            reason="Good weather and price",
        )
        mock_get_coords.return_value = (22.3, 114.2)
        mock_weather_service = MagicMock()
        mock_weather_service.get_forecast.return_value = {
            "hours": [
                {"time": "17:00", "condition": "Sunny", "precipitation_probability": 5, "temp": 27},
                {"time": "18:00", "condition": "Sunny", "precipitation_probability": 5, "temp": 26},
                {"time": "19:00", "condition": "Clear", "precipitation_probability": 3, "temp": 24},
            ]
        }
        mock_get_weather_service.return_value = mock_weather_service

        from services.llm_flight_selector_service import select_flights
        select_flights(
            round_trip_candidates=[{
                "price": 500, "currency": "USD",
                "outbound_legs": [{"airline": "CX", "from": "HKG", "to": "KIX",
                                    "depart_time": "18:25:00", "arrival_time": "22:00:00",
                                    "departure_date": _near_future_date()}],
            }],
            outbound_preference={}, inbound_preference={},
        )

        user_content = mock_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("weather: 17:00 Sunny/precip 5%/27°C", user_content)

    @patch("services.llm_flight_selector_service.get_weather_service")
    @patch("services.llm_flight_selector_service.get_airport_coords")
    @patch("services.llm_flight_selector_service.OpenAI")
    def test_selection_works_when_weather_unavailable(
        self, mock_openai_cls, mock_get_coords, mock_get_weather_service
    ):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            round_trip_index=0, outbound_index=None, inbound_index=None,
            reason="Best available option",
        )
        mock_get_coords.return_value = None
        mock_get_weather_service.return_value = MagicMock()

        from services.llm_flight_selector_service import select_flights
        result = select_flights(
            round_trip_candidates=[{
                "price": 500, "currency": "USD",
                "outbound_legs": [{"airline": "CX", "from": "HKG", "to": "KIX",
                                    "depart_time": "18:25:00", "arrival_time": "22:00:00",
                                    "departure_date": "2026-09-12"}],
            }],
            outbound_preference={}, inbound_preference={},
        )

        self.assertEqual(result["round_trip_index"], 0)
        user_content = mock_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertNotIn("weather:", user_content)


if __name__ == "__main__":
    unittest.main()
