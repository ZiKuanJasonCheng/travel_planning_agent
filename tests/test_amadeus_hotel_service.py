import unittest
from unittest.mock import MagicMock, patch


class SearchHotelsErrorTierTests(unittest.TestCase):
    def _service_with_client(self):
        from services.amadeus_hotel import AmadeusHotelService
        service = AmadeusHotelService.__new__(AmadeusHotelService)
        service.client = MagicMock()
        return service

    @patch("services.amadeus_hotel.fetch_coordinates")
    def test_response_error_returns_amadeus_hotel_error_reason(self, mock_coords):
        from amadeus import ResponseError

        class MagicMockResponse:
            status_code = 500
            result = None
            parsed = False

        mock_coords.side_effect = ResponseError(MagicMockResponse())

        service = self._service_with_client()
        result = service.search_hotels(destination="Tokyo")

        self.assertEqual(result, [{"reason": "Amadeus Hotel API error"}])

    @patch("services.amadeus_hotel.fetch_coordinates")
    def test_unexpected_error_returns_unknown_error_reason(self, mock_coords):
        mock_coords.side_effect = ValueError("boom")

        service = self._service_with_client()
        result = service.search_hotels(destination="Tokyo")

        self.assertEqual(result, [{"reason": "Unknown error"}])

    def test_no_client_still_returns_empty_list(self):
        """No credentials configured is intentional dev-mode behavior, not an error."""
        from services.amadeus_hotel import AmadeusHotelService
        service = AmadeusHotelService.__new__(AmadeusHotelService)
        service.client = None

        result = service.search_hotels(destination="Tokyo")
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
