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


class BuilderEntryTest(TestCase):
    """The guided builder is reached directly, not via LLM classification."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.client.force_login(self.user)

    def test_start_returns_the_first_question_without_an_llm_call(self):
        with patch("resume.services.agent_loop.send_openai_tool_turn") as llm:
            resp = self.client.post(
                reverse("resume:agent_builder_start"),
                json.dumps({"lang": "en"}),
                content_type="application/json",
            )
        body = resp.json()
        self.assertEqual(body["type"], "multi_step")
        self.assertEqual(body["step"], "ask_name")
        self.assertTrue(body["message"])
        llm.assert_not_called()

    def test_start_does_not_spend_message_quota(self):
        self.client.post(
            reverse("resume:agent_builder_start"),
            json.dumps({"lang": "en"}),
            content_type="application/json",
        )
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.agent_message_count, 0)

    def test_start_respects_the_resume_limit(self):
        with patch.object(
            type(self.user.profile), "can_create_resume", return_value=False
        ):
            resp = self.client.post(
                reverse("resume:agent_builder_start"),
                json.dumps({"lang": "en"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 403)

    def test_language_falls_back_for_an_unknown_code(self):
        resp = self.client.post(
            reverse("resume:agent_builder_start"),
            json.dumps({"lang": "klingon"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(
            reverse("resume:agent_builder_start"),
            json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 302)


class StreamingTest(TestCase):
    """The SSE path must carry the same outcome as the buffered one."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())
        self.client.force_login(self.user)
        self.ctx = {
            "lang": "en",
            "active_resume": self.resume,
            "resumes": [],
            "quota": {},
        }

    @staticmethod
    def _stream(*turns):
        """Replay one scripted stream per model turn."""
        remaining = list(turns)

        def fake(messages, tools, **kwargs):
            yield from remaining.pop(0)

        return fake

    @staticmethod
    def _frames(body):
        """Parse an SSE body into (event, payload) pairs."""
        out = []
        for frame in body.strip().split("\n\n"):
            event, data = "message", []
            for line in frame.split("\n"):
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
            if data:
                out.append((event, json.loads("\n".join(data))))
        return out

    def test_tokens_then_done(self):
        from resume.services import agent_loop

        turn = [("token", "Hel"), ("token", "lo"), ("message", {"content": "Hello", "tool_calls": []}, USAGE)]
        with patch(
            "resume.services.agent_loop.stream_openai_tool_turn",
            side_effect=self._stream(turn),
        ):
            events = list(agent_loop.stream_turn(self.user, self.ctx, [], "hi"))

        kinds = [e[0] for e in events]
        self.assertEqual(kinds, ["token", "token", "done"])
        self.assertEqual("".join(e[1] for e in events if e[0] == "token"), "Hello")
        self.assertEqual(events[-1][1]["status"], "done")

    def test_step_and_effect_are_emitted_before_the_answer(self):
        from resume.services import agent_loop

        first = [
            ("message", {"content": "", "tool_calls": [
                {"id": "c1", "name": "list_resumes", "arguments": "{}"}
            ]}, USAGE)
        ]
        second = [("token", "Done."), ("message", {"content": "Done.", "tool_calls": []}, USAGE)]
        with patch(
            "resume.services.agent_loop.stream_openai_tool_turn",
            side_effect=self._stream(first, second),
        ):
            events = list(agent_loop.stream_turn(self.user, self.ctx, [], "list"))

        kinds = [e[0] for e in events]
        self.assertEqual(kinds.index("step"), 0)
        self.assertLess(kinds.index("effect"), kinds.index("token"))
        self.assertEqual(kinds[-1], "done")

    def test_thinking_aloud_before_a_tool_call_is_not_shown(self):
        """Prose from a turn that ends in a tool call must not leak into chat."""
        from resume.services import agent_loop

        first = [
            ("token", "Let me check that."),
            ("message", {"content": "Let me check that.", "tool_calls": [
                {"id": "c1", "name": "list_resumes", "arguments": "{}"}
            ]}, USAGE),
        ]
        second = [("token", "You have 1."), ("message", {"content": "You have 1.", "tool_calls": []}, USAGE)]
        with patch(
            "resume.services.agent_loop.stream_openai_tool_turn",
            side_effect=self._stream(first, second),
        ):
            events = list(agent_loop.stream_turn(self.user, self.ctx, [], "list"))

        tokens = "".join(e[1] for e in events if e[0] == "token")
        self.assertEqual(tokens, "You have 1.")

    def test_stream_pauses_for_a_destructive_tool(self):
        from resume.services import agent_loop

        turn = [("message", {"content": "", "tool_calls": [
            {"id": "c1", "name": "delete_resume",
             "arguments": json.dumps({"resume_id": self.resume.id})}
        ]}, USAGE)]
        with patch(
            "resume.services.agent_loop.stream_openai_tool_turn",
            side_effect=self._stream(turn),
        ):
            events = list(agent_loop.stream_turn(self.user, self.ctx, [], "delete"))
        self.assertEqual(events[-1][1]["status"], "needs_approval")
        self.assertTrue(Resume.objects.filter(pk=self.resume.pk).exists())

    def test_stream_error_ends_the_turn(self):
        from resume.services import agent_loop

        turn = [("error", "OpenAI API returned an API Error: nope")]
        with patch(
            "resume.services.agent_loop.stream_openai_tool_turn",
            side_effect=self._stream(turn),
        ):
            events = list(agent_loop.stream_turn(self.user, self.ctx, [], "hi"))
        self.assertEqual(events[-1][1]["status"], "error")

    def test_endpoint_returns_sse_frames(self):
        turn = [("token", "Hi."), ("message", {"content": "Hi.", "tool_calls": []}, USAGE)]
        with patch(
            "resume.services.agent_loop.stream_openai_tool_turn",
            side_effect=self._stream(turn),
        ):
            resp = self.client.post(
                reverse("resume:agent_chat_stream"),
                json.dumps({"message": "hello"}),
                content_type="application/json",
            )
            body = b"".join(resp.streaming_content).decode()

        self.assertEqual(resp["Content-Type"], "text/event-stream")
        self.assertEqual(resp["X-Accel-Buffering"], "no")
        self.assertIn("event: token", body)
        self.assertIn("event: done", body)
        self.assertIn('"type": "agent_turn"', body)

    def test_endpoint_charges_one_message(self):
        turn = [("message", {"content": "Hi.", "tool_calls": []}, USAGE)]
        with patch(
            "resume.services.agent_loop.stream_openai_tool_turn",
            side_effect=self._stream(turn),
        ):
            resp = self.client.post(
                reverse("resume:agent_chat_stream"),
                json.dumps({"message": "hello"}),
                content_type="application/json",
            )
            b"".join(resp.streaming_content)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.agent_message_count, 1)

    def test_quota_answers_with_json_not_a_stream(self):
        with patch.object(
            type(self.user.profile), "can_send_agent_message", return_value=False
        ):
            resp = self.client.post(
                reverse("resume:agent_chat_stream"),
                json.dumps({"message": "hello"}),
                content_type="application/json",
            )
        self.assertIn("application/json", resp["Content-Type"])
        self.assertTrue(resp.json()["quota_exceeded"])

    def test_approve_stream_round_trip(self):
        pause = [("message", {"content": "", "tool_calls": [
            {"id": "c1", "name": "delete_resume",
             "arguments": json.dumps({"resume_id": self.resume.id})}
        ]}, USAGE)]
        after = [("message", {"content": "Deleted.", "tool_calls": []}, USAGE)]

        with patch(
            "resume.services.agent_loop.stream_openai_tool_turn",
            side_effect=self._stream(pause),
        ):
            first = b"".join(
                self.client.post(
                    reverse("resume:agent_chat_stream"),
                    json.dumps({"message": "delete it"}),
                    content_type="application/json",
                ).streaming_content
            ).decode()
        done = next(p for e, p in self._frames(first) if e == "done")
        self.assertEqual(done["type"], "approval_required")
        token = done["token"]

        with patch(
            "resume.services.agent_loop.stream_openai_tool_turn",
            side_effect=self._stream(after),
        ):
            b"".join(
                self.client.post(
                    reverse("resume:agent_approve_stream"),
                    json.dumps({"token": token, "approved": True}),
                    content_type="application/json",
                ).streaming_content
            )
        self.assertFalse(Resume.objects.filter(pk=self.resume.pk).exists())


class ProgressCopyTest(TestCase):
    """Progress wording follows the conversation, like the confirmations do."""

    def test_step_copy_is_localized(self):
        from resume.services import agent_loop

        self.assertIn("Analyzing", agent_loop.step_copy("en", "analyze_resume"))
        self.assertIn("analiz", agent_loop.step_copy("tr", "analyze_resume"))

    def test_unknown_tool_falls_back(self):
        from resume.services import agent_loop

        self.assertEqual(
            agent_loop.step_copy("tr", "brand_new_tool"),
            agent_loop.STEP_COPY["tr"]["_default"],
        )

    def test_every_tool_has_progress_copy_in_both_languages(self):
        from resume.services import agent_loop

        for lang in ("en", "tr"):
            for name in agent_tools.TOOL_REGISTRY:
                self.assertIn(
                    name, agent_loop.STEP_COPY[lang], f"{name} missing {lang} progress copy"
                )

    def test_stream_emits_localized_step(self):
        from resume.services import agent_loop

        first = [("message", {"content": "", "tool_calls": [
            {"id": "c1", "name": "list_resumes", "arguments": "{}"}
        ]}, USAGE)]
        second = [("message", {"content": "ok", "tool_calls": []}, USAGE)]
        remaining = [first, second]

        def fake(messages, tools, **kwargs):
            yield from remaining.pop(0)

        user = User.objects.create_user("ada", password="x")
        ctx = {"lang": "tr", "active_resume": None, "resumes": [], "quota": {}}
        with patch("resume.services.agent_loop.stream_openai_tool_turn", side_effect=fake):
            events = list(agent_loop.stream_turn(user, ctx, [], "listele"))
        step = next(e[1] for e in events if e[0] == "step")
        self.assertIn("CV'lerinize", step)


class LanguageDetectionTest(TestCase):
    """Terse follow-ups must not flip the conversation language."""

    def setUp(self):
        from resume.services.agent_service import agent_service
        self.detect = agent_service._detect_language

    def test_turkish_special_characters(self):
        self.assertEqual(self.detect("CV'mi güncelle"), "tr")

    def test_turkish_without_special_characters(self):
        """'yeteneklerime AWS ekle' has no Turkish-only letters."""
        self.assertEqual(self.detect("yeteneklerime AWS ekle"), "tr")

    def test_plain_english(self):
        self.assertEqual(self.detect("add AWS to my skills"), "en")

    def test_history_keeps_the_conversation_turkish(self):
        history = [
            {"role": "user", "content": "CV'mi analiz et"},
            {"role": "assistant", "content": "..."},
        ]
        self.assertEqual(self.detect("AWS", history), "tr")

    def test_history_does_not_make_english_turkish(self):
        history = [{"role": "user", "content": "analyze my resume"}]
        self.assertEqual(self.detect("add AWS", history), "en")

    def test_assistant_turns_are_ignored(self):
        history = [{"role": "assistant", "content": "CV'niz analiz edildi"}]
        self.assertEqual(self.detect("download it", history), "en")


class ParkedLanguageTest(TestCase):
    """The resumed half of a paused turn keeps speaking the same language."""

    def test_language_is_parked_with_the_pending_call(self):
        from resume.services import agent_loop

        user = User.objects.create_user("ada", password="x")
        resume = Resume.objects.create(user=user, title="CV", content=content())
        ctx = {"lang": "tr", "active_resume": resume, "resumes": [], "quota": {}}
        turn = (assistant(tool_calls=[call("delete_resume", {"resume_id": resume.id})]), USAGE)
        with patch("resume.services.agent_loop.send_openai_tool_turn", return_value=turn):
            out = agent_loop.run_turn(user, ctx, [], "sil")
        self.assertIn("Devam", out["copy"]["title"])
        parked = agent_loop.take_pending(user, out["token"])
        self.assertEqual(parked["lang"], "tr")


class ApprovedEffectStreamTest(TestCase):
    """What the approved tool produced must reach a streaming client."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())
        self.ctx = {
            "lang": "en",
            "active_resume": self.resume,
            "resumes": [],
            "quota": {},
        }

    def test_effects_are_emitted_before_the_continuation(self):
        from resume.services import agent_loop

        pause = (
            assistant(tool_calls=[call("switch_template",
                                       {"resume_id": self.resume.id,
                                        "template": "modern-sidebar"})]),
            USAGE,
        )
        with patch("resume.services.agent_loop.send_openai_tool_turn", return_value=pause):
            out = agent_loop.run_turn(self.user, self.ctx, [], "switch template")
        parked = agent_loop.take_pending(self.user, out["token"])

        after = [("message", {"content": "Switched.", "tool_calls": []}, USAGE)]
        remaining = [after]

        def fake(messages, tools, **kwargs):
            yield from remaining.pop(0)

        with patch("resume.services.agent_loop.stream_openai_tool_turn", side_effect=fake):
            events = list(
                agent_loop.stream_resume_turn(self.user, self.ctx, parked, approved=True)
            )

        kinds = [e[0] for e in events]
        self.assertIn("effect", kinds)
        self.assertLess(kinds.index("effect"), kinds.index("done"))
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.template_selector, "modern-sidebar")

    def test_a_declined_call_emits_no_effect(self):
        from resume.services import agent_loop

        pause = (
            assistant(tool_calls=[call("delete_resume", {"resume_id": self.resume.id})]),
            USAGE,
        )
        with patch("resume.services.agent_loop.send_openai_tool_turn", return_value=pause):
            out = agent_loop.run_turn(self.user, self.ctx, [], "delete it")
        parked = agent_loop.take_pending(self.user, out["token"])

        remaining = [[("message", {"content": "Cancelled.", "tool_calls": []}, USAGE)]]

        def fake(messages, tools, **kwargs):
            yield from remaining.pop(0)

        with patch("resume.services.agent_loop.stream_openai_tool_turn", side_effect=fake):
            events = list(
                agent_loop.stream_resume_turn(self.user, self.ctx, parked, approved=False)
            )
        self.assertNotIn("effect", [e[0] for e in events])
        self.assertTrue(Resume.objects.filter(pk=self.resume.pk).exists())


class AgentTurnCopyTest(TestCase):
    """An agent turn tells the client which language to render its own UI in."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        Resume.objects.create(user=self.user, title="CV", content=content())
        self.client.force_login(self.user)

    def _turn(self, message):
        with patch(
            "resume.services.agent_loop.send_openai_tool_turn",
            return_value=(assistant("ok"), USAGE),
        ):
            return self.client.post(
                reverse("resume:agent_chat"),
                json.dumps({"message": message}),
                content_type="application/json",
            ).json()

    def test_turkish_message_returns_turkish_diff_copy(self):
        body = self._turn("CV'mi göster")
        self.assertEqual(body["lang"], "tr")
        self.assertEqual(body["diff_copy"]["restore"], "Geri yükle")

    def test_english_message_returns_english_diff_copy(self):
        body = self._turn("show my resume")
        self.assertEqual(body["lang"], "en")
        self.assertEqual(body["diff_copy"]["restore"], "Restore")


class StreamCopyOrderTest(TestCase):
    """Panel wording must arrive before the panels it labels."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        Resume.objects.create(user=self.user, title="CV", content=content())
        self.client.force_login(self.user)

    def test_copy_frame_precedes_every_effect(self):
        turns = [
            [("message", {"content": "", "tool_calls": [
                {"id": "c1", "name": "list_resumes", "arguments": "{}"}
            ]}, USAGE)],
            [("message", {"content": "done", "tool_calls": []}, USAGE)],
        ]

        def fake(messages, tools, **kwargs):
            yield from turns.pop(0)

        with patch("resume.services.agent_loop.stream_openai_tool_turn", side_effect=fake):
            body = b"".join(
                self.client.post(
                    reverse("resume:agent_chat_stream"),
                    json.dumps({"message": "CV'lerimi listele"}),
                    content_type="application/json",
                ).streaming_content
            ).decode()

        order = [line[7:].strip() for line in body.split("\n") if line.startswith("event:")]
        self.assertEqual(order[0], "copy")
        self.assertLess(order.index("copy"), order.index("effect"))

    def test_copy_frame_is_in_the_conversation_language(self):
        turns = [[("message", {"content": "ok", "tool_calls": []}, USAGE)]]

        def fake(messages, tools, **kwargs):
            yield from turns.pop(0)

        with patch("resume.services.agent_loop.stream_openai_tool_turn", side_effect=fake):
            body = b"".join(
                self.client.post(
                    reverse("resume:agent_chat_stream"),
                    json.dumps({"message": "CV'lerimi göster"}),
                    content_type="application/json",
                ).streaming_content
            ).decode()

        frame = next(f for f in body.split("\n\n") if "event: copy" in f)
        payload = json.loads(frame.split("data:", 1)[1].strip())
        self.assertEqual(payload["lang"], "tr")
        self.assertEqual(payload["ui_copy"]["job"]["jobs_title"], "Başvurular")


class ConfirmationSettingTest(TestCase):
    """Turning confirmations off lets destructive tools run straight through."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())
        self.client.force_login(self.user)

    def _ctx(self, confirm):
        return {
            "lang": "en",
            "confirm_destructive": confirm,
            "active_resume": self.resume,
            "resumes": [],
            "quota": {},
        }

    def _delete_turn(self):
        return (
            assistant(tool_calls=[call("delete_resume", {"resume_id": self.resume.id})]),
            USAGE,
        )

    def test_on_by_default_the_loop_pauses(self):
        from resume.services import agent_loop

        with patch("resume.services.agent_loop.send_openai_tool_turn",
                   return_value=self._delete_turn()):
            out = agent_loop.run_turn(self.user, self._ctx(True), [], "delete it")
        self.assertEqual(out["status"], "needs_approval")
        self.assertTrue(Resume.objects.filter(pk=self.resume.pk).exists())

    def test_off_the_tool_runs_immediately(self):
        from resume.services import agent_loop

        turns = [self._delete_turn(), (assistant("Deleted."), USAGE)]
        with patch("resume.services.agent_loop.send_openai_tool_turn",
                   side_effect=turns):
            out = agent_loop.run_turn(self.user, self._ctx(False), [], "delete it")
        self.assertEqual(out["status"], "done")
        self.assertFalse(Resume.objects.filter(pk=self.resume.pk).exists())

    def test_default_is_on_for_a_new_account(self):
        self.assertTrue(self.user.profile.confirm_destructive)

    def test_toggle_endpoint(self):
        resp = self.client.post(
            reverse("resume:toggle_confirm_destructive"),
            json.dumps({"enabled": False}),
            content_type="application/json",
        )
        self.assertTrue(resp.json()["success"])
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.confirm_destructive)

    def test_toggle_requires_post_and_login(self):
        self.assertEqual(
            self.client.get(reverse("resume:toggle_confirm_destructive")).status_code,
            405,
        )
        self.client.logout()
        self.assertEqual(
            self.client.post(reverse("resume:toggle_confirm_destructive")).status_code,
            302,
        )


class UploadDoesNotClaimSuccessTest(TestCase):
    """The tool result must not read as a completed import."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.ctx = {"lang": "en", "active_resume": None, "resumes": [], "quota": {}}

    def test_result_says_it_is_waiting(self):
        result = agent_tools.get_tool("upload_resume").handler(
            self.user, self.ctx, source="pdf"
        )
        self.assertTrue(result.data["awaiting_file"])
        self.assertNotIn("ok", result.data)
        self.assertIn("nothing has been uploaded", result.data["note"].lower())

    def test_the_picker_is_still_shown(self):
        result = agent_tools.get_tool("upload_resume").handler(
            self.user, self.ctx, source="pdf"
        )
        self.assertEqual(result.ui[0]["type"], "request_upload")
