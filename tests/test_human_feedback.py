import unittest
from unittest.mock import patch, MagicMock


class ApplyUserFeedbackLastConstraintsTests(unittest.TestCase):
    def _base_state(self):
        return {
            "constraints": {},
            "dirty_agents": [],
        }

    @patch("orchestration.human_feedback.parse_feedback_with_llm")
    def test_stores_this_rounds_parsed_constraints(self, mock_parse):
        mock_new_constraints = MagicMock()
        mock_new_constraints.model_dump.return_value = {
            "transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}
        }
        mock_parse.return_value = mock_new_constraints

        from orchestration.human_feedback import apply_user_feedback
        state = self._base_state()
        apply_user_feedback(state, "no layovers on the way there")

        self.assertEqual(
            state["last_feedback_constraints"],
            {"transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}},
        )

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_no_parsed_constraints_leaves_last_feedback_constraints_unset(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback
        state = self._base_state()
        result = apply_user_feedback(state, "")

        self.assertFalse(result)
        self.assertNotIn("last_feedback_constraints", state)


if __name__ == "__main__":
    unittest.main()
