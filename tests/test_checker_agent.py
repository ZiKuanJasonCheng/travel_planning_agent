import json
import unittest
from unittest.mock import MagicMock, patch


class LLMCheckerServiceTests(unittest.TestCase):

    def _mock_openai_response(self, passed, issues, critique):
        args = json.dumps({"passed": passed, "issues": issues, "critique": critique})
        tool_call = MagicMock()
        tool_call.id = "call_evaluate"
        tool_call.function.name = "evaluate_itinerary"
        tool_call.function.arguments = args
        message = MagicMock()
        message.tool_calls = [tool_call]
        choice = MagicMock()
        choice.message = message
        response = MagicMock()
        response.choices = [choice]
        return response

    @patch("services.llm_checker_service.OpenAI")
    def test_returns_passed_true_when_llm_approves(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = self._mock_openai_response(
            passed=True, issues=[], critique=""
        )

        from services.llm_checker_service import evaluate_itinerary
        result = evaluate_itinerary(
            destination="Tokyo",
            days=3,
            num_people=2,
            itinerary=[{"day": 1, "activities": [{"name": "Senso-ji"}]}],
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["issues"], [])

    @patch("services.llm_checker_service.OpenAI")
    def test_returns_issues_when_llm_rejects(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = self._mock_openai_response(
            passed=False,
            issues=["Senso-ji appears on Day 1 and Day 3"],
            critique="Remove the duplicate Senso-ji visit on Day 3.",
        )

        from services.llm_checker_service import evaluate_itinerary
        result = evaluate_itinerary(
            destination="Tokyo",
            days=3,
            num_people=2,
            itinerary=[],
        )

        self.assertFalse(result["passed"])
        self.assertIn("Senso-ji appears on Day 1 and Day 3", result["issues"])
        self.assertNotEqual(result["critique"], "")

    @patch("services.llm_checker_service.OpenAI")
    def test_prior_critique_included_in_user_message(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = self._mock_openai_response(
            passed=True, issues=[], critique=""
        )

        from services.llm_checker_service import evaluate_itinerary
        evaluate_itinerary(
            destination="Kyoto",
            days=2,
            num_people=1,
            itinerary=[],
            prior_critique="Fix the duplicate Fushimi Inari visit.",
        )

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_message = next(m for m in messages if m["role"] == "user")
        self.assertIn("Fix the duplicate Fushimi Inari visit.", user_message["content"])


def _tool_call(name, arguments, call_id="call-1"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = arguments
    tc.model_dump.return_value = {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }
    return tc


def _response_with_tool_calls(*tool_calls):
    message = MagicMock()
    message.tool_calls = list(tool_calls)
    message.content = None
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


_CONVERT_CALL = _tool_call(
    "convert_currency",
    json.dumps({"amount": 20000, "from_currency": "JPY", "to_currency": "USD"}),
)


class CheckerConvertCurrencyToolLoopTests(unittest.TestCase):
    """The checker may call convert_currency before returning a verdict."""

    def setUp(self):
        patcher = patch("services.currency.get_rates", return_value={"USD": 1.0, "JPY": 200.0})
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("services.llm_checker_service.OpenAI")
    def test_conversion_then_verdict_resolves_to_verdict(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = [
            _response_with_tool_calls(_CONVERT_CALL),
            _response_with_tool_calls(_tool_call(
                "evaluate_itinerary",
                json.dumps({"passed": False, "issues": ["Over budget"], "critique": "Trim Day 2"}),
            )),
        ]

        from services.llm_checker_service import evaluate_itinerary
        result = evaluate_itinerary(destination="Tokyo", days=3, num_people=2, itinerary=[])

        self.assertFalse(result["passed"])
        self.assertEqual(result["issues"], ["Over budget"])
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)

    @patch("services.llm_checker_service.OpenAI")
    def test_iteration_cap_forces_the_terminal_tool(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        # The model keeps converting and never returns a verdict on its own.
        mock_client.chat.completions.create.side_effect = [
            _response_with_tool_calls(_CONVERT_CALL),
            _response_with_tool_calls(_CONVERT_CALL),
            _response_with_tool_calls(_tool_call(
                "evaluate_itinerary",
                json.dumps({"passed": True, "issues": [], "critique": ""}),
            )),
        ]

        from services.llm_checker_service import evaluate_itinerary
        result = evaluate_itinerary(destination="Tokyo", days=3, num_people=2, itinerary=[])

        self.assertTrue(result["passed"])
        last_kwargs = mock_client.chat.completions.create.call_args_list[-1].kwargs
        self.assertEqual(
            last_kwargs["tool_choice"],
            {"type": "function", "function": {"name": "evaluate_itinerary"}},
        )

    @patch("services.llm_checker_service.OpenAI")
    def test_raises_when_no_verdict_ever_arrives(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _response_with_tool_calls(_CONVERT_CALL)

        from services.llm_checker_service import evaluate_itinerary
        with self.assertRaises(ValueError):
            evaluate_itinerary(destination="Tokyo", days=3, num_people=2, itinerary=[])


def _base_state(retry_count=0, critique=None, dirty_agents=None):
    return {
        "destination": "Tokyo",
        "days": 3,
        "num_people": 2,
        "itinerary": [{"day": 1, "activities": [{"name": "Senso-ji"}]}],
        "checker_retry_count": retry_count,
        "checker_critique": critique,
        "dirty_agents": dirty_agents or [],
    }


class CheckerAgentTests(unittest.TestCase):

    @patch("agents.checker.evaluate_itinerary")
    def test_passes_good_itinerary(self, mock_eval):
        mock_eval.return_value = {"passed": True, "issues": [], "critique": ""}
        from agents.checker import checker_agent
        result = checker_agent(_base_state())
        self.assertEqual(result["checker_retry_count"], 0)
        self.assertIsNone(result["checker_critique"])
        self.assertNotIn("attraction_agent", result.get("dirty_agents", []))

    @patch("agents.checker.evaluate_itinerary")
    def test_first_retry_on_failure(self, mock_eval):
        mock_eval.return_value = {
            "passed": False,
            "issues": ["Senso-ji appears twice"],
            "critique": "Remove duplicate Senso-ji on Day 3.",
        }
        from agents.checker import checker_agent
        result = checker_agent(_base_state(retry_count=0))
        self.assertEqual(result["checker_retry_count"], 1)
        self.assertEqual(result["checker_critique"], "Remove duplicate Senso-ji on Day 3.")
        self.assertIn("attraction_agent", result["dirty_agents"])

    @patch("agents.checker.evaluate_itinerary")
    def test_second_retry_on_failure(self, mock_eval):
        mock_eval.return_value = {
            "passed": False,
            "issues": ["Still duplicate"],
            "critique": "Still needs fixing",
        }
        from agents.checker import checker_agent
        result = checker_agent(_base_state(retry_count=1, critique="Remove duplicate Senso-ji on Day 3."))
        self.assertEqual(result["checker_retry_count"], 2)
        self.assertEqual(result["checker_critique"], "Still needs fixing")
        self.assertIn("attraction_agent", result["dirty_agents"])

    @patch("agents.checker.evaluate_itinerary")
    def test_exhausted_retries_flags_for_user(self, mock_eval):
        mock_eval.return_value = {
            "passed": False,
            "issues": ["Issue A", "Issue B"],
            "critique": "Still broken",
        }
        from agents.checker import checker_agent
        result = checker_agent(_base_state(retry_count=2))
        self.assertEqual(result["checker_retry_count"], 0)
        self.assertNotIn("attraction_agent", result.get("dirty_agents", []))
        self.assertIn("Issue A", result["checker_critique"])
        self.assertIn("Issue B", result["checker_critique"])

    @patch("agents.checker.evaluate_itinerary", side_effect=Exception("timeout"))
    def test_llm_failure_treated_as_pass(self, mock_eval):
        from agents.checker import checker_agent
        result = checker_agent(_base_state())
        self.assertEqual(result["checker_retry_count"], 0)
        self.assertIsNone(result["checker_critique"])
        self.assertNotIn("attraction_agent", result.get("dirty_agents", []))


class AttractionAgentCheckerIntegrationTests(unittest.TestCase):

    def _full_state(self, critique=None):
        return {
            "destination": "Tokyo",
            "origin": "Hong Kong",
            "days": 3,
            "num_people": 2,
            "start_date": "2025-08-01",
            "constraints": {},
            "transport_options": {
                "railway": [],
                "flight": {
                    "outbound": [{"airline": "CX", "arrival_time": "10:00:00"}],
                    "inbound": [{"airline": "CX", "depart_time": "18:00:00"}],
                },
            },
            "accommodation_options": [{"area": "Shinjuku"}],
            "itinerary": None,
            "checker_retry_count": 0,
            "checker_critique": critique,
            "dirty_agents": [],
            "log_trace": False,
            "traces": [],
            "status": "planning",
        }

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_checker_agent_appended_to_dirty_agents(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.generate_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        result = attraction_agent(self._full_state())

        self.assertIn("checker_agent", result["dirty_agents"])

    @patch("agents.attraction.get_llm_itinerary_service")
    def test_critique_passed_to_llm_service(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.generate_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        attraction_agent(self._full_state(critique="Fix duplicate Senso-ji."))

        call_kwargs = mock_svc.generate_itinerary.call_args.kwargs
        self.assertEqual(call_kwargs.get("critique"), "Fix duplicate Senso-ji.")
