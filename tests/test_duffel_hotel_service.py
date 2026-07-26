import unittest

from services.duffel_hotel import _default_check_in_date, _default_check_out_date, _nights, _passes_hotel_filters, _cache_key


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


if __name__ == "__main__":
    unittest.main()
