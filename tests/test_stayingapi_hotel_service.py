import json
import unittest
from unittest.mock import MagicMock, patch
from urllib import error as urllib_error

from services.stayingapi_hotel import (
    _default_check_in_date,
    _default_check_out_date,
    _nights,
    _passes_hotel_filters,
    _cache_key,
    _parse_search_result,
    StayingAPIHotelService,
)


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
            "id": "prop_0000123",
            "name": "Shinjuku Grand Hotel",
            "location": {"city": "Tokyo", "region": "Kanto", "lat": 35.6938, "lng": 139.7034},
            "price": {"nightlyPrice": 120.0, "currency": "USD"},
        }
        base.update(overrides)
        return base

    def test_parses_fields_directly_without_dividing_by_nights(self):
        hotel = _parse_search_result(self._result())
        self.assertEqual(hotel["type"], "hotel")
        self.assertEqual(hotel["name"], "Shinjuku Grand Hotel")
        self.assertEqual(hotel["price_per_night"], 120)
        self.assertEqual(hotel["currency"], "USD")
        self.assertEqual(hotel["area"], "Tokyo")
        self.assertEqual(hotel["hotel_id"], "prop_0000123")
        self.assertEqual(hotel["lat"], 35.6938)
        self.assertEqual(hotel["lon"], 139.7034)
        self.assertEqual(hotel["supplier"], "stayingapi")
        self.assertEqual(hotel["reason"], "StayingAPI offer")

    def test_missing_price_returns_none(self):
        result = self._result()
        result["price"] = {}
        self.assertIsNone(_parse_search_result(result))

    def test_null_price_returns_none(self):
        result = self._result()
        result["price"] = None
        self.assertIsNone(_parse_search_result(result))

    def test_missing_city_falls_back_to_region(self):
        result = self._result()
        result["location"] = {"region": "Kanto"}
        hotel = _parse_search_result(result)
        self.assertEqual(hotel["area"], "Kanto")

    def test_missing_location_falls_back_to_unknown_area(self):
        result = self._result()
        result["location"] = {}
        hotel = _parse_search_result(result)
        self.assertEqual(hotel["area"], "unknown area")


class MockHotelSearchTests(unittest.TestCase):
    def _service(self):
        service = StayingAPIHotelService.__new__(StayingAPIHotelService)
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
            self.assertEqual(hotel["supplier"], "stayingapi")

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


class GetStayingAPIHotelServiceTests(unittest.TestCase):
    def test_returns_singleton(self):
        import services.stayingapi_hotel as module
        module._hotel_service = None
        first = module.get_stayingapi_hotel_service()
        second = module.get_stayingapi_hotel_service()
        self.assertIs(first, second)


class SearchHotelsTests(unittest.TestCase):
    def _service(self):
        service = StayingAPIHotelService.__new__(StayingAPIHotelService)
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

    def _search_result(self, nightly_price=120.0):
        return {
            "id": "prop_0000123",
            "name": "Shinjuku Grand Hotel",
            "location": {"city": "Tokyo", "lat": 35.6938, "lng": 139.7034},
            "price": {"nightlyPrice": nightly_price, "currency": "USD"},
        }

    @patch("urllib.request.urlopen")
    def test_search_hotels_returns_parsed_and_priced_result(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_http_response(
            {"data": {"results": [self._search_result()]}}
        )
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )

        self.assertEqual(len(result), 1)
        hotel = result[0]
        self.assertEqual(hotel["name"], "Shinjuku Grand Hotel")
        self.assertEqual(hotel["price_per_night"], 120)
        self.assertEqual(hotel["supplier"], "stayingapi")

        # Cache populated
        self.assertEqual(len(service._cache), 1)

    @patch("urllib.request.urlopen")
    def test_http_error_returns_stayingapi_hotel_error_reason(self, mock_urlopen):
        mock_urlopen.side_effect = urllib_error.HTTPError(
            url="https://api.stayingapi.com/v1/search", code=401, msg="Unauthorized", hdrs=None, fp=None,
        )
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )
        self.assertEqual(result, [{"reason": "StayingAPI Hotel API error"}])

    @patch("urllib.request.urlopen")
    def test_unexpected_error_returns_unknown_error_reason(self, mock_urlopen):
        mock_urlopen.side_effect = ValueError("boom")
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )
        self.assertEqual(result, [{"reason": "Unknown error"}])

    @patch("urllib.request.urlopen")
    def test_max_price_filters_out_expensive_hotel(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_http_response(
            {"data": {"results": [self._search_result(nightly_price=480.0)]}}
        )
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
            max_price_per_night=100,
        )
        self.assertEqual(result, [])

    @patch("urllib.request.urlopen")
    def test_cache_hit_returns_cached_result_without_calling_api_again(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_http_response(
            {"data": {"results": [self._search_result()]}}
        )
        service = self._service()
        kwargs = dict(destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14")

        first_result = service.search_hotels(**kwargs)
        second_result = service.search_hotels(**kwargs)

        # The real HTTP call must only be hit once - the second call is served from self._cache.
        self.assertEqual(mock_urlopen.call_count, 1)
        self.assertEqual(first_result, second_result)

    @patch("urllib.request.urlopen")
    def test_pending_job_polls_until_completed(self, mock_urlopen):
        pending_response = self._mock_http_response(
            {"data": {"status": "pending", "jobId": "job_1", "pollUrl": "/v1/jobs/job_1"}}
        )
        completed_response = self._mock_http_response(
            {"data": {"status": "completed", "result": {"results": [self._search_result()]}}}
        )
        mock_urlopen.side_effect = [pending_response, completed_response]
        service = self._service()

        with patch("time.sleep"):
            result = service.search_hotels(
                destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Shinjuku Grand Hotel")
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    def test_pending_job_that_fails_returns_empty_list(self, mock_urlopen):
        pending_response = self._mock_http_response(
            {"data": {"status": "pending", "jobId": "job_1", "pollUrl": "/v1/jobs/job_1"}}
        )
        failed_response = self._mock_http_response({"data": {"status": "failed"}})
        mock_urlopen.side_effect = [pending_response, failed_response]
        service = self._service()

        with patch("time.sleep"):
            result = service.search_hotels(
                destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
            )

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
