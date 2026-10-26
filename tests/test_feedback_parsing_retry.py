import json
import unittest
from unittest.mock import MagicMock, patch

from openai.types.chat import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)
from pydantic import ValidationError

from orchestration import llm_feedback_parsing

_VALID = json.dumps({
    "accommodation": {"preference": {"max_price_per_night": 5000}},
})
_MISPLACED = json.dumps({
    "accommodation": {"max_price_per_night": 5000},
})


def _response(arguments, tool_call_id="call_1", model="gpt-4o-mini"):
    """Shape a chat.completions response closely enough for the parser.

    The message is a real ChatCompletionMessage (not a MagicMock) so that
    reading an attribute the SDK doesn't define — e.g. `message.model` —
    fails here the way it fails against the live API.
    """
    tool_call = ChatCompletionMessageToolCall(
        id=tool_call_id,
        type="function",
        function=Function(name="extract_constraints", arguments=arguments),
    )
    message = ChatCompletionMessage(role="assistant", content=None, tool_calls=[tool_call])

    usage = MagicMock(prompt_tokens=100, completion_tokens=20)

    response = MagicMock(model=model, choices=[MagicMock(message=message)], usage=usage)
    return response


def _no_tool_call_response():
    response = _response(_VALID)
    response.choices[0].message = ChatCompletionMessage(role="assistant", content="no call")
    return response


class ParseRetryTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        patches = [
            patch.object(llm_feedback_parsing, "get_openai_client", return_value=self.client),
            patch.object(llm_feedback_parsing, "get_rates", return_value={"USD": 1.0}),
            patch.object(llm_feedback_parsing, "update_current_generation"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.create = self.client.chat.completions.create

    def _messages(self, call_index):
        return self.create.call_args_list[call_index].kwargs["messages"]

    def test_valid_response_uses_one_call(self):
        self.create.return_value = _response(_VALID)

        result = llm_feedback_parsing.parse_feedback_with_llm("hotel up to 5000 a night")

        self.assertEqual(result.accommodation.preference.max_price_per_night, 5000)
        self.assertEqual(self.create.call_count, 1)

    def test_misplaced_field_is_reasked_and_second_attempt_wins(self):
        self.create.side_effect = [_response(_MISPLACED), _response(_VALID)]

        result = llm_feedback_parsing.parse_feedback_with_llm("hotel up to 5000 a night")

        self.assertEqual(result.accommodation.preference.max_price_per_night, 5000)
        self.assertEqual(self.create.call_count, 2)

    def test_reask_carries_the_bad_call_and_the_validation_error(self):
        self.create.side_effect = [_response(_MISPLACED), _response(_VALID)]

        llm_feedback_parsing.parse_feedback_with_llm("hotel up to 5000 a night")

        messages = self._messages(1)
        assistant_turn, tool_turn = messages[-2], messages[-1]
        self.assertEqual(assistant_turn["role"], "assistant")
        self.assertEqual(assistant_turn["tool_calls"][0]["id"], "call_1")
        self.assertEqual(tool_turn["role"], "tool")
        self.assertEqual(tool_turn["tool_call_id"], "call_1")
        self.assertIn("max_price_per_night", tool_turn["content"])
        self.assertIn("preference", tool_turn["content"])

    def test_three_failed_attempts_raise_and_stop(self):
        self.create.return_value = _response(_MISPLACED)

        with self.assertRaises(ValidationError):
            llm_feedback_parsing.parse_feedback_with_llm("hotel up to 5000 a night")

        self.assertEqual(self.create.call_count, 3)

    def test_response_without_tool_call_is_reasked_as_user_message(self):
        self.create.side_effect = [_no_tool_call_response(), _response(_VALID)]

        result = llm_feedback_parsing.parse_feedback_with_llm("hotel up to 5000 a night")

        self.assertIsNotNone(result)
        self.assertEqual(self.create.call_count, 2)
        self.assertEqual(self._messages(1)[-1]["role"], "user")

    def test_service_errors_are_not_retried_by_the_parse_loop(self):
        self.create.side_effect = RuntimeError("upstream exploded")

        with self.assertRaises(RuntimeError):
            llm_feedback_parsing.parse_feedback_with_llm("hotel up to 5000 a night")

        self.assertEqual(self.create.call_count, 1)

    def test_generation_records_the_attempt_count(self):
        self.create.side_effect = [_response(_MISPLACED), _response(_VALID)]

        with patch.object(llm_feedback_parsing, "update_current_generation") as record:
            llm_feedback_parsing.parse_feedback_with_llm("hotel up to 5000 a night")

        kwargs = record.call_args.kwargs
        self.assertEqual(kwargs["model_parameters"]["attempts"], 2)
        # Both calls were billed; the observation shows the summed cost.
        self.assertEqual(kwargs["usage_details"], {"input": 200, "output": 40})


if __name__ == "__main__":
    unittest.main()
