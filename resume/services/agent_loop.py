"""
The agent loop.

Replaces one-shot intent classification: the model may call tools, read their
results and keep going until it has an answer. Tool results are appended to the
message list, which is what lets a single user message drive several steps
("analyse it, fix the weak parts, switch template, download").

Destructive tools interrupt the loop. The pending call and the conversation so
far are parked server-side under a single-use token; approving resumes the same
loop rather than starting a new one. The client never holds that state — it
could otherwise alter the arguments or the destructive flag.
"""

import json
import logging
import uuid

from django.conf import settings
from django.core.cache import cache

from resume.openai_engine import send_openai_tool_turn, stream_openai_tool_turn
from resume.services import agent_tools

logger = logging.getLogger(__name__)

MAX_STEPS = 5
MAX_TOOL_CALLS = 8
PENDING_TTL_SECONDS = 300

# Approval copy follows the language the user is chatting in, not the interface
# setting — a Turkish conversation should not sprout English buttons.
APPROVAL_COPY = {
    "en": {
        "title": "This will change your resume. Continue?",
        "approve": "Yes, go ahead",
        "decline": "No, cancel",
    },
    "tr": {
        "title": "Bu işlem CV'nizi değiştirecek. Devam edilsin mi?",
        "approve": "Evet, devam et",
        "decline": "Hayır, iptal",
    },
}

# Shown instead of the raw tool name and arguments, which mean nothing to a user.
DESTRUCTIVE_COPY = {
    "en": {
        "modify_resume": "Edit the resume's content",
        "switch_template": "Change the resume's template",
        "translate_resume": "Translate the whole resume",
        "delete_resume": "Delete the resume permanently",
        "revert_last_change": "Undo the most recent change",
        "create_translated_copy": "Create a translated copy",
        "tailor_resume_for_job": "Create a copy tailored to this job",
    },
    "tr": {
        "modify_resume": "CV içeriğini düzenle",
        "switch_template": "CV şablonunu değiştir",
        "translate_resume": "CV'nin tamamını çevir",
        "delete_resume": "CV'yi kalıcı olarak sil",
        "revert_last_change": "Son değişikliği geri al",
        "create_translated_copy": "Çevrilmiş bir kopya oluştur",
        "tailor_resume_for_job": "Bu ilana özel bir kopya oluştur",
    },
}


# Progress wording, in the conversation's language for the same reason as the
# confirmations above.
STEP_COPY = {
    "en": {
        "_default": "Working...",
        "list_resumes": "Looking up your resumes...",
        "find_resume": "Searching your resumes...",
        "get_resume_details": "Reading the resume...",
        "preview_resume": "Preparing the preview...",
        "analyze_resume": "Analyzing the resume...",
        "compare_resumes": "Comparing the resumes...",
        "modify_resume": "Applying your edit...",
        "translate_resume": "Translating...",
        "create_translated_copy": "Creating the translated copy...",
        "list_language_versions": "Checking language versions...",
        "switch_template": "Switching template...",
        "download_resume": "Preparing the PDF...",
        "duplicate_resume": "Making a copy...",
        "check_quota": "Checking your limits...",
        "edit_resume": "Opening the editor...",
        "create_blank_resume": "Setting up a new resume...",
        "start_guided_build": "Starting the guided build...",
        "upload_resume": "Getting ready for your file...",
        "delete_resume": "Deleting...",
        "revert_last_change": "Undoing the last change...",
        "match_job": "Comparing against the posting...",
        "tailor_resume_for_job": "Tailoring the resume...",
        "list_jobs": "Looking up your applications...",
        "update_job": "Updating the application...",
        "resume_groups": "Grouping your resumes...",
    },
    "tr": {
        "_default": "Çalışıyorum...",
        "list_resumes": "CV'lerinize bakıyorum...",
        "find_resume": "CV'lerinizde arıyorum...",
        "get_resume_details": "CV'yi okuyorum...",
        "preview_resume": "Önizleme hazırlanıyor...",
        "analyze_resume": "CV analiz ediliyor...",
        "compare_resumes": "CV'ler karşılaştırılıyor...",
        "modify_resume": "Değişiklik uygulanıyor...",
        "translate_resume": "Çevriliyor...",
        "create_translated_copy": "Çevrilmiş kopya oluşturuluyor...",
        "list_language_versions": "Dil sürümleri kontrol ediliyor...",
        "switch_template": "Şablon değiştiriliyor...",
        "download_resume": "PDF hazırlanıyor...",
        "duplicate_resume": "Kopya oluşturuluyor...",
        "check_quota": "Limitleriniz kontrol ediliyor...",
        "edit_resume": "Editör açılıyor...",
        "create_blank_resume": "Yeni CV hazırlanıyor...",
        "start_guided_build": "Adım adım kurulum başlıyor...",
        "upload_resume": "Dosyanız için hazırlanıyorum...",
        "delete_resume": "Siliniyor...",
        "revert_last_change": "Son değişiklik geri alınıyor...",
        "match_job": "İlanla karşılaştırılıyor...",
        "tailor_resume_for_job": "CV ilana göre uyarlanıyor...",
        "list_jobs": "Başvurularınıza bakıyorum...",
        "update_job": "Başvuru güncelleniyor...",
        "resume_groups": "CV'leriniz gruplanıyor...",
    },
}


def step_copy(lang, tool_name):
    """Localized 'what I'm doing right now' line for a tool."""
    table = STEP_COPY.get(lang, STEP_COPY["en"])
    return table.get(tool_name) or table["_default"]


def approval_copy(lang, tool_name):
    """Localized confirmation text for a destructive tool."""
    copy = APPROVAL_COPY.get(lang, APPROVAL_COPY["en"])
    action = DESTRUCTIVE_COPY.get(lang, DESTRUCTIVE_COPY["en"]).get(tool_name)
    return {**copy, "action": action or ""}

SYSTEM_PROMPT = """You are ResuStack's resume assistant.

You help the user manage and improve their resumes by calling tools. Rules:

- Always reply in the same language as the user's message.
- A resume has its own written language, separate from the chat language. When
  editing or writing resume content, write it in THAT resume's language unless
  the user asks otherwise — a Turkish resume gets Turkish bullet points even if
  the user is chatting in English.
- To give the user the same resume in a second language, use
  create_translated_copy — it keeps the original. translate_resume overwrites.
- When the user pastes a job posting, call match_job. Tailoring needs a saved
  posting, so match_job runs first and tailor_resume_for_job second.
- Prefer acting over asking. If the user's intent is clear, call the tool.
- Chain tools when a request needs several steps, then summarise what you did.
- Never invent a resume id. Use the ids listed below, or omit resume_id to act
  on the active resume.
- Removing part of a resume (an experience, a skill, a section) is
  modify_resume. delete_resume destroys the entire document — only use it when
  the user clearly means the whole resume.
- After tools run, write a short, friendly confirmation. Do not repeat data the
  side panel already shows in full; summarise it.
- If a tool returns an error, explain it plainly and suggest what to do next.
- Job matching, tailoring a resume to a posting and application tracking are
  Pro features. If they are not in your tool list, this user is on the free
  plan: say the feature needs Pro rather than pretending you did it, and
  mention they can buy a fixed period at /pricing/ — there is no subscription.
"""


def _context_block(ctx):
    """Facts the model needs, rendered once. Kept after the static prompt."""
    lines = [f"Resumes (rank 1 = most recently updated): {json.dumps(ctx['resumes'], ensure_ascii=False)}"]
    active = ctx.get("active_resume")
    if active:
        experiences = (active.content or {}).get("experience", [])
        summary = ", ".join(
            f"{e.get('title', '?')} at {e.get('company', '?')}" for e in experiences[:5]
        )
        versions = [
            f"{r.language}(id={r.id})" for r in active.language_family()
        ]
        lines.append(
            f"Active resume: id={active.id}, name={active.display_name}, "
            f"written in '{active.language}', template={active.template_selector}, "
            f"experiences=[{summary or 'none'}]. "
            f"Language versions of this resume: {', '.join(versions)}. "
            "Tools called without resume_id act on this one."
        )
    else:
        lines.append("No resume is currently active.")
    lines.append(f"Quota: {json.dumps(ctx['quota'], ensure_ascii=False)}")
    return "\n".join(lines)


def _build_messages(ctx, history, user_message):
    """
    Static content first (system prompt + tool schemas are sent separately but
    the prompt itself is constant), so the provider's prompt cache can hit.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "system", "content": _context_block(ctx)})
    for turn in (history or [])[-10:]:
        role = turn.get("role")
        text = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": user_message})
    return messages


def _normalise(message):
    """
    Flatten a turn into (content, calls) regardless of how it arrived.

    The buffered API returns objects; the streamed one reassembles dicts. The
    loop should not care which.
    """
    if isinstance(message, dict):
        content = message.get("content") or ""
        calls = [
            {
                "id": c["id"],
                "name": c["name"],
                "arguments": c.get("arguments") or "{}",
            }
            for c in message.get("tool_calls") or []
        ]
        return content, calls

    calls = [
        {
            "id": c.id,
            "name": c.function.name,
            "arguments": c.function.arguments or "{}",
        }
        for c in (message.tool_calls or [])
    ]
    return message.content or "", calls


def _serialise_assistant(content, calls):
    """Assistant turns must go back to the API as plain dicts."""
    payload = {"role": "assistant", "content": content}
    if calls:
        payload["tool_calls"] = [
            {
                "id": c["id"],
                "type": "function",
                "function": {"name": c["name"], "arguments": c["arguments"]},
            }
            for c in calls
        ]
    return payload


def _tool_message(call_id, data):
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(data, ensure_ascii=False, default=str),
    }


def _park(user, messages, call, effects, step, lang):
    """Store the paused loop under a single-use token."""
    token = uuid.uuid4().hex
    cache.set(
        f"agent_pending_{user.id}_{token}",
        {
            "messages": messages,
            "call": call,
            "effects": effects,
            "step": step,
            # Remember the conversation language so the resumed half does not
            # have to re-detect it from a request that carries no message.
            "lang": lang,
        },
        PENDING_TTL_SECONDS,
    )
    return token


def take_pending(user, token):
    """Read and consume a parked loop. Single use — a double click cannot replay it."""
    key = f"agent_pending_{user.id}_{token}"
    parked = cache.get(key)
    if parked:
        cache.delete(key)
    return parked


def _llm_turn(messages, schemas, stream, on_token):
    """One model turn. Returns (content, calls, error, usage)."""
    if not stream:
        message, usage = send_openai_tool_turn(messages, schemas)
        if message is None:
            return None, None, str(usage), None
        content, calls = _normalise(message)
        return content, calls, None, usage

    content, calls, usage = None, None, None
    for event in stream_openai_tool_turn(messages, schemas):
        kind = event[0]
        if kind == "token":
            on_token(event[1])
        elif kind == "error":
            return None, None, event[1], None
        elif kind == "message":
            content, calls = _normalise(event[1])
            usage = event[2]
    return content or "", calls or [], None, usage


def _run_events(user, ctx, messages, effects, start_step, usage_totals, stream=False):
    """
    Drive the loop, yielding progress as it happens.

    Yields ("token", text), ("step", tool_name) and ("effect", payload) along
    the way, then exactly one ("done", outcome). The buffered and streaming
    endpoints share this so there is a single implementation of the loop.
    """
    tool_calls_made = 0
    schemas = agent_tools.tool_schemas(user)
    pending_tokens = []

    for step in range(start_step, MAX_STEPS):
        pending_tokens.clear()
        content, calls, error, usage = _llm_turn(
            messages, schemas, stream, pending_tokens.append
        )
        # Prose is only emitted once we know the turn ended in an answer rather
        # than a tool call — otherwise a model that "thinks out loud" before
        # calling a tool would leak that text into the chat.
        if error:
            logger.warning("Agent loop LLM failure: %s", error)
            yield ("done", {"status": "error", "message": error, "effects": effects})
            return
        if usage:
            usage_totals["prompt"] += getattr(usage, "prompt_tokens", 0) or 0
            usage_totals["completion"] += getattr(usage, "completion_tokens", 0) or 0

        if not calls:
            for text in pending_tokens:
                yield ("token", text)
            yield ("done", {"status": "done", "message": content, "effects": effects})
            return

        messages.append(_serialise_assistant(content, calls))

        for call in calls:
            tool_calls_made += 1
            if tool_calls_made > MAX_TOOL_CALLS:
                yield ("done", {"status": "budget", "message": "", "effects": effects})
                return

            tool = agent_tools.get_tool(call["name"])
            if tool is None:
                messages.append(
                    _tool_message(call["id"], {"error": f"Unknown tool {call['name']}"})
                )
                continue

            if tool.destructive:
                token = _park(user, messages, call, effects, step, ctx.get("lang", "en"))
                yield (
                    "done",
                    {
                        "status": "needs_approval",
                        "token": token,
                        "tool": tool.name,
                        "copy": approval_copy(ctx.get("lang", "en"), tool.name),
                        "effects": effects,
                    },
                )
                return

            yield ("step", step_copy(ctx.get("lang", "en"), tool.name))
            result = _invoke(tool, user, ctx, call)
            messages.append(_tool_message(call["id"], result.data))
            for effect in result.ui:
                effects.append(effect)
                yield ("effect", effect)

    yield ("done", {"status": "budget", "message": "", "effects": effects})


def _run(user, ctx, messages, effects, start_step, usage_totals):
    """Buffered form: run the loop to completion and return the outcome."""
    for event in _run_events(user, ctx, messages, effects, start_step, usage_totals):
        if event[0] == "done":
            return event[1]
    return {"status": "budget", "message": "", "effects": effects}


def _safe_args(raw_arguments):
    try:
        return json.loads(raw_arguments or "{}")
    except ValueError:
        return {}


def _invoke(tool, user, ctx, call):
    """Run a tool, turning any failure into a result the model can read."""
    arguments = _safe_args(call["arguments"])
    # strict mode sends every property, nulls included — drop them so Python
    # defaults apply instead of overriding them with None.
    arguments = {k: v for k, v in arguments.items() if v is not None}
    try:
        return tool.handler(user, ctx, **arguments)
    except TypeError as exc:
        logger.warning("Tool %s called with bad arguments: %s", tool.name, exc)
        return agent_tools.ToolResult(data={"error": f"Invalid arguments: {exc}"})
    except Exception:
        logger.exception("Tool %s failed", tool.name)
        return agent_tools.ToolResult(
            data={"error": "The tool failed unexpectedly. Tell the user to try again."}
        )


def stream_turn(user, ctx, history, user_message):
    """Streaming form of run_turn: yields loop events as they happen."""
    usage_totals = {"prompt": 0, "completion": 0}
    messages = _build_messages(ctx, history, user_message)
    for event in _run_events(
        user, ctx, messages, [], 0, usage_totals, stream=True
    ):
        if event[0] == "done":
            outcome = dict(event[1])
            outcome["usage"] = usage_totals
            yield ("done", outcome)
            return
        yield event


def stream_resume_turn(user, ctx, parked, approved):
    """Streaming form of resume_turn."""
    usage_totals = {"prompt": 0, "completion": 0}
    messages, effects, new_effects = _prepare_resume(user, ctx, parked, approved)
    for effect in new_effects:
        yield ("effect", effect)
    for event in _run_events(
        user, ctx, messages, effects, parked["step"], usage_totals, stream=True
    ):
        if event[0] == "done":
            outcome = dict(event[1])
            outcome["usage"] = usage_totals
            yield ("done", outcome)
            return
        yield event


def run_turn(user, ctx, history, user_message):
    """Handle one user message. Returns a status dict for the view to render."""
    usage_totals = {"prompt": 0, "completion": 0}
    messages = _build_messages(ctx, history, user_message)
    outcome = _run(user, ctx, messages, [], 0, usage_totals)
    outcome["usage"] = usage_totals
    return outcome


def resume_turn(user, ctx, parked, approved):
    """
    Continue a loop that paused for approval.

    Whether the user approved or declined, a tool result is appended for the
    pending call — the API requires every tool_call to be answered, and it lets
    the model react to a refusal instead of the conversation dead-ending.
    """
    usage_totals = {"prompt": 0, "completion": 0}
    messages, effects, _new = _prepare_resume(user, ctx, parked, approved)
    outcome = _run(user, ctx, messages, effects, parked["step"], usage_totals)
    outcome["usage"] = usage_totals
    return outcome


def _prepare_resume(user, ctx, parked, approved):
    """
    Answer the pending tool call, whether it was approved or refused.

    Returns (messages, effects, new_effects) — the third being what the approved
    tool just produced, so a streaming caller can emit it instead of leaving it
    to the final payload.
    """
    messages = parked["messages"]
    effects = parked["effects"]
    call_info = parked["call"]
    new_effects = []

    if approved:
        tool = agent_tools.get_tool(call_info["name"])
        if tool is None:
            messages.append(
                _tool_message(call_info["id"], {"error": "Tool no longer available."})
            )
        else:
            result = _invoke(tool, user, ctx, call_info)
            messages.append(_tool_message(call_info["id"], result.data))
            effects.extend(result.ui)
            new_effects.extend(result.ui)
    else:
        messages.append(
            _tool_message(
                call_info["id"],
                {"denied_by_user": True, "note": "The user declined this action."},
            )
        )

    return messages, effects, new_effects

