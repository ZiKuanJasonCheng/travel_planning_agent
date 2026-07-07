# tests/test_transport_agent.py
import unittest
from unittest.mock import patch


class DetermineSubAgentsTests(unittest.TestCase):
    def test_reads_transport_type_from_top_level(self):
        from agents.transport import _determine_transport_sub_agents
        agents = _determine_transport_sub_agents({}, {"transport_type": "train"})
        self.assertEqual([name for name, _ in agents], ["train_ticket_agent"])

    def test_defaults_to_air_when_unset(self):
        from agents.transport import _determine_transport_sub_agents
        agents = _determine_transport_sub_agents({}, {})
        self.assertEqual([name for name, _ in agents], ["air_ticket_agent"])

    def test_both_calls_both_agents(self):
        from agents.transport import _determine_transport_sub_agents
        agents = _determine_transport_sub_agents({}, {"transport_type": "both"})
        self.assertEqual([name for name, _ in agents], ["air_ticket_agent", "train_ticket_agent"])


class FillTransportOptionsWithSubagentErrorsTests(unittest.TestCase):
    def test_returns_reason_only_error_option(self):
        from agents.transport import _fill_transport_options_with_subagent_errors
        result = _fill_transport_options_with_subagent_errors()
        self.assertEqual(
            result["flight"]["outbound"],
            [{"reason": (
                "All transport subagents (flight and railway) got failed at the moment. "
                "Please wait for a few minutes and submit a feedback saying "
                "'Run transport/flight service again'."
            )}],
        )
        self.assertEqual(result["railway"], [])
        self.assertEqual(result["flight"]["inbound"], [])


class TransportAgentTests(unittest.TestCase):
    @patch("agents.transport.air_ticket_agent")
    def test_uses_default_when_sub_agents_produce_nothing(self, mock_air_ticket):
        mock_air_ticket.side_effect = lambda state: state  # doesn't touch transport_options

        from agents.transport import transport_agent
        state = {
            "destination": "Tokyo", "constraints": {}, "log_trace": False,
        }
        result = transport_agent(state)
        self.assertTrue(result["transport_options"]["flight"]["outbound"])


if __name__ == "__main__":
    unittest.main()
