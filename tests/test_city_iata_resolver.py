"""Tests for services/city_iata_resolver.py."""
import unittest
from unittest.mock import MagicMock, patch

import services.city_iata_resolver as _resolver_module

# Minimal but realistic airport fixture used in setUp.
# Every airport exercised by this test file must be listed here.
# Fields match the OurAirports-sourced dict produced by _get_airports().
_AIRPORTS_FIXTURE: dict = {
    "NRT": {"iata": "NRT", "name": "Narita International Airport",
            "city": "Tokyo", "lat": 35.7647, "lon": 140.3864,
            "type": "large_airport", "scheduled_service": "yes"},
    "HND": {"iata": "HND", "name": "Tokyo Haneda Airport",
            "city": "Tokyo", "lat": 35.5533, "lon": 139.7811,
            "type": "large_airport", "scheduled_service": "yes"},
    "HKG": {"iata": "HKG", "name": "Hong Kong International Airport",
            "city": "Hong Kong", "lat": 22.3089, "lon": 113.9145,
            "type": "large_airport", "scheduled_service": "yes"},
    "CDG": {"iata": "CDG", "name": "Charles de Gaulle International Airport",
            "city": "Paris", "lat": 49.0097, "lon": 2.5478,
            "type": "large_airport", "scheduled_service": "yes"},
    "ORY": {"iata": "ORY", "name": "Paris Orly Airport",
            "city": "Paris", "lat": 48.7233, "lon": 2.3794,
            "type": "large_airport", "scheduled_service": "yes"},
    "JFK": {"iata": "JFK", "name": "John F Kennedy International Airport",
            "city": "New York", "lat": 40.6398, "lon": -73.7789,
            "type": "large_airport", "scheduled_service": "yes"},
    "EWR": {"iata": "EWR", "name": "Newark Liberty International Airport",
            "city": "Newark", "lat": 40.6925, "lon": -74.1687,
            "type": "large_airport", "scheduled_service": "yes"},
    "DXB": {"iata": "DXB", "name": "Dubai International Airport",
            "city": "Dubai", "lat": 25.2528, "lon": 55.3644,
            "type": "large_airport", "scheduled_service": "yes"},
    "VVO": {"iata": "VVO", "name": "Vladivostok International Airport",
            "city": "Vladivostok", "lat": 43.3989, "lon": 132.1478,
            "type": "large_airport", "scheduled_service": "yes"},
    "PVG": {"iata": "PVG", "name": "Shanghai Pudong International Airport",
            "city": "Shanghai", "lat": 31.1434, "lon": 121.8052,
            "type": "large_airport", "scheduled_service": "yes"},
    "SHA": {"iata": "SHA", "name": "Shanghai Hongqiao International Airport",
            "city": "Shanghai", "lat": 31.1979, "lon": 121.3364,
            "type": "large_airport", "scheduled_service": "yes"},
    "LAX": {"iata": "LAX", "name": "Los Angeles International Airport",
            "city": "Los Angeles", "lat": 33.9425, "lon": -118.408,
            "type": "large_airport", "scheduled_service": "yes"},
}


class CityIataResolverTests(unittest.TestCase):

    def setUp(self):
        # Inject fixture data so _get_airports() returns it without a network call.
        _resolver_module._airports = _AIRPORTS_FIXTURE.copy()

    def tearDown(self):
        _resolver_module._airports = None

    # ------------------------------------------------------------------
    # Already a 3-letter IATA code — pass through unchanged
    # ------------------------------------------------------------------

    def test_three_letter_uppercase_returned_as_is(self):
        from services.city_iata_resolver import resolve_city_iata
        self.assertEqual(resolve_city_iata("NRT"), "NRT")
        self.assertEqual(resolve_city_iata("HKG"), "HKG")
        self.assertEqual(resolve_city_iata("CDG"), "CDG")

    # ------------------------------------------------------------------
    # Common cities resolved via OurAirports
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
    # Cities handled by the static map
    # ------------------------------------------------------------------

    def test_resolves_bali_via_static_map(self):
        # Bali's municipality in OurAirports is "Denpasar", not "Bali" — static map wins
        from services.city_iata_resolver import resolve_city_iata
        self.assertEqual(resolve_city_iata("Bali"), "DPS")

    def test_resolves_london_to_heathrow(self):
        # "London" is in many countries; static map pins it to LHR
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
    # Known city resolved via OurAirports (not in static map)
    # ------------------------------------------------------------------

    def test_resolves_vladivostok(self):
        from services.city_iata_resolver import resolve_city_iata
        self.assertEqual(resolve_city_iata("Vladivostok"), "VVO")

    # ------------------------------------------------------------------
    # Truly unknown city — truncation fallback
    # ------------------------------------------------------------------

    def test_unknown_city_returns_truncated_uppercase(self):
        from services.city_iata_resolver import resolve_city_iata
        result = resolve_city_iata("Zephyrville")
        self.assertEqual(result, "ZEP")

    def test_short_unknown_city_truncated_safely(self):
        from services.city_iata_resolver import resolve_city_iata
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

    # ------------------------------------------------------------------
    # Geocoding fallback — _geocode_and_find_nearest
    # ------------------------------------------------------------------

    @patch("services.city_iata_resolver._get_airports")
    @patch("services.city_iata_resolver.fetch_coordinates")
    def test_geocode_and_find_nearest_returns_nearest_international(
        self, mock_geocode, mock_airports
    ):
        mock_geocode.return_value = (35.0, 135.0)
        mock_airports.return_value = {
            "AAA": {"iata": "AAA", "name": "Some Regional Airport",
                    "lat": 35.1, "lon": 135.1,
                    "type": "medium_airport", "scheduled_service": "yes"},
            "BBB": {"iata": "BBB", "name": "Nearby International Airport",
                    "lat": 35.3, "lon": 135.3,
                    "type": "large_airport", "scheduled_service": "yes"},
        }
        from services.city_iata_resolver import _geocode_and_find_nearest
        result = _geocode_and_find_nearest("SomeCity")
        # AAA is closer but lacks "International" — BBB wins on name preference
        self.assertEqual(result, ["BBB"])

    @patch("services.city_iata_resolver._get_airports")
    @patch("services.city_iata_resolver.fetch_coordinates")
    def test_geocode_and_find_nearest_falls_back_to_closest_when_no_international(
        self, mock_geocode, mock_airports
    ):
        mock_geocode.return_value = (35.0, 135.0)
        mock_airports.return_value = {
            "AAA": {"iata": "AAA", "name": "North Regional Airport",
                    "lat": 35.1, "lon": 135.1,
                    "type": "medium_airport", "scheduled_service": "yes"},
            "BBB": {"iata": "BBB", "name": "South Regional Airport",
                    "lat": 35.5, "lon": 135.5,
                    "type": "medium_airport", "scheduled_service": "yes"},
        }
        from services.city_iata_resolver import _geocode_and_find_nearest
        result = _geocode_and_find_nearest("SomeCity")
        # No "International" airports — returns geographically nearest
        self.assertEqual(result, ["AAA"])

    @patch("services.city_iata_resolver.fetch_coordinates")
    def test_geocode_and_find_nearest_returns_empty_when_geocoding_fails(
        self, mock_geocode
    ):
        mock_geocode.return_value = None
        from services.city_iata_resolver import _geocode_and_find_nearest
        result = _geocode_and_find_nearest("NowhereCity")
        self.assertEqual(result, [])

    @patch("services.city_iata_resolver._get_airports")
    @patch("services.city_iata_resolver.fetch_coordinates")
    def test_geocode_and_find_nearest_returns_empty_when_no_airport_in_radius(
        self, mock_geocode, mock_airports
    ):
        mock_geocode.return_value = (0.0, 0.0)  # mid-Atlantic
        mock_airports.return_value = {
            "LAX": {"iata": "LAX", "name": "Los Angeles International Airport",
                    "lat": 33.94, "lon": -118.40,
                    "type": "large_airport", "scheduled_service": "yes"},
        }
        from services.city_iata_resolver import _geocode_and_find_nearest
        result = _geocode_and_find_nearest("AtlanticCity")
        self.assertEqual(result, [])

    @patch("services.city_iata_resolver._geocode_and_find_nearest")
    def test_resolve_codes_uses_geocoding_when_lookup_fails(self, mock_nearest):
        mock_nearest.return_value = ["MFM"]
        from services.city_iata_resolver import resolve_city_iata_codes
        result = resolve_city_iata_codes("Zephyrville")
        self.assertEqual(result, ["MFM"])
        mock_nearest.assert_called_once_with("Zephyrville")

    @patch("services.city_iata_resolver._geocode_and_find_nearest")
    def test_resolve_codes_skips_geocoding_when_lookup_succeeds(self, mock_nearest):
        from services.city_iata_resolver import resolve_city_iata_codes
        resolve_city_iata_codes("Tokyo")
        mock_nearest.assert_not_called()

    # ------------------------------------------------------------------
    # Commercial filter — non-commercial airports excluded at load time
    # ------------------------------------------------------------------

    @patch("urllib.request.urlopen")
    def test_get_airports_excludes_non_commercial(self, mock_urlopen):
        csv_bytes = "\n".join([
            "ident,iata_code,type,name,latitude_deg,longitude_deg,iso_country,municipality,scheduled_service",
            "ZSSZ,SZV,medium_airport,Guangfu Airport,31.26,120.40,CN,Suzhou,no",
            "ZSPD,PVG,large_airport,Shanghai Pudong International Airport,31.14,121.80,CN,Shanghai,yes",
            "ZSSS,SHA,large_airport,Shanghai Hongqiao International Airport,31.20,121.34,CN,Shanghai,yes",
        ]).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = csv_bytes
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        _resolver_module._airports = None  # bypass setUp's pre-seeding to force a real fetch
        from services.city_iata_resolver import _get_airports
        result = _get_airports()

        self.assertNotIn("SZV", result)   # military/non-commercial → excluded
        self.assertIn("PVG", result)
        self.assertIn("SHA", result)

    @patch("services.city_iata_resolver._geocode_and_find_nearest")
    def test_resolve_codes_geocodes_when_city_has_no_commercial_airport(self, mock_nearest):
        # setUp seeds _airports without any Suzhou entry (SZV is non-commercial and would
        # be filtered by _get_airports() in production)
        mock_nearest.return_value = ["PVG"]
        from services.city_iata_resolver import resolve_city_iata_codes
        result = resolve_city_iata_codes("Suzhou")
        self.assertEqual(result, ["PVG"])
        mock_nearest.assert_called_once_with("Suzhou")


if __name__ == "__main__":
    unittest.main()
