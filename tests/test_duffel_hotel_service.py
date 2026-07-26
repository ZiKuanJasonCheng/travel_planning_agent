import json
import unittest
from unittest.mock import MagicMock, patch
from urllib import error as urllib_error

from services.duffel_hotel import _default_check_in_date, _default_check_out_date, _nights, _passes_hotel_filters, _cache_key, _parse_search_result, DuffelHotelService


class DefaultDatesTests(unittest.TestCase):
    def test_default_check_out_is_one_night_after_check_in(self):
        self.assertEqual(_default_check_out_date("2026-09-10"), "2026-09-11")


class NightsTests(unittest.TestCase):
    def test_computes_nights_between_dates(self):
        self.assertEqual(_nights("2026-09-10", "2026-09-14"), 4)

    def test_same_day_checkin_checkout_returns_minimum_one(self):
        self.assertEqual(_nights("2026-09-10", "2026-09-10"), 1)


class PassesHotelFiltersTests(unittest.TestCase):
    def test_no_filters_always_passes(self):
        self.assertTrue(_passes_hotel_filters(200, "Shinjuku", None, None))

    def test_over_budget_fails(self):
        self.assertFalse(_passes_hotel_filters(200, "Shinjuku", 150, None))

    def test_within_budget_passes(self):
        self.assertTrue(_passes_hotel_filters(120, "Shinjuku", 150, None))

    def test_area_mismatch_fails(self):
        self.assertFalse(_passes_hotel_filters(120, "Ueno", 150, "Shinjuku"))

    def test_area_substring_match_passes(self):
        self.assertTrue(_passes_hotel_filters(120, "Nishi-Shinjuku, Tokyo", 150, "Shinjuku"))


class CacheKeyTests(unittest.TestCase):
    def test_same_args_produce_same_key(self):
        key1 = _cache_key(destination="Tokyo", check_in_date="2026-09-10")
        key2 = _cache_key(destination="Tokyo", check_in_date="2026-09-10")
        self.assertEqual(key1, key2)

    def test_different_args_produce_different_keys(self):
        key1 = _cache_key(destination="Tokyo")
        key2 = _cache_key(destination="Kyoto")
        self.assertNotEqual(key1, key2)


class ParseSearchResultTests(unittest.TestCase):
    def _result(self, **overrides):
        base = {
            "cheapest_rate_total_amount": "480.00",
            "cheapest_rate_currency": "USD",
            "accommodation": {
                "id": "acc_0000123",
                "name": "Shinjuku Grand Hotel",
                "location": {
                    "address": {"city_name": "Tokyo", "region": "Kanto"},
                    "geographic_coordinates": {"latitude": 35.6938, "longitude": 139.7034},
                },
            },
        }
        base.update(overrides)
        return base

    def test_parses_fields_and_derives_nightly_price(self):
        hotel = _parse_search_result(self._result(), nights=4)
        self.assertEqual(hotel["type"], "hotel")
        self.assertEqual(hotel["name"], "Shinjuku Grand Hotel")
        self.assertEqual(hotel["price_per_night"], 120)
        self.assertEqual(hotel["currency"], "USD")
        self.assertEqual(hotel["area"], "Tokyo")
        self.assertEqual(hotel["hotel_id"], "acc_0000123")
        self.assertEqual(hotel["lat"], 35.6938)
        self.assertEqual(hotel["lon"], 139.7034)
        self.assertEqual(hotel["supplier"], "duffel")
        self.assertEqual(hotel["reason"], "Duffel Stays offer")

    def test_missing_price_returns_none(self):
        result = self._result()
        del result["cheapest_rate_total_amount"]
        self.assertIsNone(_parse_search_result(result, nights=4))

    def test_missing_city_name_falls_back_to_region(self):
        result = self._result()
        result["accommodation"]["location"]["address"] = {"region": "Kanto"}
        hotel = _parse_search_result(result, nights=4)
        self.assertEqual(hotel["area"], "Kanto")

    def test_missing_address_falls_back_to_unknown_area(self):
        result = self._result()
        result["accommodation"]["location"] = {}
        hotel = _parse_search_result(result, nights=4)
        self.assertEqual(hotel["area"], "unknown area")


class MockHotelSearchTests(unittest.TestCase):
    def _service(self):
        service = DuffelHotelService.__new__(DuffelHotelService)
        service.use_mock = True
        service.api_key = None
        service._cache = {}
        service._cache_ttl_seconds = 900
        return service

    def test_mock_search_returns_priced_hotels(self):
        service = self._service()
        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )
        self.assertGreater(len(result), 0)
        for hotel in result:
            self.assertIn("price_per_night", hotel)
            self.assertEqual(hotel["supplier"], "duffel")

    def test_mock_search_respects_max_price_per_night(self):
        service = self._service()
        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
            max_price_per_night=100,
        )
        self.assertGreater(len(result), 0)
        for hotel in result:
            self.assertLessEqual(hotel["price_per_night"], 100)

    def test_mock_search_respects_preferred_area(self):
        service = self._service()
        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
            preferred_area="riverside",
        )
        self.assertGreater(len(result), 0)
        for hotel in result:
            self.assertIn("riverside", hotel["area"].lower())


class GetDuffelHotelServiceTests(unittest.TestCase):
    def test_returns_singleton(self):
        import services.duffel_hotel as module
        module._hotel_service = None
        first = module.get_duffel_hotel_service()
        second = module.get_duffel_hotel_service()
        self.assertIs(first, second)


class SearchHotelsTests(unittest.TestCase):
    def _service(self):
        from services.duffel_hotel import DuffelHotelService
        service = DuffelHotelService.__new__(DuffelHotelService)
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

    def _search_result(self, total_amount="480.00"):
        return {
            "cheapest_rate_total_amount": total_amount,
            "cheapest_rate_currency": "USD",
            "accommodation": {
                "id": "acc_0000123",
                "name": "Shinjuku Grand Hotel",
                "location": {
                    "address": {"city_name": "Tokyo"},
                    "geographic_coordinates": {"latitude": 35.6938, "longitude": 139.7034},
                },
            },
        }

    @patch("services.duffel_hotel.fetch_coordinates")
    @patch("urllib.request.urlopen")
    def test_search_hotels_returns_parsed_and_priced_result(self, mock_urlopen, mock_coords):
        mock_coords.return_value = (35.6895, 139.6917)
        mock_urlopen.return_value = self._mock_http_response(
            {"data": {"results": [self._search_result()], "created_at": "2026-07-25T00:00:00Z"}}
        )
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )

        self.assertEqual(len(result), 1)
        hotel = result[0]
        self.assertEqual(hotel["name"], "Shinjuku Grand Hotel")
        self.assertEqual(hotel["price_per_night"], 120)
        self.assertEqual(hotel["supplier"], "duffel")

        # Cache populated
        self.assertEqual(len(service._cache), 1)

    @patch("services.duffel_hotel.fetch_coordinates")
    def test_geocode_failure_returns_empty_list(self, mock_coords):
        mock_coords.return_value = None
        service = self._service()

        result = service.search_hotels(
            destination="Nowhereville", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )
        self.assertEqual(result, [])

    @patch("services.duffel_hotel.fetch_coordinates")
    @patch("urllib.request.urlopen")
    def test_http_error_returns_duffel_hotel_error_reason(self, mock_urlopen, mock_coords):
        mock_coords.return_value = (35.6895, 139.6917)
        mock_urlopen.side_effect = urllib_error.HTTPError(
            url="https://api.duffel.com/stays/search", code=401, msg="Unauthorized", hdrs=None, fp=None,
        )
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )
        self.assertEqual(result, [{"reason": "Duffel Hotel API error"}])

    @patch("services.duffel_hotel.fetch_coordinates")
    def test_unexpected_error_returns_unknown_error_reason(self, mock_coords):
        mock_coords.side_effect = ValueError("boom")
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )
        self.assertEqual(result, [{"reason": "Unknown error"}])

    @patch("services.duffel_hotel.fetch_coordinates")
    @patch("urllib.request.urlopen")
    def test_max_price_filters_out_expensive_hotel(self, mock_urlopen, mock_coords):
        mock_coords.return_value = (35.6895, 139.6917)
        mock_urlopen.return_value = self._mock_http_response(
            {"data": {"results": [self._search_result(total_amount="480.00")], "created_at": "2026-07-25T00:00:00Z"}}
        )
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
            max_price_per_night=100,
        )
        self.assertEqual(result, [])

    @patch("services.duffel_hotel.fetch_coordinates")
    @patch("urllib.request.urlopen")
    def test_cache_hit_returns_cached_result_without_calling_api_again(self, mock_urlopen, mock_coords):
        mock_coords.return_value = (35.6895, 139.6917)
        mock_urlopen.return_value = self._mock_http_response(
            {"data": {"results": [self._search_result()], "created_at": "2026-07-25T00:00:00Z"}}
        )
        service = self._service()
        kwargs = dict(destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14")

        first_result = service.search_hotels(**kwargs)
        second_result = service.search_hotels(**kwargs)

        # The real HTTP call must only be hit once - the second call is served from self._cache.
        self.assertEqual(mock_urlopen.call_count, 1)
        self.assertEqual(first_result, second_result)


if __name__ == "__main__":
    unittest.main()
