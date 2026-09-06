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

from resume.openai_engine import send_openai_tool_turn
from resume.services import agent_tools

logger = logging.getLogger(__name__)

MAX_STEPS = 5
MAX_TOOL_CALLS = 8
PENDING_TTL_SECONDS = 300

SYSTEM_PROMPT = """You are ResuStack's resume assistant.

You help the user manage and improve their resumes by calling tools. Rules:

- Always reply in the same language as the user's message.
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
        lines.append(
            f"Active resume: id={active.id}, name={active.display_name}, "
            f"template={active.template_selector}, experiences=[{summary or 'none'}]. "
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


def _serialise_assistant(message):
    """Assistant turns must go back to the API as plain dicts."""
    payload = {"role": "assistant", "content": message.content or ""}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in message.tool_calls
        ]
    return payload


def _tool_message(call_id, data):
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(data, ensure_ascii=False, default=str),
    }


def _park(user, messages, call, effects, step):
    """Store the paused loop under a single-use token."""
    token = uuid.uuid4().hex
    cache.set(
        f"agent_pending_{user.id}_{token}",
        {
            "messages": messages,
            "call": {
                "id": call.id,
                "name": call.function.name,
                "arguments": call.function.arguments,
            },
            "effects": effects,
            "step": step,
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


def _run(user, ctx, messages, effects, start_step, usage_totals):
    """Drive the loop until the model stops calling tools, or a budget runs out."""
    tool_calls_made = 0
    schemas = agent_tools.tool_schemas()

    for step in range(start_step, MAX_STEPS):
        message, usage = send_openai_tool_turn(messages, schemas)
        if message is None:
            logger.warning("Agent loop LLM failure: %s", usage)
            return {"status": "error", "message": str(usage), "effects": effects}
        if usage:
            usage_totals["prompt"] += getattr(usage, "prompt_tokens", 0) or 0
            usage_totals["completion"] += getattr(usage, "completion_tokens", 0) or 0

        if not message.tool_calls:
            return {
                "status": "done",
                "message": message.content or "",
                "effects": effects,
            }

        messages.append(_serialise_assistant(message))

        for call in message.tool_calls:
            tool_calls_made += 1
            if tool_calls_made > MAX_TOOL_CALLS:
                return {
                    "status": "budget",
                    "message": "",
                    "effects": effects,
                }

            tool = agent_tools.get_tool(call.function.name)
            if tool is None:
                messages.append(
                    _tool_message(call.id, {"error": f"Unknown tool {call.function.name}"})
                )
                continue

            if tool.destructive:
                token = _park(user, messages, call, effects, step)
                return {
                    "status": "needs_approval",
                    "token": token,
                    "tool": tool.name,
                    "arguments": _safe_args(call.function.arguments),
                    "effects": effects,
                }

            result = _invoke(tool, user, ctx, call)
            messages.append(_tool_message(call.id, result.data))
            effects.extend(result.ui)

    return {"status": "budget", "message": "", "effects": effects}


def _safe_args(raw_arguments):
    try:
        return json.loads(raw_arguments or "{}")
    except ValueError:
        return {}


def _invoke(tool, user, ctx, call):
    """Run a tool, turning any failure into a result the model can read."""
    arguments = _safe_args(call.function.arguments)
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
    messages = parked["messages"]
    effects = parked["effects"]
    call_info = parked["call"]

    if approved:
        tool = agent_tools.get_tool(call_info["name"])
        if tool is None:
            messages.append(
                _tool_message(call_info["id"], {"error": "Tool no longer available."})
            )
        else:
            stub = _CallStub(call_info["id"], call_info["name"], call_info["arguments"])
            result = _invoke(tool, user, ctx, stub)
            messages.append(_tool_message(call_info["id"], result.data))
            effects.extend(result.ui)
    else:
        messages.append(
            _tool_message(
                call_info["id"],
                {"denied_by_user": True, "note": "The user declined this action."},
            )
        )

    outcome = _run(user, ctx, messages, effects, parked["step"], usage_totals)
    outcome["usage"] = usage_totals
    return outcome


class _CallStub:
    """Rebuilds the shape _invoke expects from a parked call."""

    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = type("fn", (), {"name": name, "arguments": arguments})()
