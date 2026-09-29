import json
import unittest
from unittest.mock import patch

from services import currency


_FAKE_RATES = {"USD": 1.0, "EUR": 0.5, "JPY": 100.0}


class ConvertTests(unittest.TestCase):

    def setUp(self):
        patcher = patch.object(currency, "get_rates", return_value=dict(_FAKE_RATES))
        self.mock_get_rates = patcher.start()
        self.addCleanup(patcher.stop)

    def test_converts_between_currencies(self):
        # 100 JPY at 100 per USD is 1 USD, which is 0.5 EUR.
        self.assertEqual(currency.convert(100, "JPY", "EUR"), 0.5)

    def test_round_trips_to_original_amount(self):
        there = currency.convert(250, "USD", "JPY")
        back = currency.convert(there, "JPY", "USD")
        self.assertAlmostEqual(back, 250.0)

    def test_same_currency_passes_through(self):
        self.assertEqual(currency.convert(42.5, "EUR", "EUR"), 42.5)
        # Case-insensitive: still the same currency after upper().
        self.assertEqual(currency.convert(42.5, "eur", "EUR"), 42.5)
        self.mock_get_rates.assert_not_called()

    def test_zero_amount_is_zero(self):
        self.assertEqual(currency.convert(0, "JPY", "EUR"), 0.0)

    def test_unknown_currency_treated_as_usd(self):
        # Unknown source behaves as USD → EUR
        self.assertEqual(currency.convert(10, "XYZ", "EUR"), 5.0)
        # Unknown target behaves as USD
        self.assertEqual(currency.convert(10, "EUR", "XYZ"), 20.0)


class ExecuteToolCallTests(unittest.TestCase):

    def setUp(self):
        patcher = patch.object(currency, "get_rates", return_value=dict(_FAKE_RATES))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_executes_convert_currency(self):
        result = json.loads(currency.execute_tool_call(
            "convert_currency",
            json.dumps({"amount": 100, "from_currency": "jpy", "to_currency": "eur"}),
        ))
        self.assertEqual(result["result"], 0.5)
        self.assertEqual(result["from_currency"], "JPY")
        self.assertEqual(result["to_currency"], "EUR")

    def test_unknown_tool_returns_error(self):
        result = json.loads(currency.execute_tool_call("something_else", "{}"))
        self.assertIn("error", result)

    def test_invalid_json_returns_error(self):
        result = json.loads(currency.execute_tool_call("convert_currency", "not json"))
        self.assertIn("error", result)

    def test_empty_arguments_default_to_zero_usd(self):
        result = json.loads(currency.execute_tool_call("convert_currency", ""))
        self.assertEqual(result["amount"], 0)


if __name__ == "__main__":
    unittest.main()
