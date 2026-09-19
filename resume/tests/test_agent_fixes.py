"""
Regressions from real sessions: a turn that paused for approval with more tool
calls behind it, re-measuring after the user edited their resume, and the
superuser debug trace.
"""

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from resume.models import Resume
from resume.services import agent_loop
from resume.tests.test_agent_loop import USAGE, assistant, call, content

LLM = "resume.services.agent_loop.send_openai_tool_turn"


class ParkedTurnTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("parker", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())
        self.ctx = {
            "lang": "en", "active_resume": self.resume, "confirm_destructive": True,
            "resumes": [{"rank": 1, "id": self.resume.id, "display_name": "CV", "language": "en"}],
            "quota": {"is_pro": False},
        }

    def test_calls_after_a_paused_one_are_answered_so_the_resumed_turn_is_valid(self):
        first = assistant(tool_calls=[
            call("modify_resume", {"instruction": "shorten"}, "c_modify"),
            call("list_resumes", {}, "c_list"),
        ])
        with patch(LLM, return_value=(first, USAGE)):
            out = agent_loop.run_turn(self.user, self.ctx, [], "shorten it and list them")
        self.assertEqual(out["status"], "needs_approval")

        parked = agent_loop.take_pending(self.user, out["token"])
        with patch(LLM, return_value=(assistant("Done."), USAGE)) as llm, \
             patch("resume.services.agent_service.send_openai_message",
                   return_value=json.dumps({"modified_resume": content(), "changes_summary": "x"})):
            resumed = agent_loop.resume_turn(self.user, self.ctx, parked, approved=True)
        self.assertEqual(resumed["status"], "done")

        messages = llm.call_args.args[0]
        answered = {m["tool_call_id"] for m in messages if m.get("role") == "tool"}
        self.assertEqual(answered, {"c_modify", "c_list"})
        deferred = next(m for m in messages if m.get("tool_call_id") == "c_list")
        self.assertIn("not_run", deferred["content"])


class DebugTraceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tracer", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())

    def ctx(self, debug):
        return {"lang": "en", "active_resume": self.resume, "quota": {},
                "resumes": [{"rank": 1, "id": self.resume.id, "display_name": "CV", "language": "en"}],
                "debug": debug}

    def test_a_superuser_turn_carries_its_trace_including_the_llm_error(self):
        with patch(LLM, return_value=(None, "OpenAI API returned an API Error: 400 tool_calls")):
            out = agent_loop.run_turn(self.user, self.ctx([]), [], "hi")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["debug"][0]["kind"], "llm_turn")
        self.assertIn("400 tool_calls", out["debug"][0]["error"])

    def test_other_users_get_no_trace(self):
        with patch(LLM, return_value=(assistant("Hello"), USAGE)):
            out = agent_loop.run_turn(self.user, self.ctx(None), [], "hi")
        self.assertIsNone(out["debug"])

    def test_only_superusers_are_traced_by_the_view(self):
        from django.test import RequestFactory

        from resume.views import _agent_context, _agent_response

        request = RequestFactory().post("/")
        request.user = self.user
        self.assertIsNone(_agent_context(request, self.resume)["debug"])
        self.user.is_superuser = True
        self.assertEqual(_agent_context(request, self.resume)["debug"], [])
        payload = _agent_response({"status": "done", "message": "", "effects": [],
                                   "debug": [{"kind": "llm_turn"}]}, None, "hi")
        self.assertEqual(payload["debug"], [{"kind": "llm_turn"}])
