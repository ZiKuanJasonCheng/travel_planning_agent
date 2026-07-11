import unittest
from unittest.mock import patch, MagicMock


class ApplyUserFeedbackNewConstraintsTests(unittest.TestCase):
    def _base_state(self):
        return {
            "constraints": {},
            "dirty_agents": [],
        }

    @patch("orchestration.human_feedback.parse_feedback_with_llm")
    def test_stores_this_rounds_parsed_constraints(self, mock_parse):
        mock_parsed = MagicMock()
        mock_parsed.model_dump.return_value = {
            "transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}
        }
        mock_parse.return_value = mock_parsed

        from orchestration.human_feedback import apply_user_feedback
        state = self._base_state()
        apply_user_feedback(state, "no layovers on the way there")

        self.assertEqual(
            state["new_constraints"],
            {"transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}},
        )

    @patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None)
    def test_no_parsed_constraints_leaves_new_constraints_unset(self, mock_parse):
        from orchestration.human_feedback import apply_user_feedback
        state = self._base_state()
        result = apply_user_feedback(state, "")

        self.assertFalse(result)
        self.assertNotIn("new_constraints", state)

    @patch("orchestration.human_feedback.parse_feedback_with_llm")
    def test_does_not_merge_or_modify_existing_constraints(self, mock_parse):
        mock_parsed = MagicMock()
        mock_parsed.model_dump.return_value = {
            "transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}
        }
        mock_parse.return_value = mock_parsed

        from orchestration.human_feedback import apply_user_feedback
        state = {
            "constraints": {"transport": {"outbound_air_ticket_preference": {"flight_class": "business"}}},
            "dirty_agents": [],
        }
        apply_user_feedback(state, "no layovers on the way there")

        # constraints must remain exactly what it was before — merging is each agent's job now
        self.assertEqual(
            state["constraints"],
            {"transport": {"outbound_air_ticket_preference": {"flight_class": "business"}}},
        )

    @patch("orchestration.human_feedback.parse_feedback_with_llm")
    def test_dirty_agents_still_computed_from_touched_categories(self, mock_parse):
        mock_parsed = MagicMock()
        mock_parsed.model_dump.return_value = {
            "accommodation": {"preference": {"area": "Shibuya"}}
        }
        mock_parse.return_value = mock_parsed

        from orchestration.human_feedback import apply_user_feedback
        state = self._base_state()
        apply_user_feedback(state, "I want to stay in Shibuya")

        # accommodation touched -> accommodation_agent dirty, plus its downstream dependent attraction_agent
        self.assertEqual(state["dirty_agents"], ["accommodation_agent", "attraction_agent"])


if __name__ == "__main__":
    unittest.main()
