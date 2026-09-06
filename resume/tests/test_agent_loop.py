"""Tests for the tool-calling agent loop: chaining, approval, budgets, errors."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from resume.models import Resume
from resume.services import agent_loop, agent_tools


def content(name="Ada Lovelace"):
    return {
        "user_info": {"full_name": name, "email": "ada@example.com", "skills": ["Python"]},
        "experience": [],
        "education": [],
        "projects_and_publications": [],
    }


def call(name, arguments, call_id="call_1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def assistant(content_text=None, tool_calls=None):
    return SimpleNamespace(content=content_text, tool_calls=tool_calls)


USAGE = SimpleNamespace(prompt_tokens=100, completion_tokens=20)


class LoopTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(
            user=self.user, title="CV", content=content()
        )
        self.ctx = {
            "lang": "en",
            "active_resume": self.resume,
            "resumes": [{"rank": 1, "id": self.resume.id, "display_name": "CV"}],
            "quota": {"is_pro": False},
        }

    def test_plain_answer_without_tools(self):
        with patch(
            "resume.services.agent_loop.send_openai_tool_turn",
            return_value=(assistant("Hello!"), USAGE),
        ):
            out = agent_loop.run_turn(self.user, self.ctx, [], "hi")
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["message"], "Hello!")
        self.assertEqual(out["effects"], [])

    def test_tool_result_is_fed_back_to_the_model(self):
        turns = [
            (assistant(tool_calls=[call("list_resumes", {})]), USAGE),
            (assistant("You have 1 resume."), USAGE),
        ]
        with patch(
            "resume.services.agent_loop.send_openai_tool_turn", side_effect=turns
        ) as llm:
            out = agent_loop.run_turn(self.user, self.ctx, [], "list them")

        self.assertEqual(out["status"], "done")
        # Second call must include the tool result the first call produced
        second_messages = llm.call_args_list[1].args[0]
        tool_messages = [m for m in second_messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertIn("resumes", tool_messages[0]["content"])

    def test_two_tools_chained_in_one_turn(self):
        turns = [
            (assistant(tool_calls=[call("list_resumes", {}, "c1")]), USAGE),
            (assistant(tool_calls=[call("preview_resume", {"resume_id": self.resume.id}, "c2")]), USAGE),
            (assistant("Listed and previewed."), USAGE),
        ]
        with patch("resume.services.agent_loop.send_openai_tool_turn", side_effect=turns):
            out = agent_loop.run_turn(self.user, self.ctx, [], "list then preview")
        self.assertEqual(out["status"], "done")
        effect_types = [e["type"] for e in out["effects"]]
        self.assertIn("chat", effect_types)      # list_resumes
        self.assertIn("preview", effect_types)   # preview_resume

    def test_history_is_sent_to_the_model(self):
        history = [
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer"},
        ]
        with patch(
            "resume.services.agent_loop.send_openai_tool_turn",
            return_value=(assistant("ok"), USAGE),
        ) as llm:
            agent_loop.run_turn(self.user, self.ctx, history, "follow up")
        sent = llm.call_args.args[0]
        contents = [m.get("content") for m in sent]
        self.assertIn("earlier question", contents)
        self.assertIn("earlier answer", contents)

    def test_unknown_tool_reported_to_the_model_not_crashing(self):
        turns = [
            (assistant(tool_calls=[call("nonexistent_tool", {})]), USAGE),
            (assistant("Sorry, I can't do that."), USAGE),
        ]
        with patch(
            "resume.services.agent_loop.send_openai_tool_turn", side_effect=turns
        ) as llm:
            out = agent_loop.run_turn(self.user, self.ctx, [], "do something odd")
        self.assertEqual(out["status"], "done")
        tool_messages = [m for m in llm.call_args_list[1].args[0] if m.get("role") == "tool"]
        self.assertIn("Unknown tool", tool_messages[0]["content"])

    def test_tool_exception_becomes_a_readable_result(self):
        turns = [
            (assistant(tool_calls=[call("list_resumes", {})]), USAGE),
            (assistant("Something went wrong."), USAGE),
        ]
        with patch("resume.services.agent_loop.send_openai_tool_turn", side_effect=turns) as llm, \
             patch.object(
                 agent_tools.TOOL_REGISTRY["list_resumes"], "handler",
                 side_effect=RuntimeError("boom")):
            out = agent_loop.run_turn(self.user, self.ctx, [], "list")
        self.assertEqual(out["status"], "done")
        tool_messages = [m for m in llm.call_args_list[1].args[0] if m.get("role") == "tool"]
        self.assertIn("error", tool_messages[0]["content"])

    def test_llm_failure_surfaces_as_error(self):
        with patch(
            "resume.services.agent_loop.send_openai_tool_turn",
            return_value=(None, "OpenAI API returned an API Error: nope"),
        ):
            out = agent_loop.run_turn(self.user, self.ctx, [], "hi")
        self.assertEqual(out["status"], "error")

    def test_step_budget_stops_a_looping_model(self):
        forever = (assistant(tool_calls=[call("list_resumes", {})]), USAGE)
        with patch(
            "resume.services.agent_loop.send_openai_tool_turn", return_value=forever
        ) as llm:
            out = agent_loop.run_turn(self.user, self.ctx, [], "loop forever")
        self.assertEqual(out["status"], "budget")
        self.assertLessEqual(llm.call_count, agent_loop.MAX_STEPS)

    def test_usage_is_accumulated(self):
        turns = [
            (assistant(tool_calls=[call("list_resumes", {})]), USAGE),
            (assistant("done"), USAGE),
        ]
        with patch("resume.services.agent_loop.send_openai_tool_turn", side_effect=turns):
            out = agent_loop.run_turn(self.user, self.ctx, [], "list")
        self.assertEqual(out["usage"]["prompt"], 200)
        self.assertEqual(out["usage"]["completion"], 40)


class ApprovalTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(
            user=self.user, title="CV", content=content()
        )
        self.ctx = {
            "lang": "en",
            "active_resume": self.resume,
            "resumes": [],
            "quota": {"is_pro": False},
        }

    def _pause_on_delete(self):
        turn = (assistant(tool_calls=[call("delete_resume", {"resume_id": self.resume.id})]), USAGE)
        with patch("resume.services.agent_loop.send_openai_tool_turn", return_value=turn):
            return agent_loop.run_turn(self.user, self.ctx, [], "delete it")

    def test_destructive_tool_pauses_before_running(self):
        out = self._pause_on_delete()
        self.assertEqual(out["status"], "needs_approval")
        self.assertEqual(out["tool"], "delete_resume")
        self.assertTrue(Resume.objects.filter(pk=self.resume.pk).exists())

    def test_approval_runs_the_tool_and_continues(self):
        out = self._pause_on_delete()
        parked = agent_loop.take_pending(self.user, out["token"])
        with patch(
            "resume.services.agent_loop.send_openai_tool_turn",
            return_value=(assistant("Deleted."), USAGE),
        ):
            resumed = agent_loop.resume_turn(self.user, self.ctx, parked, approved=True)
        self.assertEqual(resumed["status"], "done")
        self.assertFalse(Resume.objects.filter(pk=self.resume.pk).exists())

    def test_decline_leaves_the_resume_and_tells_the_model(self):
        out = self._pause_on_delete()
        parked = agent_loop.take_pending(self.user, out["token"])
        with patch(
            "resume.services.agent_loop.send_openai_tool_turn",
            return_value=(assistant("Okay, cancelled."), USAGE),
        ) as llm:
            agent_loop.resume_turn(self.user, self.ctx, parked, approved=False)
        self.assertTrue(Resume.objects.filter(pk=self.resume.pk).exists())
        tool_messages = [m for m in llm.call_args.args[0] if m.get("role") == "tool"]
        self.assertIn("denied_by_user", tool_messages[-1]["content"])

    def test_every_tool_call_gets_an_answer(self):
        """The API rejects a tool_call with no matching tool message."""
        out = self._pause_on_delete()
        parked = agent_loop.take_pending(self.user, out["token"])
        with patch(
            "resume.services.agent_loop.send_openai_tool_turn",
            return_value=(assistant("ok"), USAGE),
        ) as llm:
            agent_loop.resume_turn(self.user, self.ctx, parked, approved=False)
        sent = llm.call_args.args[0]
        call_ids = {
            c["id"] for m in sent if m.get("role") == "assistant"
            for c in m.get("tool_calls", [])
        }
        answered = {m["tool_call_id"] for m in sent if m.get("role") == "tool"}
        self.assertEqual(call_ids, answered)

    def test_token_is_single_use(self):
        out = self._pause_on_delete()
        self.assertIsNotNone(agent_loop.take_pending(self.user, out["token"]))
        self.assertIsNone(agent_loop.take_pending(self.user, out["token"]))

    def test_token_is_scoped_to_its_user(self):
        out = self._pause_on_delete()
        intruder = User.objects.create_user("eve", password="x")
        self.assertIsNone(agent_loop.take_pending(intruder, out["token"]))


class ToolSecurityTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.other = User.objects.create_user("eve", password="x")
        self.mine = Resume.objects.create(user=self.user, title="Mine", content=content())
        self.theirs = Resume.objects.create(
            user=self.other, title="Theirs", content=content("Eve")
        )
        self.ctx = {"lang": "en", "active_resume": None, "resumes": [], "quota": {}}

    def test_tools_refuse_another_users_resume(self):
        for name, args in [
            ("get_resume_details", {"resume_id": self.theirs.id}),
            ("preview_resume", {"resume_id": self.theirs.id}),
            ("delete_resume", {"resume_id": self.theirs.id}),
        ]:
            result = agent_tools.get_tool(name).handler(self.user, self.ctx, **args)
            self.assertIn("error", result.data, name)
        self.assertTrue(Resume.objects.filter(pk=self.theirs.pk).exists())

    def test_missing_resume_id_falls_back_to_active(self):
        ctx = {**self.ctx, "active_resume": self.mine}
        result = agent_tools.get_tool("get_resume_details").handler(self.user, ctx)
        self.assertEqual(result.data["id"], self.mine.id)

    def test_no_resume_id_and_no_active_asks_the_user(self):
        result = agent_tools.get_tool("get_resume_details").handler(self.user, self.ctx)
        self.assertIn("error", result.data)

    def test_download_respects_quota(self):
        ctx = {**self.ctx, "active_resume": self.mine}
        with patch.object(type(self.user.profile), "can_download", return_value=False):
            result = agent_tools.get_tool("download_resume").handler(self.user, ctx)
        self.assertIn("error", result.data)

    def test_every_tool_schema_is_strict(self):
        for schema in agent_tools.tool_schemas():
            fn = schema["function"]
            self.assertTrue(fn["strict"], fn["name"])
            self.assertFalse(fn["parameters"]["additionalProperties"], fn["name"])
            self.assertEqual(
                set(fn["parameters"]["required"]),
                set(fn["parameters"]["properties"]),
                fn["name"],
            )


class AgentEndpointTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())
        self.client.force_login(self.user)

    def test_chat_returns_an_agent_turn(self):
        with patch(
            "resume.services.agent_loop.send_openai_tool_turn",
            return_value=(assistant("Hi there."), USAGE),
        ):
            resp = self.client.post(
                reverse("resume:agent_chat"),
                json.dumps({"message": "hello"}),
                content_type="application/json",
            )
        body = resp.json()
        self.assertEqual(body["type"], "agent_turn")
        self.assertEqual(body["message"], "Hi there.")

    def test_message_counter_increments_once_per_turn(self):
        turns = [
            (assistant(tool_calls=[call("list_resumes", {})]), USAGE),
            (assistant("done"), USAGE),
        ]
        with patch("resume.services.agent_loop.send_openai_tool_turn", side_effect=turns):
            self.client.post(
                reverse("resume:agent_chat"),
                json.dumps({"message": "list"}),
                content_type="application/json",
            )
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.agent_message_count, 1)

    def test_quota_blocks_before_any_llm_call(self):
        with patch.object(
            type(self.user.profile), "can_send_agent_message", return_value=False
        ), patch("resume.services.agent_loop.send_openai_tool_turn") as llm:
            resp = self.client.post(
                reverse("resume:agent_chat"),
                json.dumps({"message": "hello"}),
                content_type="application/json",
            )
        self.assertTrue(resp.json()["quota_exceeded"])
        llm.assert_not_called()

    def test_approval_round_trip_over_http(self):
        turn = (assistant(tool_calls=[call("delete_resume", {"resume_id": self.resume.id})]), USAGE)
        with patch("resume.services.agent_loop.send_openai_tool_turn", return_value=turn):
            first = self.client.post(
                reverse("resume:agent_chat"),
                json.dumps({"message": "delete it"}),
                content_type="application/json",
            ).json()
        self.assertEqual(first["type"], "approval_required")
        self.assertTrue(Resume.objects.filter(pk=self.resume.pk).exists())

        with patch(
            "resume.services.agent_loop.send_openai_tool_turn",
            return_value=(assistant("Deleted."), USAGE),
        ):
            second = self.client.post(
                reverse("resume:agent_approve"),
                json.dumps({"token": first["token"], "approved": True}),
                content_type="application/json",
            ).json()
        self.assertEqual(second["message"], "Deleted.")
        self.assertFalse(Resume.objects.filter(pk=self.resume.pk).exists())

    def test_expired_or_forged_token_is_rejected(self):
        resp = self.client.post(
            reverse("resume:agent_approve"),
            json.dumps({"token": "deadbeef", "approved": True}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 410)

    def test_approve_requires_login(self):
        self.client.logout()
        resp = self.client.post(
            reverse("resume:agent_approve"),
            json.dumps({"token": "x", "approved": True}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 302)
