import unittest

from services.duffel_hotel import _default_check_in_date, _default_check_out_date, _nights, _passes_hotel_filters, _cache_key, _parse_search_result


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


if __name__ == "__main__":
    unittest.main()
