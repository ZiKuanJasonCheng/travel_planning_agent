# tests/test_external_service_observe_decoration.py
from services.duffel_flight import DuffelFlightService
from services.duffel_hotel import DuffelHotelService
from services.stayingapi_hotel import StayingAPIHotelService
from services.geocoding import fetch_coordinates
from services.currency import get_rates
from services.weather_mcp_service import WeatherMCPService
from services.airline_iata_resolver import resolve_airline_iata_codes
from services.city_iata_resolver import resolve_city_iata_codes


def _is_observed(func):
    return hasattr(func, "__wrapped__")


def test_duffel_flight_search_is_observed():
    assert _is_observed(DuffelFlightService.search_flights)


def test_duffel_hotel_search_is_observed():
    assert _is_observed(DuffelHotelService.search_hotels)


def test_stayingapi_hotel_search_is_observed():
    assert _is_observed(StayingAPIHotelService.search_hotels)


def test_fetch_coordinates_is_observed():
    assert _is_observed(fetch_coordinates)


def test_get_rates_is_observed():
    assert _is_observed(get_rates)


def test_weather_get_forecast_is_observed():
    assert _is_observed(WeatherMCPService.get_forecast)


def test_resolve_airline_iata_codes_is_observed():
    assert _is_observed(resolve_airline_iata_codes)


def test_resolve_city_iata_codes_is_observed():
    assert _is_observed(resolve_city_iata_codes)
