"""
A check between the model choosing a tool and the tool running.

The chat model picks tools well, but not perfectly: it can act on the active
resume when the user named another one, switch to a template nobody asked for,
or treat a question ("what templates are there?") as a request. The approval
card catches some of this — for destructive tools, and only for users who
kept confirmations on. This check covers the rest.

Before a guarded tool runs, Jev is asked three things about the conversation
and the proposed call, in one request:

  asked   did the user ask for (or agree to) this action?
  args    do the arguments match what they said (template, language, status)?
  target  which of their resumes do they mean?

The answers become a verdict:

  block   clearly not what the user wants — the tool does not run; the model
          is told why and asked to check with the user
  warn    unsure — a destructive tool asks for approval (even when the user
          turned confirmations off) and the card says why
  allow   run as usual

Fail-open: if Jev is unavailable every call is allowed, which is how the loop
behaved before this existed; destructive tools still ask when confirmations
are on.
"""

import json
import logging
from dataclasses import dataclass

from resume import typesafe_engine
from resume.models import JobPosting, Resume
from resume.typesafe_engine import Choice, Noul

logger = logging.getLogger(__name__)

# Tools that change something or spend quota. Read-only tools (list, preview,
# analyze, compare...) are not worth the extra round trip.
GUARDED_TOOLS = {
    "modify_resume", "switch_template", "translate_resume", "delete_resume",
    "revert_last_change", "create_translated_copy", "tailor_resume_for_job",
    "clone_application_resume", "duplicate_resume", "download_resume",
    "update_job", "rescore_job", "create_blank_resume",
}

RESUME_ARGS = ("resume_id", "resume_id_1", "resume_id_2")

# Chosen with `manage.py jev_eval guard`; revisit with the model version.
BLOCK_BELOW = 0.2
WARN_BELOW = 0.5
TARGET_CONFIDENCE = 0.75

UNSPECIFIED = "unspecified"

WARNING_COPY = {
    "en": "I'm not sure this is what you asked for. Check before you continue.",
    "tr": "Bunun istediğiniz şey olduğundan emin değilim. Devam etmeden önce kontrol edin.",
}
TARGET_COPY = {"en": "Resume", "tr": "CV"}


@dataclass
class Verdict:
    action: str  # "allow" | "warn" | "block"
    reason: str = ""
    target_name: str = ""
    suggested_resume_id: int = None
    # Jev's raw answers, for the superuser debug view.
    scores: dict = None

    def tool_message(self):
        """What the model reads when its call was blocked."""
        data = {
            "error": "guardrail_blocked",
            "reason": self.reason,
            "instruction": (
                "This call was NOT run: it does not appear to match what the user "
                "asked for. Do not retry it as is. Ask the user to confirm what "
                "they want, naming the resume and the change."
            ),
        }
        if self.suggested_resume_id:
            data["user_seems_to_mean_resume_id"] = self.suggested_resume_id
        return data


def check(user, ctx, messages, tool, arguments):
    """Judge one proposed call. Never raises; never blocks without Jev."""
    if tool.name not in GUARDED_TOOLS:
        return Verdict("allow")
    target = _target_resume(user, ctx, arguments)
    target_name = _name(target)

    resumes = ctx.get("resumes") or []
    state = {
        "conversation": _conversation(messages),
        "active_resume": ctx["active_resume"].display_name if ctx.get("active_resume") else None,
        "user_resumes": [{"name": r["display_name"], "language": r["language"]} for r in resumes],
        "proposed_action": {
            "tool": tool.name,
            "what_it_does": tool.description,
            "arguments": _readable_arguments(user, arguments, target_name),
        },
    }
    questions = {
        "asked": Noul(
            instructions=(
                "Is `proposed_action` something the user asked for or agreed to in "
                "`conversation`? Yes if they requested it directly, if it is a step of "
                "a larger request they made, if they accepted the assistant's offer "
                "to do it, or if it records news they just shared (an application's "
                "new status). No if they asked for something else, only asked a "
                "question, or declined."
            ),
            criteria={
                "true": "The user wants this action taken now.",
                "false": "The user did not ask for this action.",
            },
        ),
        "args": Noul(
            instructions=(
                "Do the arguments of `proposed_action` match what the user said in "
                "`conversation` — the template, language, status, wording or change "
                "they named? Arguments the user left open, filled with a reasonable "
                "choice, count as a match."
            ),
            criteria={
                "true": "Nothing in the arguments contradicts the user.",
                "false": "An argument differs from what the user said.",
            },
        ),
    }

    options = _resume_options(resumes)
    targets_resume = any(k in arguments for k in RESUME_ARGS) or tool.name in (
        "modify_resume", "switch_template", "translate_resume", "delete_resume",
        "revert_last_change", "create_translated_copy", "duplicate_resume",
        "download_resume",
    )
    if targets_resume and len(options) > 1:
        questions["target"] = Choice(
            instructions=(
                "Which of `user_resumes` does the user want `proposed_action` applied "
                "to, going by `conversation`? Choose 'unspecified' when they did not "
                "single one out, which means the active resume."
            ),
            criteria=options | {UNSPECIFIED: "The user did not name or describe a particular resume."},
        )

    answers = typesafe_engine.ask(state, questions, purpose=f"agent_guard.{tool.name}")
    if answers is None:
        return Verdict("allow", target_name=target_name)

    asked = answers.nouls.get("asked", 1.0)
    args_ok = answers.nouls.get("args", 1.0)
    verdict = Verdict("allow", target_name=target_name)

    pick = answers.choices.get("target")
    if pick and pick.choice != UNSPECIFIED and pick.confidence >= TARGET_CONFIDENCE:
        meant = _option_id(pick.choice)
        if target and meant and meant != target.id:
            verdict = Verdict(
                "block",
                reason="The user appears to mean a different resume than the one this call acts on.",
                target_name=target_name,
                suggested_resume_id=meant,
            )

    if verdict.action == "allow":
        if asked < BLOCK_BELOW:
            verdict = Verdict("block", reason="The user did not ask for this action.",
                              target_name=target_name)
        elif args_ok < BLOCK_BELOW:
            verdict = Verdict("block", reason="The arguments differ from what the user said.",
                              target_name=target_name)
        elif asked < WARN_BELOW or args_ok < WARN_BELOW:
            verdict = Verdict("warn", reason="Unsure this matches the request.",
                              target_name=target_name)

    verdict.scores = {
        "asked": round(asked, 2), "args": round(args_ok, 2),
        "target": f"{pick.choice}@{pick.confidence:.2f}" if pick else None,
    }
    logger.info(
        "Agent guard %s: %s (asked=%.2f args=%.2f target=%s)",
        tool.name, verdict.action, asked, args_ok,
        f"{pick.choice}@{pick.confidence:.2f}" if pick else "-",
    )
    return verdict


def approval_extras(verdict, lang):
    """Lines the approval card adds: which resume, and why it is asking."""
    extras = {}
    if verdict.target_name:
        extras["target"] = f"{TARGET_COPY.get(lang, TARGET_COPY['en'])}: {verdict.target_name}"
    if verdict.action == "warn":
        extras["warning"] = WARNING_COPY.get(lang, WARNING_COPY["en"])
    return extras


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


def _conversation(messages, turns=8, max_chars=1500):
    """The recent user and assistant text; system prompts and tool payloads left out."""
    picked = []
    for message in reversed(messages):
        role = message.get("role")
        text = (message.get("content") or "").strip()
        if role in ("user", "assistant") and text:
            picked.append({"role": role, "text": text[:max_chars]})
            if len(picked) >= turns:
                break
    return list(reversed(picked))


def _target_resume(user, ctx, arguments):
    for key in RESUME_ARGS:
        if arguments.get(key):
            # SECURITY: model output — only the caller's resumes resolve.
            return Resume.objects.filter(pk=arguments[key], user=user).first()
    return ctx.get("active_resume")


def _readable_arguments(user, arguments, target_name):
    """Arguments with ids swapped for the names a person would use."""
    readable = {}
    for key, value in arguments.items():
        if key in RESUME_ARGS and value:
            resume = Resume.objects.filter(pk=value, user=user).first()
            readable[key.replace("_id", "")] = _name(resume) or f"unknown ({value})"
        elif key == "job_id" and value:
            posting = JobPosting.objects.filter(pk=value, user=user).first()
            readable["application"] = posting.label if posting else f"unknown ({value})"
        elif isinstance(value, str):
            readable[key] = value[:600]
        else:
            readable[key] = value
    if target_name and not any(k in arguments for k in RESUME_ARGS):
        readable["resume"] = f"{target_name} (the active resume)"
    return json.loads(json.dumps(readable, default=str))


def _name(resume):
    """Name with language: two language versions of one resume share a title."""
    if not resume:
        return ""
    return f"{resume.display_name} ({resume.language})"


def _resume_options(resumes):
    options = {}
    for r in resumes[:20]:
        label = r["display_name"]
        if r.get("language"):
            label = f"{label} ({r['language']})"
        options[f"resume_{r['id']}"] = label
    return options


def _option_id(option):
    try:
        return int(option.removeprefix("resume_"))
    except (AttributeError, ValueError):
        return None
