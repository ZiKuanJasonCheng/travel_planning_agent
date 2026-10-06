import json
import unittest
from unittest.mock import MagicMock, patch

from services import llm_itinerary_service
from services.llm_itinerary_service import LLMItineraryService, _available_tools


_ITINERARY = {"itinerary": [{"day": 1, "activities": [{"name": "Tower of London", "type": "sightseeing"}]}]}


def _answer_response(payload=None):
    """A response whose message is plain JSON content and carries no tool calls."""
    message = MagicMock()
    message.content = json.dumps(payload or _ITINERARY)
    message.tool_calls = None
    message.role = "assistant"

    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    response.model = "gpt-4o-mini"
    response.usage.prompt_tokens = 100
    response.usage.completion_tokens = 50
    return response


def _tool_call_response(name="search_local_events", arguments=None):
    """A response whose message asks for one tool call, with content=None.

    This is the real shape of a tool-calling turn: json.loads(content) would
    raise on it, so the loop must never try to parse it.
    """
    call = MagicMock()
    call.id = "call_abc123"
    call.function.name = name
    call.function.arguments = json.dumps(arguments or {
        "city": "London", "start_date": "2027-06-28", "end_date": "2027-07-05",
    })

    message = MagicMock()
    message.content = None
    message.tool_calls = [call]
    message.role = "assistant"

    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    response.model = "gpt-4o-mini"
    response.usage.prompt_tokens = 100
    response.usage.completion_tokens = 20
    return response


class ToolCallingLoopTests(unittest.TestCase):

    def setUp(self):
        patch.dict("os.environ", {"TICKETMASTER_API_KEY": "test-key"}).start()
        self.addCleanup(patch.stopall)

    def _service(self):
        service = LLMItineraryService.__new__(LLMItineraryService)
        service.client = MagicMock()
        return service

    def test_tool_round_then_answer_parses_the_answer(self):
        service = self._service()
        service.client.chat.completions.create.side_effect = [
            _tool_call_response(),
            _answer_response(),
        ]

        result = service._call_llm("prompt", context="generate_itinerary", tools=[{"fake": "tool"}])

        self.assertEqual(service.client.chat.completions.create.call_count, 2)
        self.assertEqual(result[0]["activities"][0]["name"], "Tower of London")

    def test_tool_result_is_appended_as_a_tool_message(self):
        service = self._service()
        service.client.chat.completions.create.side_effect = [
            _tool_call_response(),
            _answer_response(),
        ]

        with patch.object(llm_itinerary_service, "execute_tool_call", return_value='{"events": []}') as mock_exec:
            service._call_llm("prompt", context="generate_itinerary", tools=[{"fake": "tool"}])

        mock_exec.assert_called_once()
        name, arguments = mock_exec.call_args[0]
        self.assertEqual(name, "search_local_events")
        self.assertIn("London", arguments)

        # Second call's messages must carry the assistant tool_calls turn and a
        # matching role:"tool" message keyed by the call id.
        second_messages = service.client.chat.completions.create.call_args[1]["messages"]
        assistant_turns = [m for m in second_messages if m.get("role") == "assistant"]
        tool_turns = [m for m in second_messages if m.get("role") == "tool"]
        self.assertEqual(len(assistant_turns), 1)
        self.assertEqual(len(tool_turns), 1)
        self.assertEqual(tool_turns[0]["tool_call_id"], "call_abc123")
        self.assertEqual(tool_turns[0]["content"], '{"events": []}')

    def test_no_tool_call_means_a_single_request(self):
        service = self._service()
        service.client.chat.completions.create.side_effect = [_answer_response()]

        service._call_llm("prompt", context="generate_itinerary", tools=None)

        self.assertEqual(service.client.chat.completions.create.call_count, 1)

    def test_cap_withholds_tools_on_the_final_iteration(self):
        """A model that keeps calling tools must still be forced to answer."""
        service = self._service()
        service.client.chat.completions.create.side_effect = [
            _tool_call_response(),
            _tool_call_response(),
            _answer_response(),
        ]

        with patch.object(llm_itinerary_service, "execute_tool_call", return_value='{"events": []}'):
            result = service._call_llm("prompt", context="generate_itinerary", tools=[{"fake": "tool"}])

        self.assertEqual(service.client.chat.completions.create.call_count, 3)
        first_kwargs = service.client.chat.completions.create.call_args_list[0][1]
        last_kwargs = service.client.chat.completions.create.call_args_list[-1][1]
        self.assertIn("tools", first_kwargs)
        self.assertNotIn("tools", last_kwargs)
        self.assertEqual(result[0]["activities"][0]["name"], "Tower of London")


class AvailableToolsTests(unittest.TestCase):

    def test_no_key_means_no_tools(self):
        with patch.dict("os.environ", {"TICKETMASTER_API_KEY": ""}):
            self.assertIsNone(_available_tools())

    def test_key_present_exposes_the_events_tool(self):
        with patch.dict("os.environ", {"TICKETMASTER_API_KEY": "test-key"}):
            tools = _available_tools()

        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["function"]["name"], "search_local_events")

    def test_generate_itinerary_passes_no_tools_without_a_key(self):
        service = LLMItineraryService.__new__(LLMItineraryService)
        service.client = MagicMock()
        service.client.chat.completions.create.return_value = _answer_response()

        with patch.dict("os.environ", {"TICKETMASTER_API_KEY": ""}):
            service.generate_itinerary(destination="Kyoto", days=2, start_date="2027-06-28")

        kwargs = service.client.chat.completions.create.call_args[1]
        self.assertNotIn("tools", kwargs)


class DateThreadingTests(unittest.TestCase):

    def test_generate_itinerary_puts_the_window_in_the_prompt(self):
        service = LLMItineraryService.__new__(LLMItineraryService)
        service.client = MagicMock()
        service.client.chat.completions.create.return_value = _answer_response()

        service.generate_itinerary(
            destination="London", days=7, start_date="2027-06-28", end_date="2027-07-05"
        )

        prompt = service.client.chat.completions.create.call_args[1]["messages"][0]["content"]
        self.assertIn("2027-06-28 to 2027-07-05", prompt)

    def test_update_itinerary_puts_the_window_in_the_prompt(self):
        """The revision path must carry dates too, or events can't be queried."""
        service = LLMItineraryService.__new__(LLMItineraryService)
        service.client = MagicMock()
        service.client.chat.completions.create.return_value = _answer_response()

        service.update_itinerary(
            existing_itinerary=[{"day": 1, "activities": []}],
            destination="London",
            days=7,
            start_date="2027-06-28",
            end_date="2027-07-05",
        )

        prompt = service.client.chat.completions.create.call_args[1]["messages"][0]["content"]
        self.assertIn("2027-06-28 to 2027-07-05", prompt)


if __name__ == "__main__":
    unittest.main()
