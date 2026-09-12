"""
The tools ResuStack exposes over MCP.

Handlers take `user` and `request` as keyword arguments and return
`(text, data)`. Raise `ToolError` for anything the model could fix by asking
differently; let everything else raise and the registry will report it.
"""

from django.conf import settings

from .registry import tool

TEMPLATE_DESCRIPTIONS = {
    "faangpath-simple": "Single column, classic. Safe for ATS screening.",
    "modern-sidebar": "Two columns: contact and skills in a left sidebar.",
}


@tool(
    name="list_templates",
    description=(
        "List the PDF templates a resume can be rendered with. Use this before "
        "setting a template so you pass a key that exists."
    ),
    input_schema={"type": "object", "additionalProperties": False},
    output_schema={
        "type": "object",
        "properties": {
            "templates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["key", "description"],
                },
            }
        },
        "required": ["templates"],
    },
)
def list_templates(user, request=None):
    templates = [
        {"key": key, "description": TEMPLATE_DESCRIPTIONS.get(key, "")}
        for key in sorted(settings.TEMPLATE_SELECTOR_HTML_MAP)
    ]
    names = ", ".join(t["key"] for t in templates)
    return f"{len(templates)} templates: {names}.", {"templates": templates}


@tool(
    name="check_quota",
    description=(
        "What this account has left: resume slots, PDF downloads, tracked "
        "applications, and whether it is on the paid tier."
    ),
    input_schema={"type": "object", "additionalProperties": False},
)
def check_quota(user, request=None):
    profile = user.profile
    profile.reset_if_new_month()
    limits = settings.FREE_TIER_LIMITS
    pro = profile.is_pro()

    def left(used, limit):
        return "unlimited" if pro else max(0, limit - used)

    data = {
        "tier": "pro" if pro else "free",
        "resumes_left": left(
            user.resumes.filter(derived_from__isnull=True).count(),
            limits["resume_count"],
        ),
        "downloads_left": left(profile.download_count, limits["download_count"]),
        "applications_left": left(
            user.job_postings.count(),
            limits["application_count"],
        ),
    }
    if pro:
        return "This account is on the paid tier: no limits apply.", data
    slots = data["resumes_left"]
    return (
        f"Free tier. {slots} resume {'slot' if slots == 1 else 'slots'}, "
        f"{data['downloads_left']} downloads and "
        f"{data['applications_left']} tracked applications left.",
        data,
    )
