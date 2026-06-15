import unittest


class CityIataResolverTests(unittest.TestCase):

    # ------------------------------------------------------------------
    # Already a 3-letter IATA code — pass through unchanged
    # ------------------------------------------------------------------

    def test_three_letter_uppercase_returned_as_is(self):
        from services.city_iata_resolver import resolve_city_iata
        self.assertEqual(resolve_city_iata("NRT"), "NRT")
        self.assertEqual(resolve_city_iata("HKG"), "HKG")
        self.assertEqual(resolve_city_iata("CDG"), "CDG")

    # ------------------------------------------------------------------
    # Common cities resolved via airportsdata
    # ------------------------------------------------------------------

    def test_resolves_tokyo(self):
        from services.city_iata_resolver import resolve_city_iata
        self.assertEqual(resolve_city_iata("Tokyo"), "NRT")

    def test_resolves_hong_kong(self):
        from services.city_iata_resolver import resolve_city_iata
        self.assertEqual(resolve_city_iata("Hong Kong"), "HKG")

    def test_resolves_paris(self):
        from services.city_iata_resolver import resolve_city_iata
        self.assertEqual(resolve_city_iata("Paris"), "CDG")

    def test_resolves_new_york(self):
        from services.city_iata_resolver import resolve_city_iata
        self.assertEqual(resolve_city_iata("New York"), "JFK")

    def test_resolves_dubai(self):
        from services.city_iata_resolver import resolve_city_iata
        self.assertEqual(resolve_city_iata("Dubai"), "DXB")

    def test_resolves_osaka(self):
        from services.city_iata_resolver import resolve_city_iata
        self.assertEqual(resolve_city_iata("Osaka"), "KIX")

    # ------------------------------------------------------------------
    # Cities where airport name search is needed (city field doesn't match)
    # ------------------------------------------------------------------

    def test_resolves_bali_via_airport_name(self):
        # Bali's city field in airportsdata is 'Denpasar-Bali Island', not 'Bali'
        from services.city_iata_resolver import resolve_city_iata
        self.assertEqual(resolve_city_iata("Bali"), "DPS")

    # ------------------------------------------------------------------
    # Ambiguous cities (same name in multiple countries) — static map wins
    # ------------------------------------------------------------------

    def test_resolves_london_to_heathrow(self):
        # 'London' exists in CA, US, and GB — should return LHR
        from services.city_iata_resolver import resolve_city_iata
        self.assertEqual(resolve_city_iata("London"), "LHR")

    # ------------------------------------------------------------------
    # Case-insensitive input
    # ------------------------------------------------------------------

    def test_case_insensitive_city_name(self):
        from services.city_iata_resolver import resolve_city_iata
        self.assertEqual(resolve_city_iata("osaka"), "KIX")
        self.assertEqual(resolve_city_iata("TOKYO"), "NRT")
        self.assertEqual(resolve_city_iata("hong kong"), "HKG")

    # ------------------------------------------------------------------
    # Known city resolved via airportsdata (not in static map)
    # ------------------------------------------------------------------

    def test_resolves_vladivostok(self):
        # Vladivostok is in airportsdata as VVO
        from services.city_iata_resolver import resolve_city_iata
        self.assertEqual(resolve_city_iata("Vladivostok"), "VVO")

    # ------------------------------------------------------------------
    # Truly unknown city — truncation fallback
    # ------------------------------------------------------------------

    def test_unknown_city_returns_truncated_uppercase(self):
        from services.city_iata_resolver import resolve_city_iata
        # "Zephyrville" is fictional and will never appear in airportsdata
        result = resolve_city_iata("Zephyrville")
        self.assertEqual(result, "ZEP")

    def test_short_unknown_city_truncated_safely(self):
        from services.city_iata_resolver import resolve_city_iata
        # 2-char input with no match in airportsdata — truncation returns all chars
        result = resolve_city_iata("Qq")
        self.assertEqual(result, "QQ")


    # ------------------------------------------------------------------
    # resolve_city_iata_codes — multi-code support
    # ------------------------------------------------------------------

    def test_resolve_codes_always_returns_list(self):
        from services.city_iata_resolver import resolve_city_iata_codes
        result = resolve_city_iata_codes("Tokyo")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_resolve_codes_single_airport_city(self):
        from services.city_iata_resolver import resolve_city_iata_codes
        result = resolve_city_iata_codes("Tokyo")
        self.assertIn("NRT", result)

    def test_resolve_codes_multi_airport_city_shanghai(self):
        from services.city_iata_resolver import resolve_city_iata_codes
        result = resolve_city_iata_codes("Shanghai")
        self.assertIn("PVG", result)
        self.assertIn("SHA", result)
        self.assertGreater(len(result), 1)

    def test_resolve_codes_iata_pass_through_as_single_list(self):
        from services.city_iata_resolver import resolve_city_iata_codes
        self.assertEqual(resolve_city_iata_codes("NRT"), ["NRT"])


if __name__ == "__main__":
    unittest.main()
