"""
The tool-call guardrail: Jev's verdict decides whether a proposed call runs,
asks, or is sent back to the model. Jev and the chat model are both mocked.
"""

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from resume.models import Resume
from resume.services import agent_guard, agent_loop, agent_tools
from resume.tests.test_agent_loop import USAGE, assistant, call, content
from resume.typesafe_engine import Answers, ChoiceResult

GUARD_ASK = "resume.services.agent_guard.typesafe_engine.ask"
LLM = "resume.services.agent_loop.send_openai_tool_turn"


def jev(asked=0.95, args=0.95, target=None, confidence=0.95):
    choices = {}
    if target is not None:
        choices["target"] = ChoiceResult(choice=target, confidence=confidence, probabilities={})
    return Answers(nouls={"asked": asked, "args": args}, choices=choices, model="jev-test")


class GuardTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("guarded", password="x")
        self.main = Resume.objects.create(user=self.user, title="Main", content=content())
        self.alt = Resume.objects.create(user=self.user, title="Alternative", content=content())
        self.ctx = {
            "lang": "en",
            "active_resume": self.main,
            "resumes": [
                {"rank": 1, "id": self.main.id, "display_name": "Main", "language": "en"},
                {"rank": 2, "id": self.alt.id, "display_name": "Alternative", "language": "en"},
            ],
            "quota": {"is_pro": False},
            "confirm_destructive": True,
        }

    def check(self, tool, arguments, answers):
        messages = [{"role": "user", "content": "please do it"}]
        with patch(GUARD_ASK, return_value=answers) as ask:
            verdict = agent_guard.check(
                self.user, self.ctx, messages, agent_tools.get_tool(tool), arguments
            )
        return verdict, ask


class VerdictTests(GuardTestBase):
    def test_read_only_tools_are_not_checked(self):
        verdict, ask = self.check("list_resumes", {}, jev(asked=0.0))
        self.assertEqual(verdict.action, "allow")
        ask.assert_not_called()

    def test_without_jev_everything_is_allowed(self):
        verdict, _ = self.check("delete_resume", {}, None)
        self.assertEqual(verdict.action, "allow")
        self.assertEqual(verdict.target_name, "Main (en)")

    def test_an_action_nobody_asked_for_is_blocked(self):
        verdict, _ = self.check("delete_resume", {}, jev(asked=0.05))
        self.assertEqual(verdict.action, "block")
        self.assertEqual(verdict.tool_message()["error"], "guardrail_blocked")

    def test_contradicting_arguments_are_blocked(self):
        verdict, _ = self.check("switch_template", {"template": "ivy-serif"}, jev(args=0.1))
        self.assertEqual(verdict.action, "block")

    def test_doubt_is_a_warning(self):
        verdict, _ = self.check("modify_resume", {"instruction": "x"}, jev(asked=0.35))
        self.assertEqual(verdict.action, "warn")

    def test_a_confident_other_target_is_blocked_with_the_id_they_meant(self):
        verdict, _ = self.check(
            "switch_template", {"template": "ivy-serif"}, jev(target=f"resume_{self.alt.id}")
        )
        self.assertEqual(verdict.action, "block")
        self.assertEqual(verdict.tool_message()["user_seems_to_mean_resume_id"], self.alt.id)

    def test_an_unsure_target_pick_does_not_block(self):
        verdict, _ = self.check(
            "switch_template", {"template": "ivy-serif"},
            jev(target=f"resume_{self.alt.id}", confidence=0.5),
        )
        self.assertEqual(verdict.action, "allow")

    def test_the_target_question_names_resumes_not_ids_in_the_state(self):
        _, ask = self.check("delete_resume", {"resume_id": self.alt.id}, jev())
        state, questions = ask.call_args.args
        self.assertEqual(state["proposed_action"]["arguments"]["resume"], "Alternative (en)")
        self.assertIn(f"resume_{self.alt.id}", questions["target"].criteria)

    def test_another_users_resume_id_does_not_resolve(self):
        stranger = User.objects.create_user("stranger", password="x")
        theirs = Resume.objects.create(user=stranger, title="Secret title", content=content())
        _, ask = self.check("delete_resume", {"resume_id": theirs.id}, jev())
        state = ask.call_args.args[0]
        self.assertNotIn("Secret title", json.dumps(state))


class LoopIntegrationTests(GuardTestBase):
    def run_turn(self, tool, arguments, answers, then="OK."):
        turns = [
            (assistant(tool_calls=[call(tool, arguments)]), USAGE),
            (assistant(then), USAGE),
        ]
        with patch(LLM, side_effect=turns) as llm, patch(GUARD_ASK, return_value=answers):
            out = agent_loop.run_turn(self.user, self.ctx, [], "do something")
        return out, llm

    def test_a_blocked_call_does_not_run_and_the_model_is_told(self):
        out, llm = self.run_turn("delete_resume", {"resume_id": self.main.id}, jev(asked=0.02),
                                 then="Did you mean to delete it?")
        self.assertEqual(out["status"], "done")
        self.assertTrue(Resume.objects.filter(pk=self.main.pk).exists())
        tool_messages = [m for m in llm.call_args_list[1].args[0] if m.get("role") == "tool"]
        self.assertIn("guardrail_blocked", tool_messages[0]["content"])

    def test_the_approval_card_names_the_resume(self):
        out, _ = self.run_turn("delete_resume", {"resume_id": self.alt.id}, jev())
        self.assertEqual(out["status"], "needs_approval")
        self.assertEqual(out["copy"]["target"], "Resume: Alternative (en)")
        self.assertNotIn("warning", out["copy"])

    def test_a_doubtful_destructive_call_asks_even_with_confirmations_off(self):
        self.ctx["confirm_destructive"] = False
        out, _ = self.run_turn("modify_resume", {"instruction": "shorten"}, jev(asked=0.3))
        self.assertEqual(out["status"], "needs_approval")
        self.assertIn("warning", out["copy"])

    def test_a_confident_call_runs_straight_through_with_confirmations_off(self):
        self.ctx["confirm_destructive"] = False
        out, _ = self.run_turn("delete_resume", {"resume_id": self.alt.id}, jev())
        self.assertEqual(out["status"], "done")
        self.assertFalse(Resume.objects.filter(pk=self.alt.pk).exists())

    def test_an_approved_call_is_not_checked_again(self):
        out, _ = self.run_turn("delete_resume", {"resume_id": self.alt.id}, jev())
        parked = agent_loop.take_pending(self.user, out["token"])
        with patch(LLM, return_value=(assistant("Deleted."), USAGE)), \
             patch(GUARD_ASK) as ask:
            resumed = agent_loop.resume_turn(self.user, self.ctx, parked, approved=True)
        self.assertEqual(resumed["status"], "done")
        ask.assert_not_called()
        self.assertFalse(Resume.objects.filter(pk=self.alt.pk).exists())
