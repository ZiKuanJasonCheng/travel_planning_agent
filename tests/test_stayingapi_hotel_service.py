import json
import unittest
from unittest.mock import MagicMock, patch
from urllib import error as urllib_error

from services.stayingapi_hotel import (
    _default_check_in_date,
    _default_check_out_date,
    _is_timeout,
    _nights,
    _passes_hotel_filters,
    _within_preferred_area,
    _cache_key,
    _parse_search_result,
    HotelJobFailedError,
    HotelJobTimeoutError,
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
        self.assertTrue(_passes_hotel_filters(200, None))

    def test_over_budget_fails(self):
        self.assertFalse(_passes_hotel_filters(200, 150))

    def test_within_budget_passes(self):
        self.assertTrue(_passes_hotel_filters(120, 150))


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


class ParseSearchResultDistanceTests(unittest.TestCase):
    """distance_to_airport_km is what the hotel selector uses to judge a late landing."""

    def _result(self, **overrides):
        base = {
            "id": "prop_0000123",
            "name": "Shinjuku Grand Hotel",
            "location": {"city": "Tokyo", "lat": 35.6938, "lng": 139.7034},
            "price": {"nightlyPrice": 120.0, "currency": "USD"},
        }
        base.update(overrides)
        return base

    def test_no_airport_coords_leaves_distance_none(self):
        hotel = _parse_search_result(self._result())
        self.assertIsNone(hotel["distance_to_airport_km"])

    def test_computes_distance_when_airport_coords_given(self):
        # NRT sits ~60 km east of central Tokyo.
        hotel = _parse_search_result(self._result(), airport_coords=(35.7647, 140.3864))
        self.assertAlmostEqual(hotel["distance_to_airport_km"], 60.8, delta=2.0)

    def test_distance_is_rounded_to_one_decimal(self):
        hotel = _parse_search_result(self._result(), airport_coords=(35.7647, 140.3864))
        self.assertEqual(hotel["distance_to_airport_km"], round(hotel["distance_to_airport_km"], 1))

    def test_missing_hotel_coords_leaves_distance_none(self):
        # vrbo listings frequently carry no coordinates.
        result = self._result(location={"city": "Tokyo", "lat": None, "lng": None})
        hotel = _parse_search_result(result, airport_coords=(35.7647, 140.3864))
        self.assertIsNone(hotel["distance_to_airport_km"])

    def test_nonnumeric_hotel_coords_leave_distance_none(self):
        result = self._result(location={"city": "Tokyo", "lat": "n/a", "lng": "n/a"})
        hotel = _parse_search_result(result, airport_coords=(35.7647, 140.3864))
        self.assertIsNone(hotel["distance_to_airport_km"])


class WithinPreferredAreaTests(unittest.TestCase):
    """Area matching by distance, because most providers return no city at all."""

    _SHINJUKU = (35.6937632, 139.7036319)

    def _within(self, lat, lon, area="unknown area", preferred="Shinjuku", coords=None):
        return _within_preferred_area(
            lat, lon, area, preferred, self._SHINJUKU if coords is None else coords
        )

    def test_hotel_at_the_area_centre_is_within(self):
        self.assertTrue(self._within(35.6937632, 139.7036319))

    def test_hotel_three_km_away_is_within(self):
        # ~0.027 degrees of latitude is ~3 km.
        self.assertTrue(self._within(35.7208, 139.7036319))

    def test_hotel_twenty_km_away_is_outside(self):
        self.assertFalse(self._within(35.8745, 139.7036319))

    def test_five_km_is_inclusive_boundary(self):
        # 5 km north of the centre; the threshold itself must count as inside.
        self.assertTrue(self._within(35.7387292, 139.7036319))

    def test_unknown_hotel_coords_pass_rather_than_drop(self):
        # A provider without coordinates must not have every listing discarded.
        self.assertTrue(self._within(None, None))

    def test_nonnumeric_hotel_coords_pass(self):
        self.assertTrue(self._within("n/a", "n/a"))

    def test_no_preferred_area_passes(self):
        self.assertTrue(self._within(35.8745, 139.7036319, preferred=None))

    def test_falls_back_to_name_match_when_area_ungeocoded(self):
        self.assertTrue(
            _within_preferred_area(35.8745, 139.7036319, "Nishi-Shinjuku, Tokyo", "Shinjuku", None)
        )
        self.assertFalse(
            _within_preferred_area(35.8745, 139.7036319, "Ueno", "Shinjuku", None)
        )

    def test_far_hotel_naming_the_area_still_counts(self):
        # A large area extends past its centre point, so a hotel beyond the radius
        # that still names the area is kept.
        self.assertTrue(self._within(35.8745, 139.7036319, area="Shinjuku Ward"))

    def test_far_hotel_not_naming_the_area_is_dropped(self):
        self.assertFalse(self._within(35.8745, 139.7036319, area="Ueno"))

    def test_far_hotel_with_unknown_area_is_dropped(self):
        # AirBnB and Google return no city, so they can't rescue themselves by name.
        self.assertFalse(self._within(35.8745, 139.7036319, area="unknown area"))

    def test_name_match_is_case_insensitive(self):
        self.assertTrue(self._within(35.8745, 139.7036319, area="SHINJUKU"))

    def test_nearby_hotel_passes_even_when_area_names_something_else(self):
        # Distance is checked first: a hotel inside the radius is kept regardless.
        self.assertTrue(self._within(35.7000, 139.7036319, area="Ueno"))


class AreaDistanceFilteringTests(unittest.TestCase):
    """search_hotels uses the area centre when a preferred_area is given."""

    def _service(self):
        service = StayingAPIHotelService.__new__(StayingAPIHotelService)
        service.use_mock = False
        service.api_key = "test-key"
        service._cache = {}
        service._cache_ttl_seconds = 900
        return service

    def _raw(self, name, lat, lon, price=100.0, city=None):
        return {
            "id": f"id_{name}",
            "name": name,
            "location": {"city": city, "region": None, "lat": lat, "lng": lon},
            "price": {"nightlyPrice": price, "currency": "USD"},
        }

    @patch("services.stayingapi_hotel.fetch_coordinates")
    def test_keeps_only_hotels_within_the_radius(self, mock_geocode):
        mock_geocode.return_value = (35.6937632, 139.7036319)  # Shinjuku
        service = self._service()
        near = self._raw("Near Shinjuku", 35.7000, 139.7036)      # ~0.7 km
        far = self._raw("Far Away", 35.8745, 139.7036)            # ~20 km
        service._request_search = MagicMock(return_value=[near, far])

        hotels = service.search_hotels(
            destination="Tokyo", check_in_date="2026-11-17", check_out_date="2026-11-21",
            preferred_area="Shinjuku",
        )

        self.assertEqual([h["name"] for h in hotels], ["Near Shinjuku"])

    @patch("services.stayingapi_hotel.fetch_coordinates")
    def test_city_less_results_are_judged_by_distance_not_dropped(self, mock_geocode):
        # The regression this fixes: airbnb/google carry no city, so the old
        # name-match filter discarded all of them.
        mock_geocode.return_value = (35.6937632, 139.7036319)
        service = self._service()
        cityless_near = self._raw("Airbnb Near", 35.7000, 139.7036, city=None)
        cityless_far = self._raw("Airbnb Far", 35.8745, 139.7036, city=None)
        service._request_search = MagicMock(return_value=[cityless_near, cityless_far])

        hotels = service.search_hotels(
            destination="Tokyo", check_in_date="2026-11-17", check_out_date="2026-11-21",
            preferred_area="Shinjuku",
        )

        self.assertEqual([h["name"] for h in hotels], ["Airbnb Near"])

    @patch("services.stayingapi_hotel.fetch_coordinates")
    def test_ungeocodable_area_falls_back_to_name_matching(self, mock_geocode):
        # Keep a typo'd or unlisted area from silently returning nothing, and keep
        # the mock path (which has no coordinates to measure) working.
        mock_geocode.return_value = None
        service = self._service()
        matching = self._raw("Riverside Inn", 35.8745, 139.7036, city="Riverside")
        other = self._raw("Downtown", 35.8745, 139.7036, city="Uptown")
        service._request_search = MagicMock(return_value=[matching, other])

        hotels = service.search_hotels(
            destination="Tokyo", check_in_date="2026-11-17", check_out_date="2026-11-21",
            preferred_area="Riverside",
        )

        self.assertEqual([h["name"] for h in hotels], ["Riverside Inn"])

    @patch("services.stayingapi_hotel.fetch_coordinates")
    def test_no_preferred_area_skips_geocoding(self, mock_geocode):
        service = self._service()
        service._request_search = MagicMock(return_value=[self._raw("Anywhere", 35.8745, 139.7036)])

        hotels = service.search_hotels(
            destination="Tokyo", check_in_date="2026-11-17", check_out_date="2026-11-21",
        )

        mock_geocode.assert_not_called()
        self.assertEqual(len(hotels), 1)


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
    def test_pending_job_that_fails_returns_an_error_result(self, mock_urlopen):
        # A failed job must not look like "this city has no hotels" — that would hand
        # the traveler a fallback hotel instead of surfacing the problem.
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

        self.assertEqual(result, [{"reason": "StayingAPI Hotel API error"}])

    def test_poll_job_that_outlasts_the_deadline_raises_timeout(self):
        # No urlopen here: `_get_json` is stubbed so the clock can be driven directly
        # (patching time.time at module scope also hits logging's own time calls, so
        # a fixed side_effect list is fragile — use an advancing clock instead).
        service = self._service()
        service._get_json = MagicMock(return_value={"data": {"status": "running"}})

        ticks = iter(float(t) for t in range(0, 100_000, 1_000))

        with patch("time.sleep"), patch(
            "services.stayingapi_hotel.time.time", side_effect=lambda: next(ticks)
        ):
            with self.assertRaises(HotelJobTimeoutError):
                service._poll_job("/v1/jobs/job_1")

    def test_poll_job_that_fails_raises_job_failed(self):
        service = self._service()
        service._get_json = MagicMock(return_value={"data": {"status": "failed"}})

        with self.assertRaises(HotelJobFailedError):
            service._poll_job("/v1/jobs/job_1")

    @patch("urllib.request.urlopen")
    def test_pending_job_that_outlasts_the_deadline_returns_a_timeout_result(self, mock_urlopen):
        pending_response = self._mock_http_response(
            {"data": {"status": "pending", "jobId": "job_1", "pollUrl": "/v1/jobs/job_1"}}
        )
        mock_urlopen.return_value = pending_response
        service = self._service()
        service._poll_job = MagicMock(side_effect=HotelJobTimeoutError("slow"))

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )

        self.assertEqual(result, [{"reason": "StayingAPI Hotel API timeout"}])

    @patch("urllib.request.urlopen")
    def test_socket_timeout_returns_a_timeout_result(self, mock_urlopen):
        # urllib wraps a read timeout in URLError(TimeoutError) — the traveler should
        # see the same retryable timeout as a job that ran long.
        mock_urlopen.side_effect = urllib_error.URLError(TimeoutError("timed out"))
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )

        self.assertEqual(result, [{"reason": "StayingAPI Hotel API timeout"}])

    @patch("urllib.request.urlopen")
    def test_connection_error_returns_an_api_error_result(self, mock_urlopen):
        # Unreachable host: an error, but not a timeout — the traveler shouldn't be
        # told to simply retry.
        mock_urlopen.side_effect = urllib_error.URLError(ConnectionRefusedError("refused"))
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )

        self.assertEqual(result, [{"reason": "StayingAPI Hotel API error"}])

    @patch("urllib.request.urlopen")
    def test_http_error_still_returns_an_api_error_result(self, mock_urlopen):
        # HTTPError subclasses URLError, so this pins the handler ordering: it must
        # not be classified as a timeout or misfiled by the URLError clause.
        mock_urlopen.side_effect = urllib_error.HTTPError("http://x", 500, "Server Error", {}, None)
        service = self._service()

        result = service.search_hotels(
            destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
        )

        self.assertEqual(result, [{"reason": "StayingAPI Hotel API error"}])

    def test_is_timeout_classifies_urlerror_reasons(self):
        self.assertTrue(_is_timeout(urllib_error.URLError(TimeoutError("timed out"))))
        self.assertTrue(_is_timeout(urllib_error.URLError("timed out")))
        self.assertFalse(_is_timeout(urllib_error.URLError(ConnectionRefusedError("refused"))))
        self.assertFalse(_is_timeout(urllib_error.URLError("Name or service not known")))

    @patch("urllib.request.urlopen")
    def test_completed_job_with_no_results_is_a_genuine_empty_result(self, mock_urlopen):
        # The distinction the error handling exists to preserve.
        pending_response = self._mock_http_response(
            {"data": {"status": "pending", "jobId": "job_1", "pollUrl": "/v1/jobs/job_1"}}
        )
        completed_empty = self._mock_http_response(
            {"data": {"status": "completed", "result": {"results": []}}}
        )
        mock_urlopen.side_effect = [pending_response, completed_empty]
        service = self._service()

        with patch("time.sleep"):
            result = service.search_hotels(
                destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14",
            )

        self.assertEqual(result, [])

    @patch("urllib.request.urlopen")
    def test_error_results_are_not_cached(self, mock_urlopen):
        # A timeout shouldn't shadow the query for the cache TTL.
        pending_response = self._mock_http_response(
            {"data": {"status": "pending", "jobId": "job_1", "pollUrl": "/v1/jobs/job_1"}}
        )
        failed_response = self._mock_http_response({"data": {"status": "failed"}})
        mock_urlopen.side_effect = [pending_response, failed_response]
        service = self._service()
        kwargs = dict(destination="Tokyo", check_in_date="2026-09-10", check_out_date="2026-09-14")

        with patch("time.sleep"):
            service.search_hotels(**kwargs)

        self.assertEqual(service._cache, {})


if __name__ == "__main__":
    unittest.main()
