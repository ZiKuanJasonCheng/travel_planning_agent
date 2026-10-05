import unittest
from unittest.mock import MagicMock, patch

from openai import APIConnectionError, APITimeoutError, BadRequestError, RateLimitError

from services.llm_retry import call_with_retry


def _status_error(cls, status_code):
    response = MagicMock()
    response.status_code = status_code
    response.request = MagicMock()
    return cls("boom", response=response, body=None)


class CallWithRetryTests(unittest.TestCase):
    @patch("services.llm_retry.time.sleep")
    def test_transient_error_then_success_retries_once(self, mock_sleep):
        fn = MagicMock(side_effect=[APIConnectionError(request=MagicMock()), "ok"])

        result = call_with_retry(fn, model="gpt-4o-mini")

        self.assertEqual(result, "ok")
        self.assertEqual(fn.call_count, 2)
        mock_sleep.assert_called_once_with(1.0)

    @patch("services.llm_retry.time.sleep")
    def test_exhausted_attempts_raises_and_backs_off_exponentially(self, mock_sleep):
        error = RateLimitError("slow down", response=MagicMock(status_code=429), body=None)
        fn = MagicMock(side_effect=error)

        with self.assertRaises(RateLimitError):
            call_with_retry(fn)

        # 3 retries on top of the initial call
        self.assertEqual(fn.call_count, 4)
        self.assertEqual([c.args[0] for c in mock_sleep.call_args_list], [1.0, 2.0, 4.0])

    @patch("services.llm_retry.time.sleep")
    def test_server_error_is_retried(self, mock_sleep):
        from openai import APIStatusError
        fn = MagicMock(side_effect=[_status_error(APIStatusError, 503), "ok"])

        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 2)

    @patch("services.llm_retry.time.sleep")
    def test_client_error_is_not_retried(self, mock_sleep):
        fn = MagicMock(side_effect=_status_error(BadRequestError, 400))

        with self.assertRaises(BadRequestError):
            call_with_retry(fn)

        self.assertEqual(fn.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("services.llm_retry.time.sleep")
    def test_non_openai_exception_is_not_retried(self, mock_sleep):
        fn = MagicMock(side_effect=ValueError("boom"))

        with self.assertRaises(ValueError):
            call_with_retry(fn)

        self.assertEqual(fn.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("services.llm_retry.time.sleep")
    def test_timeout_is_retried(self, mock_sleep):
        fn = MagicMock(side_effect=[APITimeoutError(request=MagicMock()), "ok"])

        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 2)

    @patch("services.llm_retry.time.sleep")
    def test_overrides_respected(self, mock_sleep):
        fn = MagicMock(side_effect=APIConnectionError(request=MagicMock()))

        with self.assertRaises(APIConnectionError):
            call_with_retry(fn, max_attempts=2, base_delay=0.25)

        self.assertEqual(fn.call_count, 2)
        self.assertEqual([c.args[0] for c in mock_sleep.call_args_list], [0.25])

    @patch("services.llm_retry.time.sleep")
    def test_success_passes_args_through_and_sleeps_never(self, mock_sleep):
        fn = MagicMock(return_value="ok")

        self.assertEqual(call_with_retry(fn, 1, 2, model="m", timeout=5), "ok")
        fn.assert_called_once_with(1, 2, model="m", timeout=5)
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
