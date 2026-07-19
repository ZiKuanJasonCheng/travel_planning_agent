import unittest

from states.transport_constraints import (
    FlightPreferenceConstraint,
    RailwayTicketPreferenceConstraint,
    TransportConstraint,
)


class TransportConstraintTests(unittest.TestCase):
    def test_flight_preference_constraint_has_max_price_per_ticket(self):
        pref = FlightPreferenceConstraint(max_price_per_ticket=500, airlines=["CX"])
        self.assertEqual(pref.max_price_per_ticket, 500)
        self.assertEqual(pref.airlines, ["CX"])

    def test_railway_ticket_preference_constraint_has_max_price_per_ticket(self):
        pref = RailwayTicketPreferenceConstraint(max_price_per_ticket=80)
        self.assertEqual(pref.max_price_per_ticket, 80)

    def test_transport_constraint_fields(self):
        tc = TransportConstraint(
            outbound_air_ticket_preference=FlightPreferenceConstraint(airlines=["CX"]),
            inbound_air_ticket_preference=FlightPreferenceConstraint(airlines=["UO"]),
            railway_ticket_preference=RailwayTicketPreferenceConstraint(max_price_per_ticket=80),
            transport_type="both",
            rerun_planning=True,
        )
        self.assertEqual(tc.outbound_air_ticket_preference.airlines, ["CX"])
        self.assertEqual(tc.inbound_air_ticket_preference.airlines, ["UO"])
        self.assertEqual(tc.railway_ticket_preference.max_price_per_ticket, 80)
        self.assertEqual(tc.transport_type, "both")
        self.assertTrue(tc.rerun_planning)

    def test_transport_constraint_defaults_to_none(self):
        tc = TransportConstraint()
        self.assertIsNone(tc.outbound_air_ticket_preference)
        self.assertIsNone(tc.inbound_air_ticket_preference)
        self.assertIsNone(tc.railway_ticket_preference)
        self.assertIsNone(tc.transport_type)
        self.assertIsNone(tc.rerun_planning)

    def test_rerun_planning_has_llm_facing_description(self):
        schema = TransportConstraint.model_json_schema()
        description = schema["properties"]["rerun_planning"]["description"]
        self.assertIn("rerun", description.lower())

    def test_direct_flights_only_defaults_to_none(self):
        """Must default to None (not False) so an unmentioned direct_flights_only
        this round doesn't overwrite an existing True from a prior round — see
        merge_constraints()'s None-preserving merge rule."""
        pref = FlightPreferenceConstraint()
        self.assertIsNone(pref.direct_flights_only)


if __name__ == "__main__":
    unittest.main()
