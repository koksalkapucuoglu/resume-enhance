"""
One canonical shape for resume content, and one way to render it.

Resume JSON reaches the database from four places — the form editor, PDF
import, LinkedIn import and the agent — and each used to hand the templates a
slightly different shape. Three separate context builders then papered over the
differences in three different ways, which is why the same resume could render
correctly in the editor preview and come out mangled in the downloaded PDF.

Everything that stores content should pass it through `normalize`, and
everything that renders it should use `build_context`.
"""

import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m", "%Y/%m/%d", "%d.%m.%Y", "%Y")


def parse_date(value):
    """
    Turn whatever a date field holds into a date, or None.

    Django's `date` filter renders nothing for a string, so "2022-01" would
    silently disappear from a PDF while a real date object printed fine — the
    reason dates showed in the editor preview but not in the download.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    logger.debug("Unparseable date value: %r", value)
    return None


def year_of(*values):
    """First usable year among the given values."""
    for value in values:
        if value in (None, ""):
            continue
        parsed = parse_date(value)
        if parsed:
            return parsed.year
        try:
            return int(str(value)[:4])
        except (TypeError, ValueError):
            continue
    return None


def as_list(value):
    """
    Coerce a field that should be a list into one.

    The AI sometimes returns skills or bullet points as a single string. A
    string left in place is iterable, so `join` would split it into individual
    characters — "P, y, t, h, o, n" instead of "Python".
    """
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        # `str(None)` is "None", which would end up in the rendered resume.
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    text = str(value)
    separator = "\n" if "\n" in text else ","
    return [part.strip() for part in text.split(separator) if part.strip()]


def normalize(content):
    """
    Return content in the shape every reader expects.

    Idempotent, and safe on partial or malformed input — the point is that a
    resume written by the agent is as usable by the form editor as one typed
    into it.
    """
    content = dict(content or {})
    user_info = dict(content.get("user_info") or {})
    user_info["skills"] = as_list(user_info.get("skills"))
    content["user_info"] = user_info

    experience = []
    for entry in content.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        entry["description"] = as_list(entry.get("description"))
        experience.append(entry)
    content["experience"] = experience

    education = []
    for entry in content.get("education") or []:
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        # The form stores years, imports and the agent store dates. Keep both
        # populated so neither reader finds an empty field.
        start_year = year_of(entry.get("start_year"), entry.get("start_date"))
        end_year = year_of(entry.get("end_year"), entry.get("end_date"))
        if start_year:
            entry["start_year"] = start_year
        if end_year:
            entry["end_year"] = end_year
        education.append(entry)
    content["education"] = education

    projects = [
        dict(p) for p in content.get("projects_and_publications") or []
        if isinstance(p, dict)
    ]
    content["projects_and_publications"] = projects
    return content


def build_context(content, generation_date=None):
    """
    Template context for the PDF/preview templates.

    Dates are parsed here rather than in the template, so `|date:"M Y"` has
    something to format regardless of how the value was stored.
    """
    content = normalize(content)

    experience = []
    for entry in content["experience"]:
        entry = dict(entry)
        entry["start_date"] = parse_date(entry.get("start_date"))
        entry["end_date"] = parse_date(entry.get("end_date"))
        experience.append(entry)

    return {
        "user_data": content["user_info"],
        "education_data": content["education"],
        "experience_data": experience,
        "project_data": content["projects_and_publications"],
        "generation_date": (
            generation_date or datetime.now().strftime("%Y-%m-%d")
        ),
    }
