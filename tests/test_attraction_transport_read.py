import unittest
from unittest.mock import MagicMock, patch


class AttractionTransportReadTests(unittest.TestCase):
    @patch("agents.attraction.get_llm_itinerary_service")
    def test_arrival_time_from_last_outbound_leg(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.generate_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        state = {
            "destination": "Tokyo", "origin": "Hong Kong", "days": 3, "num_people": 2,
            "start_date": "2026-09-10", "constraints": {}, "new_constraints": {},
            "transport_options": {
                "railway": [],
                "flight": {
                    "outbound": [
                        {"airline": "CX", "arrival_time": "12:00:00"},
                        {"airline": "CX", "arrival_time": "14:00:00"},
                    ],
                    "inbound": [{"airline": "CX", "depart_time": "19:00:00"}],
                },
            },
            "accommodation_options": [{"area": "Shinjuku"}],
            "itinerary": None, "checker_critique": None,
            "log_trace": False, "traces": [], "dirty_agents": [],
        }
        attraction_agent(state)

        call_kwargs = mock_svc.generate_itinerary.call_args.kwargs
        self.assertEqual(call_kwargs["arrival_time"], "14:00:00")
        self.assertEqual(call_kwargs["return_depart_time"], "19:00:00")


class AttractionAgentSkipLogicTests(unittest.TestCase):
    def _base_state(self, **overrides):
        state = {
            "destination": "Tokyo", "origin": "Hong Kong", "days": 3, "num_people": 2,
            "start_date": "2026-09-10",
            "feedback": "some prior feedback",
            "constraints": {"attraction": {"preference": {"styles": ["natural scenery"]}}},
            "new_constraints": {},
            "transport_options": {"railway": [], "flight": {"outbound": [], "inbound": []}},
            "accommodation_options": [{"area": "Shinjuku"}],
            "itinerary": [{"day": 1, "activities": [{"name": "Senso-ji", "reason": "LLM generated activity"}]}],
            "checker_critique": None,
            "log_trace": False, "traces": [], "dirty_agents": [],
        }
        state.update(overrides)
        return state

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_skips_when_constraints_unchanged(self, mock_get_svc):
        from agents.attraction import attraction_agent
        state = self._base_state()
        new_state = attraction_agent(state)

        mock_get_svc.assert_not_called()
        self.assertEqual(new_state["itinerary"], state["itinerary"])
        self.assertNotIn("checker_agent", new_state.get("dirty_agents", []))
        self.assertEqual(
            new_state["constraints"]["attraction"],
            {"preference": {"styles": ["natural scenery"]}},
        )

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_replans_and_queues_checker_when_constraints_change(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.update_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        state = self._base_state(
            new_constraints={"attraction": {"preference": {"must_go_places": ["Fushimi Inari"]}}}
        )
        new_state = attraction_agent(state)

        mock_svc.update_itinerary.assert_called_once()
        self.assertIn("checker_agent", new_state["dirty_agents"])
        self.assertEqual(
            new_state["constraints"]["attraction"]["preference"]["must_go_places"],
            ["Fushimi Inari"],
        )

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_does_not_skip_when_checker_critique_pending(self, mock_get_svc):
        """Even with unchanged constraints, a pending checker critique means
        there's a retry to do — must not skip."""
        mock_svc = MagicMock()
        mock_svc.update_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        state = self._base_state(checker_critique="Remove the duplicate Senso-ji visit.")
        new_state = attraction_agent(state)

        mock_svc.update_itinerary.assert_called_once()
        self.assertIn("checker_agent", new_state["dirty_agents"])

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_replans_when_rerun_planning_true_even_if_unchanged(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.update_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        state = self._base_state(
            constraints={
                "attraction": {
                    "preference": {"styles": ["natural scenery"]},
                    "rerun_planning": True,
                }
            },
        )
        new_state = attraction_agent(state)

        mock_svc.update_itinerary.assert_called_once()
        self.assertIsNone(new_state["constraints"]["attraction"]["rerun_planning"])

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_replans_when_last_run_had_errors_even_if_unchanged(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.update_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        state = self._base_state(
            itinerary=[{
                "day": None, "activities": [],
                "reason": (
                    "There's an LLM API error (or unknown error) at the moment. "
                    "Please wait for a few minutes and submit a feedback saying "
                    "'Run attraction service again'."
                ),
            }],
        )
        new_state = attraction_agent(state)

        mock_svc.update_itinerary.assert_not_called()  # no existing_itinerary content -> generate, not update
        mock_svc.generate_itinerary.assert_called_once()

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_new_trip_always_plans_even_with_no_preferences(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.generate_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        state = self._base_state(
            feedback=None, constraints={}, new_constraints={}, itinerary=None,
        )
        new_state = attraction_agent(state)

        mock_svc.generate_itinerary.assert_called_once()

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_llm_error_on_update_keeps_existing_itinerary(self, mock_get_svc):
        """On a real error, prefer showing the existing (still-valid) itinerary
        over an error placeholder, matching air_ticket_agent's fallback philosophy."""
        mock_svc = MagicMock()
        mock_svc.update_itinerary.return_value = [
            {"day": None, "activities": [], "reason": "LLM API error"}
        ]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        existing = [{"day": 1, "activities": [{"name": "Senso-ji", "reason": "LLM generated activity"}]}]
        state = self._base_state(
            new_constraints={"attraction": {"preference": {"must_go_places": ["Fushimi Inari"]}}},
            itinerary=existing,
        )
        new_state = attraction_agent(state)

        self.assertEqual(new_state["itinerary"], existing)


if __name__ == "__main__":
    unittest.main()
