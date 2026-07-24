import unittest

from services.duffel_flight import _apply_leg_prices, _passes_preference, _cache_key


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


if __name__ == "__main__":
    unittest.main()
