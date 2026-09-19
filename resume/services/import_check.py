"""
Checking an AI import against the file it came from.

The parser (OpenAI) reads a PDF and writes our JSON. It usually gets it right,
but it can add what is not there: a skill the person never listed, a metric in
a bullet, a degree forced into "Bachelor" because the prompt asks for one of
three names. This module holds the result to two standards before it is saved:

1. Shape. `resume_content.conform` drops keys we do not store and coerces
   types, so an unexpected field cannot break the editor or a template.
2. Evidence. Each value is checked against the source text. Contact details
   are checked by code (an email is either in the text or it is not); the rest
   are Jev questions — "does the document support this?" — asked together.

Nothing is deleted. An unsupported value stays where it is and is flagged for
the person to look at; the editor highlights it. The one exception is a
contact detail the parser got wrong while the right one is in the text: that
is replaced by the verbatim value Jev picked, and flagged as corrected.

If Jev is unavailable the import goes through unchecked — the review says so.
"""

import logging
import re

from django.utils import timezone

from resume import typesafe_engine
from resume.services import resume_content
from resume.typesafe_engine import Choice, Noul

logger = logging.getLogger(__name__)

# Enough for a long multi-page resume; the state is one request either way.
MAX_SOURCE_CHARS = 20000

# A claim whose probability of being supported falls below this is flagged.
# Chosen with `manage.py jev_eval import` on jev-1.13.0: supported values scored
# 0.74 and up, unsupported ones 0.35 and down. Revisit with the model version.
SUPPORT_THRESHOLD = 0.5

# How sure the pick must be before a wrong contact value is replaced.
PICK_CONFIDENCE = 0.8

NONE = "none"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?:/[^\s,;)]*)?"
)

SUPPORTED = {
    "true": "The document states this, possibly reworded, abbreviated or translated.",
    "false": "The document does not contain this; it was added, changed or guessed.",
}

# Labels for the editor panel and chat, by field kind.
FIELD_LABELS = {
    "en": {
        "full_name": "Name", "email": "Email", "phone": "Phone",
        "linkedin": "LinkedIn", "github": "GitHub", "skill": "Skill",
        "title": "Job title", "company": "Company", "dates": "Dates",
        "bullet": "Experience bullet", "school": "School", "degree": "Degree",
        "field_of_study": "Field of study", "years": "Years",
        "project": "Project", "project_description": "Project description",
        "link": "Project link",
    },
    "tr": {
        "full_name": "Ad soyad", "email": "E-posta", "phone": "Telefon",
        "linkedin": "LinkedIn", "github": "GitHub", "skill": "Yetenek",
        "title": "Unvan", "company": "Şirket", "dates": "Tarihler",
        "bullet": "Deneyim maddesi", "school": "Okul", "degree": "Derece",
        "field_of_study": "Bölüm", "years": "Yıllar",
        "project": "Proje", "project_description": "Proje açıklaması",
        "link": "Proje linki",
    },
}


def label(kind, lang="en"):
    return FIELD_LABELS.get(lang, FIELD_LABELS["en"]).get(kind, kind)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def run(extracted, source_text):
    """
    Shape and check an import. Returns (content, review).

    `review` is stored on the resume:
      status   "checked" | "skipped" (Jev unavailable)
      flags    [{path, kind, value, p, status, original?}]  — see `open_flags`
      dropped  dotted names of keys the parser returned that we do not store
    """
    content = resume_content.normalize(extracted)
    content, dropped = resume_content.conform(content)
    source = (source_text or "")[:MAX_SOURCE_CHARS]

    claims = _claims(content)
    contact = _contact_checks(content, source)

    questions = {}
    for i, claim in enumerate(claims):
        questions[f"c{i}"] = Noul(instructions=claim["question"], criteria=SUPPORTED)
    for i, check in enumerate(contact):
        if check["candidates"]:
            questions[f"p{i}"] = Choice(
                instructions=check["question"],
                criteria={c: None for c in check["candidates"]}
                | {NONE: "None of these is the person's own " + check["what"] + "."},
            )

    review = {
        "status": "skipped",
        "flags": [],
        "dropped": dropped,
        "checked_at": timezone.now().isoformat(timespec="seconds"),
    }

    answers = typesafe_engine.ask({"document": source}, questions, purpose="import_check")
    if answers is None:
        # Contact checks need no model: report what code alone can tell.
        review["flags"] = [_unsupported(c) for c in contact]
        return content, review

    flags = []
    for i, claim in enumerate(claims):
        p = answers.nouls.get(f"c{i}")
        if p is not None and p < SUPPORT_THRESHOLD:
            flags.append(
                {"path": claim["path"], "kind": claim["kind"], "value": claim["value"],
                 "p": round(p, 2), "status": "unsupported"}
            )
    for i, check in enumerate(contact):
        pick = answers.choices.get(f"p{i}")
        if pick and pick.choice != NONE and pick.confidence >= PICK_CONFIDENCE:
            _set_path(content, check["path"], _normalise_contact(check["kind"], pick.choice))
            flags.append(
                {"path": check["path"], "kind": check["kind"],
                 "value": _get_path(content, check["path"]), "original": check["value"],
                 "p": round(pick.confidence, 2), "status": "corrected"}
            )
        else:
            flags.append(_unsupported(check))

    review.update(status="checked", flags=flags, model=answers.model)
    logger.info(
        "Import check: %d claims, %d contact checks, %d flagged, %d keys dropped",
        len(claims), len(contact), len(flags), len(dropped),
    )
    return content, review


def summary(review):
    """What the upload response and the chat need to know."""
    review = review or {}
    return {
        "checked": review.get("status") == "checked",
        "flagged": len(review.get("flags") or []),
        "dropped": list(review.get("dropped") or []),
    }


def open_flags(resume, lang="en"):
    """
    Flags still worth showing: the flagged value is still where it was.

    Once the person edits or removes a value its flag no longer applies, so the
    list shrinks as they work through it — no separate "resolved" bookkeeping.
    """
    review = resume.import_review or {}
    shown = []
    content = resume.content or {}
    for flag in review.get("flags") or []:
        if not _still_there(content, flag.get("path", ""), flag.get("value"), flag.get("kind")):
            continue
        shown.append(dict(flag, label=label(flag.get("kind"), lang),
                          field_id=_field_id(flag.get("path", ""))))
    return shown


def _still_there(content, path, value, kind=None):
    current = _get_path(content, path)
    if current == value:
        return True
    # The editor stores a month as "2022-03-01" where the import wrote "2022-03".
    if kind == "dates" and current:
        return str(current)[:7] == str(value)[:7]
    # A list item moves when an earlier one is removed; it is still unchecked.
    if path.endswith("]"):
        parent = _get_path(content, path.rsplit("[", 1)[0])
        return isinstance(parent, list) and value in parent
    return False


# --------------------------------------------------------------------------
# Claims for Jev
# --------------------------------------------------------------------------


def _q(text):
    return " ".join(text.split())


def _claims(content):
    claims = []

    def add(path, kind, value, question):
        if value not in (None, "", []):
            claims.append({"path": path, "kind": kind, "value": value, "question": _q(question)})

    info = content.get("user_info") or {}
    add("user_info.full_name", "full_name", info.get("full_name"),
        f"Is '{info.get('full_name')}' the name of the person this document is about?")
    for i, skill in enumerate(info.get("skills") or []):
        add(f"user_info.skills[{i}]", "skill", skill,
            f"Does the document name the skill '{skill}'? An abbreviation, another "
            "spelling or a translation of the same skill counts; a related skill the "
            "document does not name does not.")

    for i, job in enumerate(content.get("experience") or []):
        base = f"experience[{i}]"
        title, company = job.get("title") or "", job.get("company") or ""
        at = f" at '{company}'" if company else ""
        add(f"{base}.title", "title", title,
            f"Does the document give '{title}' as a job title the person held{at}?")
        add(f"{base}.company", "company", company,
            f"Does the document name '{company}' as a place the person worked?")
        if job.get("start_date"):
            end = "the present" if job.get("current_role") or not job.get("end_date") else job["end_date"]
            add(f"{base}.start_date", "dates", job["start_date"],
                f"The resume says the role '{title}'{at} ran from {job['start_date']} to "
                f"{end} (dates are YYYY-MM). Is that consistent with the dates the document "
                "gives for that role? When the document gives only a year, any month of "
                "that year is consistent.")
        for j, bullet in enumerate(job.get("description") or []):
            add(f"{base}.description[{j}]", "bullet", bullet,
                f"Is everything this sentence claims about the person's work stated in the "
                f"document: '{bullet}'? Rewording, shortening, splitting and translation "
                "are fine. A number, tool, scope or result the document does not give is not.")

    for i, edu in enumerate(content.get("education") or []):
        base = f"education[{i}]"
        school = edu.get("school") or ""
        add(f"{base}.school", "school", school,
            f"Does the document name '{school}' as a school the person attended?")
        add(f"{base}.degree", "degree", edu.get("degree"),
            f"Does the document support that the person's education at '{school}' was a "
            f"'{edu.get('degree')}' degree? A standard name for the same degree counts "
            "(BSc as Bachelor, Yüksek Lisans as Master); a different level does not.")
        add(f"{base}.field_of_study", "field_of_study", edu.get("field_of_study"),
            f"Does the document support that the person studied '{edu.get('field_of_study')}' "
            f"at '{school}'? An equivalent or translated name counts.")
        if edu.get("start_year") or edu.get("end_year"):
            year_field = "start_year" if edu.get("start_year") else "end_year"
            add(f"{base}.{year_field}", "years", edu.get(year_field),
                f"The resume says the education at '{school}' ran from "
                f"{edu.get('start_year') or '?'} to {edu.get('end_year') or '?'}. "
                "Is that consistent with the document?")

    for i, project in enumerate(content.get("projects_and_publications") or []):
        base = f"projects_and_publications[{i}]"
        add(f"{base}.name", "project", project.get("name"),
            f"Does the document mention a project or publication called "
            f"'{project.get('name')}', or clearly the same one under another name?")
        add(f"{base}.description", "project_description", project.get("description"),
            f"Is everything this project description claims stated in the document: "
            f"'{project.get('description')}'? Rewording and translation are fine; a "
            "number, tool or result the document does not give is not.")

    return claims


# --------------------------------------------------------------------------
# Contact details, checked by code first
# --------------------------------------------------------------------------

_CONTACT = (
    ("user_info.email", "email", _EMAIL_RE, "email address"),
    ("user_info.phone", "phone", _PHONE_RE, "phone number"),
    ("user_info.linkedin", "linkedin", _URL_RE, "LinkedIn profile URL"),
    ("user_info.github", "github", _URL_RE, "GitHub profile URL"),
)


def _contact_checks(content, source):
    """Contact values not found verbatim in the source, with candidates to pick from."""
    checks = []
    fields = list(_CONTACT) + [
        (f"projects_and_publications[{i}].link", "link", _URL_RE, "link for this project")
        for i in range(len(content.get("projects_and_publications") or []))
    ]
    for path, kind, pattern, what in fields:
        value = _get_path(content, path)
        if not value or _in_source(kind, value, source):
            continue
        candidates = _candidates(kind, pattern, source)
        checks.append({
            "path": path, "kind": kind, "value": value, "what": what,
            "candidates": candidates,
            "question": _q(
                f"Which of these is the {what} of the person this document is about"
                + (f" (the project '{_get_path(content, path.rsplit('.', 1)[0] + '.name')}')"
                   if kind == "link" else "")
                + "? Pick 'none' if it is not among them."
            ),
        })
    return checks


def _unsupported(check):
    return {"path": check["path"], "kind": check["kind"], "value": check["value"],
            "p": None, "status": "unsupported"}


def _squash(text):
    return re.sub(r"\s+", "", (text or "").lower())


def _url_key(value):
    value = _squash(value)
    value = re.sub(r"^https?://", "", value)
    value = re.sub(r"^www\.", "", value)
    return value.rstrip("/")


def _in_source(kind, value, source):
    if kind == "email":
        return _squash(value) in _squash(source)
    if kind == "phone":
        digits = re.sub(r"\D", "", value)
        return len(digits) >= 7 and digits in re.sub(r"[\s().+-]", "", source)
    # URLs: PDFs break them across lines and drop the scheme.
    return _url_key(value) in _squash(source)


def _candidates(kind, pattern, source):
    found = []
    for match in pattern.findall(source):
        span = match.strip().rstrip(".")
        if kind in ("linkedin", "github") and kind not in span.lower():
            continue
        if kind == "link" and "@" in span:
            continue
        if span and span not in found:
            found.append(span)
    return found[:12]


def _normalise_contact(kind, value):
    if kind in ("linkedin", "github", "link") and not value.lower().startswith("http"):
        return "https://" + value
    return value


# --------------------------------------------------------------------------
# Paths: "experience[0].description[2]"
# --------------------------------------------------------------------------

_STEP = re.compile(r"([A-Za-z_]+)(?:\[(\d+)\])?")


def _steps(path):
    for name, index in _STEP.findall(path):
        yield name, int(index) if index else None


def _get_path(content, path):
    node = content
    for name, index in _steps(path):
        if not isinstance(node, dict):
            return None
        node = node.get(name)
        if index is not None:
            if not isinstance(node, list) or index >= len(node):
                return None
            node = node[index]
    return node


def _set_path(content, path, value):
    *parents, (last, last_index) = list(_steps(path))
    node = content
    for name, index in parents:
        node = node[name] if index is None else node[name][index]
    if last_index is None:
        node[last] = value
    else:
        node[last][last_index] = value


_FORM_PREFIX = {
    "experience": "experience",
    "education": "education",
    "projects_and_publications": "project",
}


def _field_id(path):
    """The editor input that holds the value at `path`."""
    steps = list(_steps(path))
    if not steps:
        return ""
    section, index = steps[0]
    if section == "user_info" and len(steps) > 1:
        return f"id_{steps[1][0]}"
    if section in _FORM_PREFIX and index is not None and len(steps) > 1:
        return f"id_{_FORM_PREFIX[section]}-{index}-{steps[1][0]}"
    return ""
