import unittest
from unittest.mock import patch


class TrainTicketAgentTests(unittest.TestCase):
    def _base_state(self, **overrides):
        state = {
            "destination": "Kyoto", "constraints": {}, "new_constraints": {},
            "feedback": None, "log_trace": False, "traces": [],
        }
        state.update(overrides)
        return state

    def test_writes_into_railway_sub_dict(self):
        from agents.train_ticket import train_ticket_agent
        new_state = train_ticket_agent(self._base_state())
        self.assertEqual(len(new_state["transport_options"]["railway"]), 1)
        self.assertEqual(new_state["transport_options"]["railway"][0]["type"], "train")

    def test_preserves_existing_flight_options(self):
        from agents.train_ticket import train_ticket_agent
        existing_flight = {"outbound": [{"airline": "CX"}], "inbound": []}
        state = self._base_state(transport_options={"railway": [], "flight": existing_flight})
        new_state = train_ticket_agent(state)
        self.assertEqual(new_state["transport_options"]["flight"], existing_flight)

    def test_uses_railway_ticket_preference_max_price(self):
        from agents.train_ticket import train_ticket_agent
        state = self._base_state(
            new_constraints={"transport": {"railway_ticket_preference": {"max_price_per_ticket": 45}}}
        )
        new_state = train_ticket_agent(state)
        self.assertEqual(new_state["transport_options"]["railway"][0]["price"], 45)

    def test_skips_when_railway_preference_unchanged(self):
        from agents.train_ticket import train_ticket_agent
        existing_railway = [{"type": "train", "price": 45, "reason": "Placeholder train option (API not yet integrated)"}]
        state = self._base_state(
            feedback="some prior feedback",
            constraints={"transport": {"railway_ticket_preference": {"max_price_per_ticket": 45}}},
            new_constraints={"transport": {"railway_ticket_preference": {"max_price_per_ticket": 45}}},
            transport_options={"railway": existing_railway, "flight": {"outbound": [], "inbound": []}},
        )
        new_state = train_ticket_agent(state)
        self.assertEqual(new_state["transport_options"]["railway"], existing_railway)
        self.assertEqual(
            new_state["constraints"]["transport"]["railway_ticket_preference"],
            {"max_price_per_ticket": 45},
        )

    def test_replans_when_railway_preference_changes(self):
        from agents.train_ticket import train_ticket_agent
        state = self._base_state(
            feedback="actually cap it at 30",
            constraints={"transport": {"railway_ticket_preference": {"max_price_per_ticket": 45}}},
            new_constraints={"transport": {"railway_ticket_preference": {"max_price_per_ticket": 30}}},
            transport_options={
                "railway": [{"type": "train", "price": 45}],
                "flight": {"outbound": [], "inbound": []},
            },
        )
        new_state = train_ticket_agent(state)
        self.assertEqual(new_state["transport_options"]["railway"][0]["price"], 30)

    def test_replans_when_rerun_planning_true_even_if_unchanged(self):
        from agents.train_ticket import train_ticket_agent
        state = self._base_state(
            feedback="run it again please",
            constraints={"transport": {"railway_ticket_preference": {"max_price_per_ticket": 45}}},
            new_constraints={"transport": {"rerun_planning": True}},
            transport_options={
                "railway": [{"type": "train", "price": 45}],
                "flight": {"outbound": [], "inbound": []},
            },
        )
        new_state = train_ticket_agent(state)
        self.assertEqual(len(new_state["transport_options"]["railway"]), 1)
        self.assertIsNone(new_state["constraints"]["transport"]["rerun_planning"])

    def test_new_trip_always_plans_even_with_no_preferences(self):
        from agents.train_ticket import train_ticket_agent
        state = self._base_state(feedback=None, constraints={}, new_constraints={})
        new_state = train_ticket_agent(state)
        self.assertEqual(len(new_state["transport_options"]["railway"]), 1)


if __name__ == "__main__":
    unittest.main()
