import unittest
from unittest.mock import patch

from agents.final_output import final_output_node


_EXPECTED_KEYS = {
    "session_id", "destination", "origin", "num_people", "days",
    "start_date", "end_date", "transport_options", "accommodation_options",
    "itinerary", "reminder",
}


def _base_state(**overrides):
    state = {
        "session_id": "sess-1",
        "destination": "Tokyo",
        "origin": "San Francisco",
        "num_people": 2,
        "days": 3,
        "start_date": "2026-10-01",
        "end_date": "2026-10-04",
        "transport_options": {
            "flight": {
                "outbound": [{"airline": "CX", "reason": "Duffel API result"}],
                "inbound": [{"airline": "CX", "reason": "Duffel API result"}],
            },
        },
        "accommodation_options": [{"area": "Shinjuku", "reason": "Mock hotel data"}],
        "itinerary": [{"day": 1, "activities": []}],
        "checker_critique": None,
        "log_trace": False,
    }
    state.update(overrides)
    return state


class FinalOutputNodeTests(unittest.TestCase):

    @patch("agents.final_output.generate_reminder", return_value="Enjoy your trip!")
    def test_payload_has_exactly_the_whitelisted_keys(self, mock_reminder):
        result = final_output_node(_base_state())

        self.assertEqual(set(result["final_output"].keys()), _EXPECTED_KEYS)
        self.assertEqual(result["final_output"]["reminder"], "Enjoy your trip!")
        self.assertEqual(result["final_output"]["destination"], "Tokyo")

    @patch("agents.final_output.generate_reminder", return_value="Enjoy your trip!")
    def test_session_id_is_present_in_payload(self, mock_reminder):
        result = final_output_node(_base_state(session_id="sess-xyz"))
        self.assertEqual(result["final_output"]["session_id"], "sess-xyz")

    @patch("agents.final_output.generate_reminder", return_value="")
    def test_reminder_empty_when_service_returns_nothing(self, mock_reminder):
        result = final_output_node(_base_state())
        self.assertEqual(result["final_output"]["reminder"], "")

    @patch("services.llm_reminder_service.get_openai_client")
    def test_reminder_service_is_fail_safe(self, mock_get_client):
        # The node relies on generate_reminder never raising; verify the service
        # itself degrades to "" when the LLM call blows up.
        mock_get_client.return_value.chat.completions.create.side_effect = Exception("LLM down")
        from services.llm_reminder_service import generate_reminder
        self.assertEqual(generate_reminder(destination="Tokyo"), "")

    @patch("agents.final_output.generate_reminder", return_value="Wrap-up!")
    def test_normal_path_passes_critique_and_reasons(self, mock_reminder):
        final_output_node(_base_state(checker_critique="Remove duplicate Senso-ji"))

        kwargs = mock_reminder.call_args.kwargs
        self.assertFalse(kwargs["closing"])
        self.assertEqual(kwargs["checker_critique"], "Remove duplicate Senso-ji")
        reasons = [item["reason"] for item in kwargs["flight_items"]]
        self.assertEqual(reasons, ["Duffel API result", "Duffel API result"])
        self.assertEqual(kwargs["accommodation_options"][0]["reason"], "Mock hotel data")

    @patch("agents.final_output.generate_reminder", return_value="Wrap-up!")
    def test_closing_path_calls_reminder_in_closing_mode(self, mock_reminder):
        result = final_output_node(_base_state(), closing_reminder=True)

        self.assertTrue(mock_reminder.call_args.kwargs["closing"])
        self.assertEqual(result["final_output"]["reminder"], "Wrap-up!")

    @patch("agents.final_output.generate_reminder", return_value="")
    def test_missing_optional_state_fields_do_not_raise(self, mock_reminder):
        state = {"destination": "Tokyo"}  # no transport_options/accommodation/itinerary
        result = final_output_node(state)
        self.assertEqual(result["final_output"]["transport_options"], {})
        self.assertEqual(result["final_output"]["accommodation_options"], [])
        self.assertEqual(result["final_output"]["itinerary"], [])


if __name__ == "__main__":
    unittest.main()
