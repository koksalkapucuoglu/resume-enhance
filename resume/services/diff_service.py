"""
Field-level diff between two resume content payloads.

Produces a flat list of change entries so the UI can render "before → after"
rows without knowing the resume schema. List sections (experience, education,
projects) are matched by identity rather than position, so reordering an entry
is not reported as an edit of two unrelated ones.
"""

KIND_ADDED = "added"
KIND_REMOVED = "removed"
KIND_CHANGED = "changed"

# Scalar fields of user_info, in display order.
_USER_INFO_FIELDS = [
    ("full_name", "Full Name"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("address", "Address"),
    ("linkedin", "LinkedIn"),
    ("github", "GitHub"),
]

# section key -> (display label, identity fields, [(field, label), ...])
_LIST_SECTIONS = {
    "experience": (
        "Experience",
        ("title", "company"),
        [
            ("title", "Role"),
            ("company", "Company"),
            ("start_date", "Start Date"),
            ("end_date", "End Date"),
            ("current_role", "Current Role"),
            ("description", "Description"),
        ],
    ),
    "education": (
        "Education",
        ("school", "degree"),
        [
            ("school", "School"),
            ("degree", "Degree"),
            ("field_of_study", "Field of Study"),
            ("start_date", "Start"),
            ("end_date", "End"),
            ("start_year", "Start Year"),
            ("end_year", "End Year"),
        ],
    ),
    "projects_and_publications": (
        "Projects",
        ("name",),
        [("name", "Name"), ("description", "Description"), ("link", "Link")],
    ),
}


# Wording for rendering a diff. Kept next to the diff itself so the agentic
# panel (conversation language) and the editor's history modal (interface
# language) render the same thing from one source.
DIFF_COPY = {
    "en": {
        "title": "Change history",
        "see_last_change": "See last change",
        "restore": "Restore",
        "restored": "Restored.",
        "no_changes": "No changes",
        "before": "Before",
        "after": "After",
        "added": "added",
        "removed": "removed",
        "changed": "changed",
        "template": "Template",
    },
    "tr": {
        "title": "Değişiklik geçmişi",
        "see_last_change": "Son değişikliği gör",
        "restore": "Geri yükle",
        "restored": "Geri yüklendi.",
        "no_changes": "Değişiklik yok",
        "before": "Önce",
        "after": "Sonra",
        "added": "eklendi",
        "removed": "kaldırıldı",
        "changed": "değişti",
        "template": "Şablon",
    },
}


def copy(lang):
    """Localized labels for rendering a diff."""
    return DIFF_COPY.get(lang, DIFF_COPY["en"])


def _as_text(value):
    """Render any resume value as comparable, displayable text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return "\n".join(_as_text(v) for v in value)
    return str(value).strip()


def _identity(entry, keys):
    return tuple(_as_text(entry.get(k, "")).lower() for k in keys)


def _entry_label(entry, keys):
    parts = [_as_text(entry.get(k, "")) for k in keys]
    return " · ".join(p for p in parts if p) or "(untitled)"


def _change(section, item, field, kind, before="", after=""):
    return {
        "section": section,
        "item": item,
        "field": field,
        "kind": kind,
        "before": before,
        "after": after,
    }


def _diff_user_info(before, after):
    changes = []
    old_info = before.get("user_info") or {}
    new_info = after.get("user_info") or {}

    for key, label in _USER_INFO_FIELDS:
        old_val, new_val = _as_text(old_info.get(key)), _as_text(new_info.get(key))
        if old_val == new_val:
            continue
        kind = (
            KIND_ADDED if not old_val else KIND_REMOVED if not new_val else KIND_CHANGED
        )
        changes.append(
            _change("Personal", "", label, kind, before=old_val, after=new_val)
        )

    old_skills = [_as_text(s) for s in (old_info.get("skills") or []) if _as_text(s)]
    new_skills = [_as_text(s) for s in (new_info.get("skills") or []) if _as_text(s)]
    old_lookup = {s.lower(): s for s in old_skills}
    new_lookup = {s.lower(): s for s in new_skills}

    for key, label in new_lookup.items():
        if key not in old_lookup:
            changes.append(_change("Skills", "", label, KIND_ADDED, after=label))
    for key, label in old_lookup.items():
        if key not in new_lookup:
            changes.append(_change("Skills", "", label, KIND_REMOVED, before=label))

    return changes


def _diff_list_section(section_key, before, after):
    label, id_keys, fields = _LIST_SECTIONS[section_key]
    old_entries = [e for e in (before.get(section_key) or []) if isinstance(e, dict)]
    new_entries = [e for e in (after.get(section_key) or []) if isinstance(e, dict)]

    old_by_id = {}
    for entry in old_entries:
        old_by_id.setdefault(_identity(entry, id_keys), []).append(entry)

    changes = []
    matched = set()

    for entry in new_entries:
        ident = _identity(entry, id_keys)
        bucket = old_by_id.get(ident)
        if not bucket:
            changes.append(
                _change(
                    label,
                    _entry_label(entry, id_keys),
                    "",
                    KIND_ADDED,
                    after=_as_text(entry.get("description")) or "",
                )
            )
            continue

        counterpart = bucket.pop(0)
        matched.add(id(counterpart))
        for field, field_label in fields:
            old_val = _as_text(counterpart.get(field))
            new_val = _as_text(entry.get(field))
            if old_val != new_val:
                changes.append(
                    _change(
                        label,
                        _entry_label(entry, id_keys),
                        field_label,
                        KIND_CHANGED,
                        before=old_val,
                        after=new_val,
                    )
                )

    for entry in old_entries:
        if id(entry) in matched:
            continue
        ident = _identity(entry, id_keys)
        if old_by_id.get(ident):
            changes.append(
                _change(
                    label,
                    _entry_label(entry, id_keys),
                    "",
                    KIND_REMOVED,
                    before=_as_text(entry.get("description")) or "",
                )
            )
            old_by_id[ident].remove(entry)

    return changes


def diff_resume_content(before, after):
    """
    Compare two resume content dicts.

    Returns a flat list of {section, item, field, kind, before, after} entries,
    empty when the two are equivalent.
    """
    before = before or {}
    after = after or {}

    changes = _diff_user_info(before, after)
    for section_key in _LIST_SECTIONS:
        changes.extend(_diff_list_section(section_key, before, after))
    return changes


def summarize(changes):
    """One-line human summary of a diff, e.g. '2 added, 1 changed'."""
    if not changes:
        return "No changes"
    counts = {}
    for change in changes:
        counts[change["kind"]] = counts.get(change["kind"], 0) + 1
    order = [(KIND_ADDED, "added"), (KIND_CHANGED, "changed"), (KIND_REMOVED, "removed")]
    return ", ".join(f"{counts[k]} {word}" for k, word in order if counts.get(k))
