import unittest


class TrainTicketAgentTests(unittest.TestCase):
    def _base_state(self, **overrides):
        state = {
            "destination": "Kyoto", "constraints": {}, "log_trace": False,
            "traces": [],
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
        state = self._base_state(constraints={
            "transport": {"railway_ticket_preference": {"max_price_per_ticket": 45}}
        })
        new_state = train_ticket_agent(state)
        self.assertEqual(new_state["transport_options"]["railway"][0]["price"], 45)


if __name__ == "__main__":
    unittest.main()
