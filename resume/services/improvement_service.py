"""
Improving a resume for the gaps an evaluation found.

Three steps, each one visible to the person:

1. plan — for each selected requirement, decide what can be done without
   inventing anything. A requirement the resume shows partly, in a bullet, is
   rewritten *in that bullet* (evaluation rows say where: `evidence_at`), which
   is often not the latest job. A requirement with no evidence in the work
   history becomes a question: "Where did you do this, and what did you do?"
   Jev pre-selects the likeliest job; the person decides, or says they did not.
2. draft — OpenAI writes the bullets from the existing bullet and the
   person's own words only. Jev then checks each new line against those
   sources; a line that says more than they support is flagged in the card.
3. apply — the accepted changes (edited or not) are written in one step
   with a restore point, and the resume is measured again.

The draft is held server-side under a token together with the content hash it
was written against, so an edit in between cannot be overwritten by a stale
draft.
"""

import copy
import json
import logging
import uuid

from django.conf import settings
from django.core.cache import cache

from resume import typesafe_engine
from resume.models import ResumeRevision
from resume.openai_engine import send_openai_message
from resume.services import diff_service, evaluation_service, revision_service
from resume.services.import_check import SUPPORTED
from resume.typesafe_engine import Choice, Noul

logger = logging.getLogger(__name__)

DRAFT_TTL_SECONDS = 30 * 60
# Below this, a new line is flagged as saying more than its sources.
SUPPORTED_FROM = 0.5
NONE = "none"


class ImprovementError(Exception):
    """Something the person can act on: a stale draft, a limit, a busy service."""


def _job_label(job):
    return f"{job.get('title') or '?'} · {job.get('company') or '?'}"


def _experiences(content):
    return [e for e in (content.get("experience") or []) if isinstance(e, dict)]


# --------------------------------------------------------------------------
# 1. Plan
# --------------------------------------------------------------------------


def plan(resume, posting, evaluation, requirement_ids=None):
    """
    What to do for each selected gap.

    Returns {"rewrites": [...], "questions": [...]}:
      rewrites   {req_id, label, text, entry, bullet, job, bullet_text}
      questions  {req_id, label, text, suggested_entry, jobs: [{entry, label}]}
    With no ids, every gap (partial or missing) is selected.
    """
    content = resume.content or {}
    jobs = _experiences(content)
    rows = evaluation_service.table(posting, evaluation)
    selected = [
        r for r in rows
        if r["status"] != "covered" and (not requirement_ids or r["id"] in requirement_ids)
    ]

    rewrites, questions = [], []
    for row in selected:
        at = row.get("evidence_at") or {}
        entry, bullet = at.get("entry"), at.get("bullet")
        if (
            row["status"] == "partial"
            and at.get("section") == "experience"
            and bullet is not None
            and entry is not None
            and entry < len(jobs)
        ):
            bullets = jobs[entry].get("description") or []
            if isinstance(bullets, list) and bullet < len(bullets):
                rewrites.append({
                    "req_id": row["id"], "label": row["label"], "text": row["text"],
                    "entry": entry, "bullet": bullet,
                    "job": _job_label(jobs[entry]), "bullet_text": bullets[bullet],
                })
                continue
        questions.append({"req_id": row["id"], "label": row["label"], "text": row["text"]})

    job_options = [{"entry": i, "label": _job_label(job)} for i, job in enumerate(jobs)]
    suggestions = _suggest_jobs(content, jobs, questions)
    for question in questions:
        question["jobs"] = job_options
        question["suggested_entry"] = suggestions.get(question["req_id"])
    return {"rewrites": rewrites, "questions": questions}


def _suggest_jobs(content, jobs, questions):
    """Jev's guess at the job each unanswered requirement belongs to; {} if none."""
    if not jobs or not questions:
        return {}
    options = {f"j{i}": _job_label(job) for i, job in enumerate(jobs)} | {
        NONE: "None of these roles is likely."
    }
    state = {"experience": [
        {"id": f"j{i}", "role": _job_label(job), "bullets": job.get("description") or []}
        for i, job in enumerate(jobs)
    ]}
    asked = {
        q["req_id"]: Choice(
            instructions=(
                "In which role in `experience` did this person most likely do this: "
                f"{json.dumps(q['text'], ensure_ascii=False)}? Judge from what each role involved."
            ),
            criteria=options,
        )
        for q in questions
    }
    answers = typesafe_engine.ask(state, asked, purpose="improve.suggest_job")
    if answers is None:
        return {}
    suggested = {}
    for req_id, pick in answers.choices.items():
        if pick.choice != NONE and pick.confidence >= 0.4:
            suggested[req_id] = int(pick.choice[1:])
    return suggested


# --------------------------------------------------------------------------
# 2. Draft
# --------------------------------------------------------------------------


def draft(user, resume, posting, rewrites, answers, lang="en"):
    """
    Write the changes; nothing is saved yet.

    `rewrites` are plan() rewrites; `answers` are {req_id, entry, fact} for
    questions the person answered (entry None or empty fact = skipped).
    Returns {"token", "changes": [{id, req_id, label, entry, bullet, job,
    before, after, words, unsupported}]}.
    """
    content = resume.content or {}
    jobs = _experiences(content)
    requirements = {r["id"]: r for r in posting.requirements}

    # A note the person added to a rewrite ("I led the move to Kafka") feeds
    # that rewrite; answers to questions become new bullets.
    notes = {
        str(a.get("req_id")): str(a.get("fact") or "").strip()[:500]
        for a in answers if a.get("entry") in (None, "") and a.get("fact")
    }
    rewrite_ids = {r["req_id"] for r in rewrites}
    items = []
    for rewrite in rewrites:
        items.append({
            "key": f"k{len(items)}", "req_id": rewrite["req_id"], "entry": rewrite["entry"],
            "bullet": rewrite["bullet"], "current": rewrite["bullet_text"],
            "fact": notes.get(rewrite["req_id"], ""),
        })
    for answer in answers:
        if answer.get("req_id") in rewrite_ids:
            continue
        try:
            entry = int(answer.get("entry"))
        except (TypeError, ValueError):
            continue  # "I did not do this", or nothing chosen
        fact = str(answer.get("fact") or "").strip()
        if not fact or not 0 <= entry < len(jobs) or answer.get("req_id") not in requirements:
            continue
        items.append({
            "key": f"k{len(items)}", "req_id": answer["req_id"], "entry": entry,
            "bullet": None, "current": "", "fact": fact[:500],
        })
    if not items:
        raise ImprovementError("Nothing to change: choose a role and say what you did.")

    language = {"tr": "Turkish", "en": "English"}.get(resume.language, "English")
    written = _write(content, posting, items, requirements, language)
    if written is None:
        raise ImprovementError("The draft could not be written. Please try again.")

    changes = []
    for item in items:
        text = (written.get(item["key"]) or "").strip()
        # A full stop or a space is not a change worth reviewing.
        if not text or _same(text, item["current"]):
            continue
        job = jobs[item["entry"]]
        change = {
            "id": item["key"], "req_id": item["req_id"],
            "label": requirements.get(item["req_id"], {}).get("label", ""),
            "entry": item["entry"], "bullet": item["bullet"], "job": _job_label(job),
            "before": item["current"], "after": text, "fact": item["fact"],
        }
        change["words"] = diff_service.word_segments(item["current"], text) if item["current"] else []
        changes.append(change)
    if not changes:
        raise ImprovementError("The draft came back unchanged. Try adding a line about what you did.")

    _flag_unsupported(changes)
    token = uuid.uuid4().hex
    cache.set(_key(user, token), {
        "resume_id": resume.pk, "posting_id": posting.pk,
        "content_hash": evaluation_service.content_hash(content),
        "changes": changes,
    }, DRAFT_TTL_SECONDS)
    return {"token": token, "changes": changes}


def _same(a, b):
    squash = lambda t: " ".join((t or "").split()).rstrip(" .;").lower()
    return squash(a) == squash(b)


def _write(content, posting, items, requirements, language):
    jobs = _experiences(content)
    brief = [
        {
            "key": item["key"],
            "requirement": requirements.get(item["req_id"], {}).get("text", ""),
            "role": _job_label(jobs[item["entry"]]),
            "role_bullets": jobs[item["entry"]].get("description") or [],
            "current_bullet": item["current"] or None,
            "what_they_did": item["fact"] or None,
        }
        for item in items
    ]
    meta_prompt = f"""
    You improve resume bullets for one job posting, one bullet per item.

    Hard rules:
    - Use ONLY what the item gives you: `current_bullet` and `what_they_did`.
      Never add a technology, number, scale, team size or result that is not
      in them. Inventing experience is the one unacceptable outcome.
    - With `current_bullet`: rewrite it so the work it describes shows the
      requirement more clearly, using `what_they_did` if given. Keep every
      fact it already states. If the sources give nothing more to say about
      the requirement, return `current_bullet` exactly as it is — never pad it
      with a purpose or outcome ("to ensure efficiency…") it does not state.
    - Without `current_bullet`: write ONE new bullet from `what_they_did`.
    - A bullet describes work done ("Built…", "Migrated…"), never a claim of
      knowledge ("Familiar with…", "Knowledge of…").
    - One sentence, in {language}, in the style of `role_bullets`.

    Respond ONLY with JSON: {{"bullets": {{"<key>": "the bullet"}}}}
    """.strip()
    raw = send_openai_message(
        user_message=json.dumps({"posting": posting.label, "items": brief}, ensure_ascii=False),
        meta_prompt=meta_prompt,
        is_json=True,
        temperature=0.2,
        max_tokens=1200,
    )
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Improvement draft unusable (%d chars)", len(raw or ""))
        return None
    bullets = parsed.get("bullets") if isinstance(parsed, dict) else None
    return {str(k): str(v) for k, v in bullets.items()} if isinstance(bullets, dict) else None


def _flag_unsupported(changes):
    """Mark a new line that says more than the bullet and the person's words."""
    questions = {
        change["id"]: Noul(
            instructions=(
                f"Is everything this new resume line claims stated in its sources — the "
                f"original line {json.dumps(change['before'] or None, ensure_ascii=False)} and "
                f"what the person said {json.dumps(change['fact'] or None, ensure_ascii=False)}? "
                f"New line: {json.dumps(change['after'], ensure_ascii=False)}. Rewording is "
                "fine; a technology, number, scale or result neither source gives is not."
            ),
            criteria=SUPPORTED,
        )
        for change in changes
    }
    answers = typesafe_engine.ask(
        {"changes": [{"id": c["id"], "role": c["job"]} for c in changes]},
        questions, purpose="improve.check",
    )
    for change in changes:
        p = answers.nouls.get(change["id"]) if answers else None
        change["unsupported"] = p is not None and p < SUPPORTED_FROM


# --------------------------------------------------------------------------
# 3. Apply
# --------------------------------------------------------------------------


def apply(user, resume, token, accepted):
    """
    Write the accepted changes. `accepted` is [{id, text}] — text as the person
    left it in the card. Returns (evaluation, previous, posting).
    """
    stored = cache.get(_key(user, token))
    if not stored or stored["resume_id"] != resume.pk:
        raise ImprovementError("This draft has expired. Please create it again.")
    if stored["content_hash"] != evaluation_service.content_hash(resume.content):
        raise ImprovementError("The resume changed after this draft. Please create it again.")

    by_id = {change["id"]: change for change in stored["changes"]}
    chosen = []
    for item in accepted or []:
        change = by_id.get(item.get("id"))
        text = str(item.get("text") or "").strip()
        if change and text:
            chosen.append((change, text[:600]))
    if not chosen:
        raise ImprovementError("No change was selected.")

    from resume.models import JobPosting

    posting = JobPosting.objects.get(pk=stored["posting_id"], user=user)
    content = copy.deepcopy(resume.content)
    jobs = _experiences(content)
    # Rewrites first, by position; additions after, so indexes stay valid.
    for change, text in sorted(chosen, key=lambda c: c[0]["bullet"] is None):
        job = jobs[change["entry"]]
        bullets = job.get("description") or []
        if not isinstance(bullets, list):
            bullets = [bullets]
        if change["bullet"] is None:
            bullets.append(text)
        elif change["bullet"] < len(bullets):
            bullets[change["bullet"]] = text
        job["description"] = bullets

    labels = ", ".join(c["label"] for c, _ in chosen if c["label"])[:180]
    revision_service.snapshot(
        resume,
        source=ResumeRevision.SOURCE_AGENT,
        summary=f"Improved for {posting.short_label}: {labels}"[:255],
        tool_name="improve_for_posting",
    )
    resume.content = content
    resume.save(update_fields=["content", "updated_at"])
    cache.delete(_key(user, token))
    evaluation, previous = evaluation_service.evaluate(resume, posting)
    return evaluation, previous, posting


def _key(user, token):
    return f"improve_draft_{user.pk}_{token}"


def can_draft(user):
    return user.profile.can_enhance()


def charge(user):
    """A draft is one AI enhancement on the free plan."""
    profile = user.profile
    if not profile.is_pro():
        profile.enhance_count += 1
        profile.save(update_fields=["enhance_count"])


def enhance_limit():
    return settings.FREE_TIER_LIMITS["enhance_count"]
