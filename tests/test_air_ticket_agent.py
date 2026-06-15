import unittest
from unittest.mock import MagicMock, patch


class AirTicketAgentOneWayFallbackTests(unittest.TestCase):
    def setUp(self):
        # Patch resolver so agent tests are not sensitive to airportsdata internals.
        # (Tokyo resolves to ["NRT", "HND"] dynamically; keep tests deterministic.)
        patcher = patch(
            "agents.air_ticket.resolve_city_iata_codes",
            side_effect=lambda name: {
                "Hong Kong": ["HKG"],
                "Tokyo": ["NRT"],
                "Shanghai": ["PVG", "SHA"],
            }.get(name, [name.upper()[:3]])
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _base_state(self):
        return {
            "destination": "Tokyo",
            "origin": "Hong Kong",
            "num_people": 1,
            "days": 5,
            "start_date": "2025-06-01",
            "end_date": "2025-06-06",
            "constraints": {},
            "transport_options": [],
            "log_trace": False,
            "traces": [],
            "dirty_agents": [],
            "status": "planning",
        }

    def _make_flight(self, direction="outbound", airline="CX"):
        return {
            "type": "flight",
            "airline": airline,
            "price": 400,
            "currency": "USD",
            "depart_time": "10:00:00",
            "arrival_time": "14:00:00",
            "return_depart_time": "",
            "stops": 0,
            "direction": direction,
            "reason": "Amadeus API result",
        }

    # ------------------------------------------------------------------
    # Round-trip succeeds
    # ------------------------------------------------------------------

    @patch("agents.air_ticket.get_flight_service")
    def test_uses_round_trip_when_available(self, mock_get_svc):
        round_trip_flight = {**self._make_flight(), "return_depart_time": "19:00:00"}
        mock_svc = MagicMock()
        mock_svc.search_flights.return_value = [round_trip_flight]
        mock_get_svc.return_value = mock_svc

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(self._base_state())
        print(f"test_uses_round_trip_when_available(): new_state: {new_state}")

        # Only one search call (round-trip)
        mock_svc.search_flights.assert_called_once()
        call_kwargs = mock_svc.search_flights.call_args.kwargs
        print(f"test_uses_round_trip_when_available(): call_kwargs: {call_kwargs}")
        self.assertIsNotNone(call_kwargs.get("return_date"))

        flights = [o for o in new_state["transport_options"] if o.get("type") == "flight"]
        self.assertGreater(len(flights), 0)

    # ------------------------------------------------------------------
    # Round-trip returns nothing → separate one-way fallback
    # ------------------------------------------------------------------

    @patch("agents.air_ticket.get_flight_service")
    def test_falls_back_to_one_way_pair_when_round_trip_empty(self, mock_get_svc):
        outbound = self._make_flight("outbound", "CX")
        inbound = self._make_flight("inbound", "CX")

        mock_svc = MagicMock()
        # First call (round-trip) → empty; second call (outbound) → result; third call (inbound) → result
        mock_svc.search_flights.side_effect = [[], [outbound], [inbound]]
        mock_get_svc.return_value = mock_svc

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(self._base_state())

        # Three search calls total
        self.assertEqual(mock_svc.search_flights.call_count, 3)

        flights = [option for option in new_state["transport_options"] if option.get("type") == "flight"]
        self.assertGreater(len(flights), 0)


    @patch("agents.air_ticket.get_flight_service")
    def test_one_way_fallback_searches_correct_routes(self, mock_get_svc):
        outbound = self._make_flight("outbound")
        inbound = self._make_flight("inbound")

        mock_svc = MagicMock()
        mock_svc.search_flights.side_effect = [[], [outbound], [inbound]]
        mock_get_svc.return_value = mock_svc

        from agents.air_ticket import air_ticket_agent
        air_ticket_agent(self._base_state())

        calls = mock_svc.search_flights.call_args_list
        print(f"test_one_way_fallback_searches_correct_routes(): calls: {calls}")
        # Second call: outbound (origin → destination, no return_date)
        outbound_kwargs = calls[1].kwargs
        self.assertIsNone(outbound_kwargs.get("return_date"))
        # Third call: inbound (destination → origin, no return_date)
        inbound_kwargs = calls[2].kwargs
        self.assertIsNone(inbound_kwargs.get("return_date"))
        # Outbound origin == inbound destination (swapped)
        self.assertEqual(outbound_kwargs["origin"], inbound_kwargs["destination"])
        self.assertEqual(outbound_kwargs["destination"], inbound_kwargs["origin"])


    @patch("agents.air_ticket.get_flight_service")
    def test_one_way_fallback_not_triggered_for_one_way_trip(self, mock_get_svc):
        """No return_date means no one-way fallback; goes straight to static fallback."""
        mock_svc = MagicMock()
        mock_svc.search_flights.return_value = []
        mock_get_svc.return_value = mock_svc

        state = self._base_state()
        state["days"] = 1  # one-way trip

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(state)

        # Only one call; no separate one-way pair searched
        mock_svc.search_flights.assert_called_once()
        # Still returns some flight option (static fallback)
        flights = [option for option in new_state["transport_options"] if option.get("type") == "flight"]
        self.assertGreater(len(flights), 0)

    # ------------------------------------------------------------------
    # Both round-trip and one-way return nothing → static fallback
    # ------------------------------------------------------------------

    @patch("agents.air_ticket.get_flight_service")
    def test_static_fallback_when_all_searches_empty(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.search_flights.side_effect = [[], [], []]  # or: mock_svc.search_flights.return_value = []
        mock_get_svc.return_value = mock_svc

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(self._base_state())

        #mock_svc.search_flights.assert_called_once()
        self.assertEqual(mock_svc.search_flights.call_count, 3)
        flights = [option for option in new_state["transport_options"] if option.get("type") == "flight"]
        self.assertGreater(len(flights), 0, "Static fallback should always produce at least one option")


    # ------------------------------------------------------------------
    # Top-3 per direction when one-way pair is used
    # ------------------------------------------------------------------

    @patch("agents.air_ticket.get_flight_service")
    def test_one_way_pair_selects_top3_per_direction(self, mock_get_svc):
        outbound_flights = [self._make_flight("outbound", f"A{i}") for i in range(5)]
        inbound_flights = [self._make_flight("inbound", f"B{i}") for i in range(4)]

        mock_svc = MagicMock()
        mock_svc.search_flights.side_effect = [[], outbound_flights, inbound_flights]
        mock_get_svc.return_value = mock_svc

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(self._base_state())

        flights = [o for o in new_state["transport_options"] if o.get("type") == "flight"]
        # top-3 outbound + top-3 inbound = 6 total
        self.assertEqual(len(flights), 6)

    # ------------------------------------------------------------------
    # Multi-airport city searches all code combinations
    # ------------------------------------------------------------------

    @patch("agents.air_ticket.get_flight_service")
    def test_multi_airport_origin_searches_all_code_combinations(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.search_flights.return_value = []
        mock_get_svc.return_value = mock_svc

        state = self._base_state()
        state["origin"] = "Shanghai"   # multi-airport: ["PVG", "SHA"]
        # destination stays "Tokyo" → single code ["NRT"]

        from agents.air_ticket import air_ticket_agent
        air_ticket_agent(state)

        # Round-trip: PVG→NRT + SHA→NRT = 2
        # One-way outbound: PVG→NRT + SHA→NRT = 2
        # One-way inbound: NRT→PVG + NRT→SHA = 2
        self.assertEqual(mock_svc.search_flights.call_count, 6)


if __name__ == "__main__":
    unittest.main()
