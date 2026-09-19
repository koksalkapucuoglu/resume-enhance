"""Filling the single placeholder in a UI string: "{n} postings", "{job} is tracked"."""

import re

from django import template

register = template.Library()

_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


@register.filter
def fill(text, value):
    """Replace the first {placeholder} in `text` with `value`."""
    return _PLACEHOLDER.sub(str(value), str(text or ""), count=1)


@register.filter
def get_item(mapping, key):
    """`mapping[key]` for a dict in a template, or "" when absent."""
    return (mapping or {}).get(key, "") if hasattr(mapping, "get") else ""
