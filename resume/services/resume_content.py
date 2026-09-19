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


# The shape a resume is stored in. MCP publishes it as the tool input schema,
# declared strictly so a calling model is told about a misspelled key by its own
# validation; imports are held to it by `conform`.
CONTENT_SCHEMA = {
    "type": "object",
    "description": "The full resume. Sections you omit are stored empty.",
    "properties": {
        "user_info": {
            "type": "object",
            "properties": {
                "full_name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "github": {"type": "string"},
                "linkedin": {"type": "string"},
                "skills": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        "experience": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "location": {"type": "string"},
                    "start_date": {
                        "type": "string",
                        "description": "YYYY-MM, e.g. 2022-03.",
                    },
                    "end_date": {
                        "type": ["string", "null"],
                        "description": "YYYY-MM, or null while still there.",
                    },
                    "current_role": {"type": "boolean"},
                    "description": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "One bullet per item, not one blob.",
                    },
                },
                "additionalProperties": False,
            },
        },
        "education": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "school": {"type": "string"},
                    "degree": {"type": "string"},
                    "field_of_study": {"type": "string"},
                    "start_year": {"type": "integer"},
                    "end_year": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
        "projects_and_publications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "link": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "focus_areas": {
            "type": "object",
            "description": (
                "What the person is working on now, in their own terms — one "
                "short line per area, written from what they have actually "
                "been doing. Printed above Education, and only when `include` "
                "is true: store the lines even when it is false, and let the "
                "person decide in ResuStack whether the section appears."
            ),
            "properties": {
                "include": {"type": "boolean"},
                "items": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


# Keys kept at the top level besides the schema's sections: the language the
# resume is written in travels with the content on import.
_EXTRA_TOP_LEVEL = {"language": {"type": "string"}}

# Dropped by `conform` but not lost: `normalize` has already folded them into
# the years the schema keeps, so they are not worth reporting.
_FOLDED = {"education[].start_date", "education[].end_date"}


def conform(content):
    """
    Hold content to CONTENT_SCHEMA, returning (content, dropped).

    For AI imports: the parser is asked for our shape but sometimes answers with
    more — an address, a certifications section, a nested object where a string
    belongs. Unknown keys are dropped and wrong types coerced, so nothing
    downstream meets a shape it does not expect. `dropped` lists the dropped
    keys as dotted paths without indices ("user_info.address"), never values.

    Run after `normalize`, which folds education dates into the years the
    schema keeps.
    """
    schema = dict(CONTENT_SCHEMA, properties={**CONTENT_SCHEMA["properties"], **_EXTRA_TOP_LEVEL})
    dropped = set()
    cleaned = _conform(content if isinstance(content, dict) else {}, schema, "", dropped)
    return cleaned, sorted(dropped - _FOLDED)


def _conform(value, schema, path, dropped):
    kind = schema.get("type")
    kinds = kind if isinstance(kind, list) else [kind]

    if value is None:
        return None if "null" in kinds else _empty(kinds[0])

    if "object" in kinds:
        if not isinstance(value, dict):
            dropped.add(path or "(root)")
            return {}
        properties = schema.get("properties", {})
        result = {}
        for key, item in value.items():
            child = f"{path}.{key}" if path else key
            if key in properties:
                result[key] = _conform(item, properties[key], child, dropped)
            else:
                dropped.add(child)
        return result

    if "array" in kinds:
        items = schema.get("items", {})
        if items.get("type") == "string":
            return as_list(value)
        if not isinstance(value, list):
            value = [value] if isinstance(value, dict) else []
        return [
            _conform(item, items, f"{path}[]", dropped)
            for item in value
            if isinstance(item, dict) or items.get("type") != "object"
        ]

    if "boolean" in kinds:
        if isinstance(value, str):
            return value.strip().lower() in ("true", "yes", "1")
        return bool(value)

    if "integer" in kinds:
        year = year_of(value) if not isinstance(value, bool) else None
        return year if year is not None else None

    # string
    if isinstance(value, list):
        return "\n".join(str(v).strip() for v in value if v is not None and str(v).strip())
    if isinstance(value, dict):
        dropped.add(path)
        return ""
    return str(value).strip()


def _empty(kind):
    return {"object": {}, "array": [], "boolean": False, "string": ""}.get(kind)


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
    content["focus_areas"] = normalize_focus_areas(content.get("focus_areas"))
    return content


def normalize_focus_areas(value):
    """
    The "what I'm working on" section: lines plus whether to show them.

    Kept as a dict rather than a bare list because not everyone wants the
    section printed — the lines are stored either way, so turning it off and
    back on does not lose them. Accepts the older shapes (a bare list, or a
    single string) so content written before this section existed still loads.
    """
    include = False
    items = []
    if isinstance(value, dict):
        include = bool(value.get("include"))
        items = as_list(value.get("items"))
    elif value:
        items = as_list(value)
        include = bool(items)
    return {"include": include, "items": [i for i in items if str(i).strip()]}


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

    focus_areas = content["focus_areas"]

    return {
        "user_data": content["user_info"],
        "education_data": content["education"],
        # The template renders the section only when the user asked for it.
        "focus_areas": focus_areas["items"] if focus_areas["include"] else [],
        "focus_areas_data": focus_areas,
        "experience_data": experience,
        "project_data": content["projects_and_publications"],
        "generation_date": (
            generation_date or datetime.now().strftime("%Y-%m-%d")
        ),
    }
