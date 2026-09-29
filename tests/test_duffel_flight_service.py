import json
import unittest
from unittest.mock import MagicMock, patch
from urllib import error as urllib_error

from services import duffel_flight
from services.duffel_flight import _apply_leg_prices, _passes_preference, _cache_key, _parse_segment


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

    def test_stamps_currency_on_every_leg(self):
        legs = [{"airline": "CX"}, {"airline": "CX"}]
        priced = _apply_leg_prices(legs, 278.83, currency="JPY")
        self.assertEqual(priced[0]["currency"], "JPY")
        self.assertEqual(priced[1]["currency"], "JPY")

    def test_currency_defaults_to_usd(self):
        priced = _apply_leg_prices([{"airline": "CX"}], 500.0)
        self.assertEqual(priced[0]["currency"], "USD")


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

    def test_budget_check_converts_leg_currency_to_usd(self):
        # 20,000 JPY at 200 JPY per USD is 100 USD, comfortably under the cap.
        legs = [{"airline": "CX", "depart_time": "10:00:00", "price": 20000, "currency": "JPY"}]
        with patch.object(duffel_flight, "to_usd", return_value=100.0) as mock_to_usd:
            passed = _passes_preference(legs, {"max_price_per_ticket": 300})

        self.assertTrue(passed)
        mock_to_usd.assert_called_once_with(20000, "JPY")

    def test_non_usd_offer_exceeding_cap_is_rejected(self):
        # 500,000 JPY is 2,500 USD at 200 JPY/USD — over the 1,000 USD cap even
        # though the raw number alone would look like it passes a naive check.
        legs = [{"airline": "CX", "depart_time": "10:00:00", "price": 500000, "currency": "JPY"}]
        with patch.object(duffel_flight, "to_usd", return_value=2500.0):
            passed = _passes_preference(legs, {"max_price_per_ticket": 1000})

        self.assertFalse(passed)

    def test_legs_without_currency_are_treated_as_usd(self):
        legs = [{"airline": "CX", "depart_time": "10:00:00", "price": 500}]
        with patch.object(duffel_flight, "to_usd", return_value=500.0) as mock_to_usd:
            passed = _passes_preference(legs, {"max_price_per_ticket": 1000})

        self.assertTrue(passed)
        mock_to_usd.assert_called_once_with(500, "USD")


class CacheKeyTests(unittest.TestCase):
    def test_same_args_produce_same_key(self):
        key1 = _cache_key(origin="HKG", destination="KIX", outbound_preference={"airlines": ["CX"]})
        key2 = _cache_key(origin="HKG", destination="KIX", outbound_preference={"airlines": ["CX"]})
        self.assertEqual(key1, key2)

    def test_different_args_produce_different_keys(self):
        key1 = _cache_key(origin="HKG", destination="KIX")
        key2 = _cache_key(origin="HKG", destination="NRT")
        self.assertNotEqual(key1, key2)


class ParseSegmentTests(unittest.TestCase):
    def test_parses_segment_fields(self):
        segment = {
            "origin": {"iata_code": "HKG"},
            "destination": {"iata_code": "TAO"},
            "departing_at": "2026-09-10T17:30:00",
            "arriving_at": "2026-09-10T20:40:00",
            "operating_carrier": {"iata_code": "SC", "name": "Shandong Airlines"},
        }
        leg = _parse_segment(segment)
        self.assertEqual(leg["airline"], "SC")
        self.assertEqual(leg["from"], "HKG")
        self.assertEqual(leg["to"], "TAO")
        self.assertEqual(leg["depart_time"], "17:30:00")
        self.assertEqual(leg["arrival_time"], "20:40:00")
        self.assertEqual(leg["departure_date"], "2026-09-10")


class MockFlightSearchTests(unittest.TestCase):
    def _service(self):
        from services.duffel_flight import DuffelFlightService
        service = DuffelFlightService.__new__(DuffelFlightService)
        service.use_mock = True
        service.api_key = None
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
            self.assertTrue(all("currency" in leg for leg in candidate["outbound_legs"]))
            self.assertTrue(all("currency" in leg for leg in candidate["inbound_legs"]))

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


class GetFlightServiceTests(unittest.TestCase):
    def test_returns_singleton(self):
        import services.duffel_flight as module
        module._flight_service = None
        first = module.get_flight_service()
        second = module.get_flight_service()
        self.assertIs(first, second)


class SearchFlightsTests(unittest.TestCase):
    def _round_trip_offer(self):
        return {
            "total_amount": "600.00",
            "total_currency": "USD",
            "slices": [
                {"segments": [
                    {"origin": {"iata_code": "HKG"}, "destination": {"iata_code": "KIX"},
                     "departing_at": "2026-09-10T17:30:00", "arriving_at": "2026-09-10T22:00:00",
                     "operating_carrier": {"iata_code": "CX"}},
                ]},
                {"segments": [
                    {"origin": {"iata_code": "KIX"}, "destination": {"iata_code": "HKG"},
                     "departing_at": "2026-09-16T21:45:00", "arriving_at": "2026-09-17T01:00:00",
                     "operating_carrier": {"iata_code": "CX"}},
                ]},
            ],
        }

    def _round_trip_offer_with_airlines(self, outbound_airline, inbound_airline):
        return {
            "total_amount": "600.00",
            "total_currency": "USD",
            "slices": [
                {"segments": [
                    {"origin": {"iata_code": "HKG"}, "destination": {"iata_code": "KIX"},
                     "departing_at": "2026-09-10T17:30:00", "arriving_at": "2026-09-10T22:00:00",
                     "operating_carrier": {"iata_code": outbound_airline}},
                ]},
                {"segments": [
                    {"origin": {"iata_code": "KIX"}, "destination": {"iata_code": "HKG"},
                     "departing_at": "2026-09-16T21:45:00", "arriving_at": "2026-09-17T01:00:00",
                     "operating_carrier": {"iata_code": inbound_airline}},
                ]},
            ],
        }

    def _service(self):
        from services.duffel_flight import DuffelFlightService
        service = DuffelFlightService.__new__(DuffelFlightService)
        service.use_mock = False
        service.api_key = "test-key"
        service._cache = {}
        service._cache_ttl_seconds = 900
        return service

    def _mock_http_response(self, body: dict):
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode("utf-8")
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    @patch("urllib.request.urlopen")
    def test_search_flights_returns_split_candidate_and_caches_result(self, mock_urlopen):
        service = self._service()
        mock_urlopen.return_value = self._mock_http_response({"data": {"offers": [self._round_trip_offer()]}})

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
        # Each leg carries its own currency so downstream budget checks can convert.
        self.assertEqual(candidate["outbound_legs"][0]["currency"], "USD")
        self.assertEqual(candidate["inbound_legs"][0]["currency"], "USD")

        # Cache populated
        self.assertEqual(len(service._cache), 1)

    @patch("urllib.request.urlopen")
    def test_http_error_returns_duffel_error_reason(self, mock_urlopen):
        service = self._service()
        mock_urlopen.side_effect = urllib_error.HTTPError(
            url="https://api.duffel.com/air/offer_requests", code=401, msg="Unauthorized",
            hdrs=None, fp=None,
        )

        result = service.search_flights(origin="HKG", destination="KIX", departure_date="2026-09-10")
        self.assertEqual(result, [{"type": "flight", "reason": "Duffel API error"}])

    @patch("urllib.request.urlopen")
    def test_unexpected_error_returns_unknown_error_reason(self, mock_urlopen):
        service = self._service()
        mock_urlopen.side_effect = ValueError("boom")

        result = service.search_flights(origin="HKG", destination="KIX", departure_date="2026-09-10")
        self.assertEqual(result, [{"type": "flight", "reason": "Unknown error"}])

    @patch("urllib.request.urlopen")
    def test_inbound_failure_drops_whole_candidate_even_when_outbound_passes(self, mock_urlopen):
        service = self._service()
        offer = self._round_trip_offer_with_airlines(outbound_airline="CX", inbound_airline="UO")
        mock_urlopen.return_value = self._mock_http_response({"data": {"offers": [offer]}})

        result = service.search_flights(
            origin="HKG", destination="KIX",
            departure_date="2026-09-10", return_date="2026-09-16",
            outbound_preference={"airlines": ["CX"]},
            inbound_preference={"excluded_airlines": ["UO"]},
        )

        self.assertEqual(result, [])

    @patch("urllib.request.urlopen")
    def test_outbound_failure_drops_whole_candidate_even_when_inbound_would_pass(self, mock_urlopen):
        service = self._service()
        offer = self._round_trip_offer_with_airlines(outbound_airline="CX", inbound_airline="UO")
        mock_urlopen.return_value = self._mock_http_response({"data": {"offers": [offer]}})

        result = service.search_flights(
            origin="HKG", destination="KIX",
            departure_date="2026-09-10", return_date="2026-09-16",
            outbound_preference={"excluded_airlines": ["CX"]},
            inbound_preference={"airlines": ["UO"]},
        )

        self.assertEqual(result, [])

    @patch("urllib.request.urlopen")
    def test_cache_hit_returns_cached_result_without_calling_api_again(self, mock_urlopen):
        service = self._service()
        mock_urlopen.return_value = self._mock_http_response({"data": {"offers": [self._round_trip_offer()]}})

        kwargs = dict(
            origin="HKG", destination="KIX",
            departure_date="2026-09-10", return_date="2026-09-16",
        )

        first_result = service.search_flights(**kwargs)
        second_result = service.search_flights(**kwargs)

        # The real HTTP call must only be hit once - the second call is served from self._cache.
        self.assertEqual(mock_urlopen.call_count, 1)
        self.assertEqual(first_result, second_result)


if __name__ == "__main__":
    unittest.main()
