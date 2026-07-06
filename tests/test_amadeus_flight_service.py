import unittest

from services.amadeus_flight import _parse_segment, _apply_leg_prices, _passes_preference, _cache_key


class ParseSegmentTests(unittest.TestCase):
    def test_parses_segment_fields(self):
        segment = {
            "departure": {"iataCode": "HKG", "terminal": "1", "at": "2026-09-10T17:30:00"},
            "arrival": {"iataCode": "TAO", "at": "2026-09-10T20:40:00"},
            "carrierCode": "SC",
            "number": "4632",
        }
        leg = _parse_segment(segment)
        self.assertEqual(leg["airline"], "SC")
        self.assertEqual(leg["from"], "HKG")
        self.assertEqual(leg["to"], "TAO")
        self.assertEqual(leg["depart_time"], "17:30:00")
        self.assertEqual(leg["arrival_time"], "20:40:00")
        self.assertEqual(leg["departure_date"], "2026-09-10")


class ApplyLegPricesTests(unittest.TestCase):
    def test_splits_price_evenly_across_two_legs(self):
        legs = [{"airline": "SC"}, {"airline": "SC"}]
        priced = _apply_leg_prices(legs, 278.83)
        self.assertEqual(priced[0]["price"], 139)
        self.assertEqual(priced[1]["price"], 139)

    def test_single_leg_gets_full_price(self):
        legs = [{"airline": "CX"}]
        priced = _apply_leg_prices(legs, 500.0)
        self.assertEqual(priced[0]["price"], 500)

    def test_empty_legs_returns_empty(self):
        self.assertEqual(_apply_leg_prices([], 500.0), [])

    def test_does_not_mutate_input_legs(self):
        legs = [{"airline": "CX"}]
        _apply_leg_prices(legs, 500.0)
        self.assertNotIn("price", legs[0])


class PassesPreferenceTests(unittest.TestCase):
    def _legs(self, airline="CX", depart_time="10:00:00", price=200, count=1):
        return [{"airline": airline, "depart_time": depart_time, "price": price} for _ in range(count)]

    def test_no_preference_always_passes(self):
        self.assertTrue(_passes_preference(self._legs(), None))

    def test_excluded_airline_fails(self):
        pref = {"excluded_airlines": ["CX"]}
        self.assertFalse(_passes_preference(self._legs(airline="CX"), pref))

    def test_preferred_airline_whitelist_fails_when_not_matched(self):
        pref = {"airlines": ["UO"]}
        self.assertFalse(_passes_preference(self._legs(airline="CX"), pref))

    def test_direct_flights_only_fails_for_multi_leg(self):
        pref = {"direct_flights_only": True}
        self.assertFalse(_passes_preference(self._legs(count=2), pref))

    def test_direct_flights_only_passes_for_single_leg(self):
        pref = {"direct_flights_only": True}
        self.assertTrue(_passes_preference(self._legs(count=1), pref))

    def test_redeye_rejected_when_not_accepted(self):
        pref = {"accept_redeye_flights": False}
        self.assertFalse(_passes_preference(self._legs(depart_time="00:30:00"), pref))

    def test_max_price_per_ticket_fails_when_over_budget(self):
        pref = {"max_price_per_ticket": 100}
        self.assertFalse(_passes_preference(self._legs(price=200), pref))

    def test_max_price_per_ticket_passes_when_within_budget(self):
        pref = {"max_price_per_ticket": 300}
        self.assertTrue(_passes_preference(self._legs(price=200), pref))


class CacheKeyTests(unittest.TestCase):
    def test_same_args_produce_same_key(self):
        key1 = _cache_key(origin="HKG", destination="KIX", outbound_preference={"airlines": ["CX"]})
        key2 = _cache_key(origin="HKG", destination="KIX", outbound_preference={"airlines": ["CX"]})
        self.assertEqual(key1, key2)

    def test_different_args_produce_different_keys(self):
        key1 = _cache_key(origin="HKG", destination="KIX")
        key2 = _cache_key(origin="HKG", destination="NRT")
        self.assertNotEqual(key1, key2)


class MockFlightSearchTests(unittest.TestCase):
    def _service(self):
        from services.amadeus_flight import AmadeusFlightService
        service = AmadeusFlightService.__new__(AmadeusFlightService)
        service.use_mock = True
        service.client = None
        service._cache = {}
        service._cache_ttl_seconds = 900
        return service

    def test_round_trip_mock_returns_priced_candidates(self):
        service = self._service()
        result = service.search_flights(
            origin="HKG", destination="KIX",
            departure_date="2026-09-10", return_date="2026-09-16",
        )
        self.assertGreater(len(result), 0)
        for candidate in result:
            self.assertIn("outbound_legs", candidate)
            self.assertIn("inbound_legs", candidate)
            self.assertTrue(all("price" in leg for leg in candidate["outbound_legs"]))
            self.assertTrue(all("price" in leg for leg in candidate["inbound_legs"]))

    def test_one_way_mock_has_no_inbound_legs(self):
        service = self._service()
        result = service.search_flights(origin="HKG", destination="KIX", departure_date="2026-09-10")
        self.assertGreater(len(result), 0)
        for candidate in result:
            self.assertIsNone(candidate["inbound_legs"])

    def test_direct_flights_only_filters_multi_leg_mock_candidate(self):
        service = self._service()
        result = service.search_flights(
            origin="HKG", destination="KIX", departure_date="2026-09-10",
            outbound_preference={"direct_flights_only": True},
        )
        for candidate in result:
            self.assertEqual(candidate["stops_outbound"], 0)


class SearchFlightsTests(unittest.TestCase):
    def _round_trip_offer(self):
        return {
            "itineraries": [
                {"segments": [
                    {"departure": {"iataCode": "HKG", "at": "2026-09-10T17:30:00"},
                     "arrival": {"iataCode": "KIX", "at": "2026-09-10T22:00:00"},
                     "carrierCode": "CX"},
                ]},
                {"segments": [
                    {"departure": {"iataCode": "KIX", "at": "2026-09-16T21:45:00"},
                     "arrival": {"iataCode": "HKG", "at": "2026-09-17T01:00:00"},
                     "carrierCode": "CX"},
                ]},
            ],
            "price": {"currency": "USD", "total": "600.00"},
        }

    def _round_trip_offer_with_airlines(self, outbound_airline, inbound_airline):
        return {
            "itineraries": [
                {"segments": [
                    {"departure": {"iataCode": "HKG", "at": "2026-09-10T17:30:00"},
                     "arrival": {"iataCode": "KIX", "at": "2026-09-10T22:00:00"},
                     "carrierCode": outbound_airline},
                ]},
                {"segments": [
                    {"departure": {"iataCode": "KIX", "at": "2026-09-16T21:45:00"},
                     "arrival": {"iataCode": "HKG", "at": "2026-09-17T01:00:00"},
                     "carrierCode": inbound_airline},
                ]},
            ],
            "price": {"currency": "USD", "total": "600.00"},
        }

    def _service_with_offers(self, offers):
        from services.amadeus_flight import AmadeusFlightService

        service = AmadeusFlightService.__new__(AmadeusFlightService)
        service.use_mock = False
        service._cache = {}
        service._cache_ttl_seconds = 900
        mock_response = type("R", (), {"data": offers})()
        service.client = type("C", (), {
            "shopping": type("S", (), {
                "flight_offers_search": type("F", (), {"get": lambda self, **kw: mock_response})()
            })()
        })()
        return service

    def test_search_flights_returns_split_candidate_and_caches_result(self):
        from services.amadeus_flight import AmadeusFlightService

        service = AmadeusFlightService.__new__(AmadeusFlightService)
        service.use_mock = False
        service._cache = {}
        service._cache_ttl_seconds = 900
        mock_response = type("R", (), {"data": [self._round_trip_offer()]})()
        service.client = type("C", (), {
            "shopping": type("S", (), {
                "flight_offers_search": type("F", (), {"get": lambda self, **kw: mock_response})()
            })()
        })()

        result = service.search_flights(
            origin="HKG", destination="KIX",
            departure_date="2026-09-10", return_date="2026-09-16",
        )

        self.assertEqual(len(result), 1)
        candidate = result[0]
        self.assertEqual(candidate["price"], 600)
        self.assertEqual(candidate["outbound_legs"][0]["price"], 300)
        self.assertEqual(candidate["inbound_legs"][0]["price"], 300)
        self.assertEqual(candidate["stops_outbound"], 0)
        self.assertEqual(candidate["stops_inbound"], 0)

        # Cache populated
        self.assertEqual(len(service._cache), 1)

    def test_response_error_returns_amadeus_error_reason(self):
        from services.amadeus_flight import AmadeusFlightService
        from amadeus import ResponseError

        service = AmadeusFlightService.__new__(AmadeusFlightService)
        service.use_mock = False
        service._cache = {}
        service._cache_ttl_seconds = 900

        def _raise(**kw):
            raise ResponseError(MagicMockResponse())

        class MagicMockResponse:
            status_code = 500
            result = None
            parsed = False

        service.client = type("C", (), {
            "shopping": type("S", (), {
                "flight_offers_search": type("F", (), {"get": lambda self, **kw: _raise(**kw)})()
            })()
        })()

        result = service.search_flights(origin="HKG", destination="KIX", departure_date="2026-09-10")
        self.assertEqual(result, [{"type": "flight", "reason": "Amadeus API error"}])

    def test_unexpected_error_returns_unknown_error_reason(self):
        from services.amadeus_flight import AmadeusFlightService

        service = AmadeusFlightService.__new__(AmadeusFlightService)
        service.use_mock = False
        service._cache = {}
        service._cache_ttl_seconds = 900

        def _raise(**kw):
            raise ValueError("boom")

        service.client = type("C", (), {
            "shopping": type("S", (), {
                "flight_offers_search": type("F", (), {"get": lambda self, **kw: _raise(**kw)})()
            })()
        })()

        result = service.search_flights(origin="HKG", destination="KIX", departure_date="2026-09-10")
        self.assertEqual(result, [{"type": "flight", "reason": "Unknown error"}])

    def test_inbound_failure_drops_whole_candidate_even_when_outbound_passes(self):
        # Outbound is CX and satisfies outbound_preference; inbound is UO and is excluded
        # by inbound_preference. Neither direction can rescue the other's rejection, so
        # the whole round-trip candidate must be dropped.
        offer = self._round_trip_offer_with_airlines(outbound_airline="CX", inbound_airline="UO")
        service = self._service_with_offers([offer])

        result = service.search_flights(
            origin="HKG", destination="KIX",
            departure_date="2026-09-10", return_date="2026-09-16",
            outbound_preference={"airlines": ["CX"]},
            inbound_preference={"excluded_airlines": ["UO"]},
        )

        self.assertEqual(result, [])

    def test_outbound_failure_drops_whole_candidate_even_when_inbound_would_pass(self):
        # Converse case: outbound is CX and is excluded by outbound_preference; inbound is
        # UO and would satisfy inbound_preference on its own. The candidate must still be
        # dropped because outbound's rejection is not overridden by inbound passing.
        offer = self._round_trip_offer_with_airlines(outbound_airline="CX", inbound_airline="UO")
        service = self._service_with_offers([offer])

        result = service.search_flights(
            origin="HKG", destination="KIX",
            departure_date="2026-09-10", return_date="2026-09-16",
            outbound_preference={"excluded_airlines": ["CX"]},
            inbound_preference={"airlines": ["UO"]},
        )

        self.assertEqual(result, [])

    def test_cache_hit_returns_cached_result_without_calling_api_again(self):
        from services.amadeus_flight import AmadeusFlightService

        service = AmadeusFlightService.__new__(AmadeusFlightService)
        service.use_mock = False
        service._cache = {}
        service._cache_ttl_seconds = 900

        mock_response = type("R", (), {"data": [self._round_trip_offer()]})()

        class _CountingSearch:
            def __init__(self, response):
                self.response = response
                self.call_count = 0

            def get(self, **kw):
                self.call_count += 1
                return self.response

        counting_search = _CountingSearch(mock_response)
        service.client = type("C", (), {
            "shopping": type("S", (), {"flight_offers_search": counting_search})()
        })()

        kwargs = dict(
            origin="HKG", destination="KIX",
            departure_date="2026-09-10", return_date="2026-09-16",
        )

        first_result = service.search_flights(**kwargs)
        second_result = service.search_flights(**kwargs)

        # The real "API" (flight_offers_search.get) must only be hit once — the second
        # call should be served entirely from self._cache.
        self.assertEqual(counting_search.call_count, 1)
        self.assertEqual(first_result, second_result)


if __name__ == "__main__":
    unittest.main()
