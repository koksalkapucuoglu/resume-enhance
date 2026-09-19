"""
The TypeSafe wrapper: typed answers out, never an exception, never content in
the logs. The SDK client is always faked — tests make no API calls.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from resume import typesafe_engine
from resume.typesafe_engine import Choice, Noul, Score
from typesafe_sdk import TypeSafeError

SECRET = "Ada Lovelace, ada@example.com"


def _response(answers, model="jev-1.13.0"):
    return SimpleNamespace(
        model=model,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        answers=answers,
    )


def _noul(p):
    return SimpleNamespace(type="noul", noul=p)


def _choice(choice, probabilities):
    return SimpleNamespace(
        type="choice", choice=choice, confidence=0.9, probabilities=probabilities
    )


def _score(score, probabilities):
    return SimpleNamespace(
        type="score",
        score=score,
        confidence=0.8,
        legend={i: f"level {i}" for i in range(len(probabilities))},
        probabilities=probabilities,
    )


@override_settings(TYPESAFE_API_KEY="test-key")
class AskTests(SimpleTestCase):
    def setUp(self):
        self.client = MagicMock()
        patcher = patch.object(typesafe_engine, "_get_client", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_answers_are_typed_by_kind(self):
        self.client.system_one.return_value = _response(
            {
                "yes": _noul(0.92),
                "pick": _choice("b", {"a": 0.1, "b": 0.9}),
                "level": _score(1.5, {0: 0.0, 1: 0.5, 2: 0.5}),
            }
        )
        answers = typesafe_engine.ask(
            SECRET,
            {
                "yes": Noul(instructions="?"),
                "pick": Choice(instructions="?", criteria={"a": None, "b": None}),
                "level": Score(instructions="?", criteria=["x", "y", "z"]),
            },
            purpose="test",
        )
        self.assertEqual(answers.nouls["yes"], 0.92)
        self.assertEqual(answers.choices["pick"].choice, "b")
        self.assertEqual(answers.scores["level"].fraction, 0.75)
        self.assertEqual(answers.model, "jev-1.13.0")

    def test_a_large_batch_is_split_and_merged(self):
        count = typesafe_engine.MAX_QUESTIONS_PER_REQUEST + 5
        questions = {f"q{i}": Noul(instructions="?") for i in range(count)}
        self.client.system_one.side_effect = lambda state, questions: _response(
            {key: _noul(0.5) for key in questions}
        )
        answers = typesafe_engine.ask("state", questions, purpose="test")
        self.assertEqual(self.client.system_one.call_count, 2)
        self.assertEqual(len(answers.nouls), count)

    def test_no_questions_makes_no_call(self):
        answers = typesafe_engine.ask("state", {}, purpose="test")
        self.assertEqual(answers.nouls, {})
        self.client.system_one.assert_not_called()

    def test_an_api_error_returns_none_and_logs_no_content(self):
        self.client.system_one.side_effect = TypeSafeError(SECRET)
        with self.assertLogs("resume.typesafe_engine", level="WARNING") as captured:
            answers = typesafe_engine.ask(
                SECRET, {"q": Noul(instructions=SECRET)}, purpose="test"
            )
        self.assertIsNone(answers)
        logged = "\n".join(captured.output)
        self.assertIn("TypeSafeError", logged)
        self.assertNotIn("Ada", logged)

    def test_an_unexpected_failure_returns_none_and_logs_no_content(self):
        self.client.system_one.side_effect = KeyError(SECRET)
        with self.assertLogs("resume.typesafe_engine", level="WARNING") as captured:
            answers = typesafe_engine.ask(
                SECRET, {"q": Noul(instructions="?")}, purpose="test"
            )
        self.assertIsNone(answers)
        self.assertNotIn("Ada", "\n".join(captured.output))

    def test_success_log_holds_sizes_not_content(self):
        self.client.system_one.return_value = _response({"q": _noul(0.1)})
        with self.assertLogs("resume.typesafe_engine", level="INFO") as captured:
            typesafe_engine.ask(SECRET, {"q": Noul(instructions=SECRET)}, purpose="test")
        logged = "\n".join(captured.output)
        self.assertIn("1 questions", logged)
        self.assertNotIn("Ada", logged)


class UnconfiguredTests(SimpleTestCase):
    @override_settings(TYPESAFE_API_KEY="")
    def test_without_a_key_nothing_is_called(self):
        with patch.object(typesafe_engine, "_get_client") as get_client:
            answers = typesafe_engine.ask("s", {"q": Noul(instructions="?")}, purpose="t")
        self.assertIsNone(answers)
        get_client.assert_not_called()


class SdkLoggingTests(SimpleTestCase):
    def test_sdk_body_logging_is_held_above_debug(self):
        # The SDK logs request and response bodies at DEBUG.
        self.assertGreaterEqual(
            logging.getLogger("typesafe_sdk").getEffectiveLevel(), logging.WARNING
        )


class TestRunKeyTests(SimpleTestCase):
    def test_the_test_run_has_no_real_key(self):
        from django.conf import settings

        # .env is loaded for tests too; an unmocked call must not reach the API.
        self.assertEqual(settings.TYPESAFE_API_KEY, "")
