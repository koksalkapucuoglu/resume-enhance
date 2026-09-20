"""
Tool registry for the agentic dashboard.

A tool is a plain function with a JSON Schema for its arguments. It returns a
ToolResult that separates two audiences:

    data — facts fed back into the model's context, so it can chain a further
           call or narrate the outcome itself.
    ui   — directives for the frontend. The model never sees these.

The `ui` payloads intentionally reuse the response dicts the agentic dashboard
already knows how to render (`{"type": "preview", ...}` and friends), so the
loop can be introduced without rewriting the frontend's rendering switch.

Business logic still lives on AgentService; tools are the typed, schema-checked
surface the model is allowed to reach it through.
"""

import copy as copy_module
import logging
from dataclasses import dataclass, field
from typing import Callable

from django.conf import settings

from resume.models import Resume

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """What a tool hands back: facts for the model, effects for the browser."""

    data: dict
    ui: list = field(default_factory=list)

    @property
    def is_error(self):
        return "error" in self.data


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable
    destructive: bool = False

    def schema(self):
        """OpenAI function-calling definition, with strict argument checking."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    # strict mode requires every property listed as required and
                    # no extras; optional arguments are expressed as nullable types.
                    "required": list(self.parameters.keys()),
                    "additionalProperties": False,
                },
            },
        }


TOOL_REGISTRY: dict[str, Tool] = {}


def tool(name, description, parameters=None, destructive=False):
    """Register a function as a tool the model may call."""

    def decorator(fn):
        TOOL_REGISTRY[name] = Tool(
            name=name,
            description=description,
            parameters=parameters or {},
            handler=fn,
            destructive=destructive,
        )
        return fn

    return decorator


def tool_schemas():
    """Schemas the model may choose from."""
    return [t.schema() for t in TOOL_REGISTRY.values()]


def get_tool(name):
    return TOOL_REGISTRY.get(name)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

INT_OR_NULL = {"type": ["integer", "null"]}
STR_OR_NULL = {"type": ["string", "null"]}


def _service():
    # Imported lazily: agent_service imports this module for the loop.
    from resume.services.agent_service import agent_service

    return agent_service


def _resume_or_error(user, resume_id, ctx):
    """
    Resolve a resume the caller owns, falling back to the active one.
    SECURITY: always filtered by user — resume_id comes from model output.
    """
    if resume_id:
        resume = Resume.objects.filter(pk=resume_id, user=user).first()
        if resume:
            return resume, None
        return None, ToolResult(
            data={"error": f"No resume with id {resume_id} belongs to this user."}
        )

    active = ctx.get("active_resume")
    if active:
        return active, None
    return None, ToolResult(
        data={"error": "No resume specified and none is active. Ask the user which one."}
    )


def _resume_facts(resume):
    content = resume.content or {}
    return {
        "id": resume.id,
        "name": resume.display_name,
        "language": resume.language,
        "derived_from": resume.derived_from_id,
        "derived_kind": resume.derived_kind,
        "template": resume.template_selector,
        "experience_count": len(content.get("experience", [])),
        "education_count": len(content.get("education", [])),
        "project_count": len(content.get("projects_and_publications", [])),
        "skills": (content.get("user_info") or {}).get("skills", [])[:15],
    }


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------


@tool(
    name="list_resumes",
    description="List all of the user's resumes with id, name, template and last-updated date.",
)
def list_resumes(user, ctx):
    legacy = _service()._exec_list_resumes(user, ctx["lang"])
    rows = legacy.get("data", [])
    # The panel already lists them. Handing the model the whole list too made it
    # narrate a second, redundant copy in prose.
    return ToolResult(
        data={
            "count": len(rows),
            "names": [r.get("display_name") for r in rows][:10],
            "note": (
                "The full list is already on screen. Acknowledge it in one short "
                "sentence and ask what to do next — do not repeat the entries."
            ),
        },
        ui=[legacy],
    )


@tool(
    name="get_resume_details",
    description="Read the contents of one resume: skills, and how many experience, education and project entries it holds.",
    parameters={"resume_id": INT_OR_NULL},
)
def get_resume_details(user, ctx, resume_id=None):
    resume, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error
    return ToolResult(data=_resume_facts(resume))


@tool(
    name="preview_resume",
    description="Show a rendered preview of a resume in the side panel.",
    parameters={"resume_id": INT_OR_NULL},
)
def preview_resume(user, ctx, resume_id=None):
    resume, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error
    legacy = _service()._exec_preview_resume(
        user, {"resume_id": resume.id}, ctx["lang"]
    )
    return ToolResult(
        data={"ok": True, "resume_id": resume.id, "name": resume.display_name},
        ui=[legacy],
    )


@tool(
    name="check_quota",
    description="Report the user's remaining monthly allowances and plan tier.",
)
def check_quota(user, ctx):
    legacy = _service()._exec_check_quota(user, ctx["lang"])
    profile = user.profile
    limits = settings.FREE_TIER_LIMITS
    return ToolResult(
        data={
            "is_pro": profile.is_pro(),
            "resumes_used": user.resumes.count(),
            "resume_limit": limits["resume_count"],
            "imports_left": max(0, limits["import_count"] - profile.import_count),
            "enhancements_left": max(0, limits["enhance_count"] - profile.enhance_count),
            "downloads_left": max(0, limits["download_count"] - profile.download_count),
            "messages_left": max(
                0, limits["agent_message_count"] - profile.agent_message_count
            ),
        },
        ui=[legacy],
    )


@tool(
    name="find_resume",
    description="Search the user's resumes by content — a skill, company, job title, school or keyword.",
    parameters={"query": {"type": "string"}},
)
def find_resume(user, ctx, query):
    legacy = _service()._exec_find_resume(user, {"query": query}, ctx["lang"])
    return ToolResult(data={"matches": legacy.get("data", [])}, ui=[legacy])


@tool(
    name="analyze_resume",
    description="Score a resume's strength across five categories and return improvement suggestions.",
    parameters={"resume_id": INT_OR_NULL},
)
def analyze_resume(user, ctx, resume_id=None):
    resume, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error
    legacy = _service()._exec_analyze_resume(user, {"resume_id": resume.id}, ctx["lang"])
    analysis = legacy.get("analysis") or {}
    return ToolResult(
        data={
            "resume_id": resume.id,
            "overall_score": analysis.get("overall_score"),
            "categories": analysis.get("categories", []),
            "suggestions": analysis.get("suggestions", []),
        },
        ui=[legacy],
    )


@tool(
    name="compare_resumes",
    description="Compare two resumes side by side, highlighting differences and which is stronger.",
    parameters={"resume_id_1": {"type": "integer"}, "resume_id_2": {"type": "integer"}},
)
def compare_resumes(user, ctx, resume_id_1, resume_id_2):
    legacy = _service()._exec_compare_resumes(
        user, {"resume_id_1": resume_id_1, "resume_id_2": resume_id_2}, ctx["lang"]
    )
    if legacy.get("type") == "chat" and not legacy.get("comparison"):
        return ToolResult(data={"summary": legacy.get("message", "")}, ui=[legacy])
    return ToolResult(data={"comparison": legacy.get("comparison", {})}, ui=[legacy])


# ---------------------------------------------------------------------------
# Navigation tools — these hand the user off to another page
# ---------------------------------------------------------------------------


@tool(
    name="download_resume",
    description="Download a resume as PDF. Counts against the user's monthly download quota.",
    parameters={"resume_id": INT_OR_NULL},
)
def download_resume(user, ctx, resume_id=None):
    resume, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error
    if not user.profile.can_download():
        return ToolResult(
            data={"error": "Monthly PDF download limit reached. Pro removes the limit."}
        )
    legacy = _service()._exec_download_resume(
        user, {"resume_id": resume.id}, ctx["lang"]
    )
    return ToolResult(
        data={"ok": True, "resume_id": resume.id, "started": True}, ui=[legacy]
    )


@tool(
    name="edit_resume",
    description="Open a resume in the form editor, in a new tab.",
    parameters={"resume_id": INT_OR_NULL},
)
def edit_resume(user, ctx, resume_id=None):
    resume, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error
    legacy = _service()._exec_edit_resume(user, {"resume_id": resume.id}, ctx["lang"])
    return ToolResult(data={"ok": True, "resume_id": resume.id}, ui=[legacy])


@tool(
    name="create_blank_resume",
    description="Start a new empty resume. Offers a step-by-step chat build or the form editor.",
)
def create_blank_resume(user, ctx):
    if not user.profile.can_create_resume():
        return ToolResult(
            data={
                "error": f"Resume limit reached ({settings.FREE_TIER_LIMITS['resume_count']} on the free plan)."
            }
        )
    legacy = _service()._exec_create_blank(ctx["lang"])
    return ToolResult(data={"ok": True, "awaiting_user_choice": True}, ui=[legacy])


@tool(
    name="start_guided_build",
    description="Begin the step-by-step question-and-answer flow that builds a resume from scratch.",
)
def start_guided_build(user, ctx):
    if not user.profile.can_create_resume():
        return ToolResult(
            data={
                "error": f"Resume limit reached ({settings.FREE_TIER_LIMITS['resume_count']} on the free plan)."
            }
        )
    legacy = _service()._exec_builder_start(ctx["lang"])
    return ToolResult(data={"ok": True, "builder_started": True}, ui=[legacy])


@tool(
    name="upload_resume",
    description=(
        "Ask the user to pick a resume file to import. Default source to 'pdf'; "
        "use 'linkedin' ONLY when the user mentions LinkedIn. Do not ask which "
        "one they mean — plain 'upload' or 'import my CV' means 'pdf'."
    ),
    parameters={"source": {"type": "string", "enum": ["pdf", "linkedin"]}},
)
def upload_resume(user, ctx, source="pdf"):
    service = _service()
    legacy = (
        service._exec_upload_linkedin(ctx["lang"])
        if source == "linkedin"
        else service._exec_upload_resume(ctx["lang"])
    )
    # Deliberately not "ok": the file picker has only been shown. Reporting
    # success here made the model announce an upload that had not happened.
    return ToolResult(
        data={
            "awaiting_file": True,
            "source": source,
            "note": (
                "A file picker was shown to the user. Nothing has been uploaded "
                "yet. Do not claim the import succeeded — say you are waiting "
                "for them to choose a file, or say nothing further."
            ),
        },
        ui=[legacy],
    )


@tool(
    name="duplicate_resume",
    description="Make a copy of a resume.",
    parameters={"resume_id": INT_OR_NULL},
)
def duplicate_resume(user, ctx, resume_id=None):
    resume, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error
    if not user.profile.can_create_resume():
        return ToolResult(
            data={
                "error": f"Resume limit reached ({settings.FREE_TIER_LIMITS['resume_count']} on the free plan)."
            }
        )
    legacy = _service()._exec_duplicate_resume(
        user, {"resume_id": resume.id}, ctx["lang"]
    )
    return ToolResult(data={"ok": True, "source_resume_id": resume.id}, ui=[legacy])


# ---------------------------------------------------------------------------
# Job postings — evaluate a resume against one, in a branch or on the base
# ---------------------------------------------------------------------------


@tool(
    name="evaluate_posting",
    description=(
        "Evaluate a resume against a job posting the user pasted: score, and "
        "which requirements the resume shows, shows partly or lacks. Pass the "
        "posting text verbatim. `target`: 'branch' works in a copy of the "
        "resume kept for this posting (the base stays as it is), 'base' "
        "evaluates the base resume itself. Leave target null unless the user "
        "said which: they are then asked with a card, and you should not ask "
        "again in text. The side panel shows the full table."
    ),
    parameters={
        "posting": {"type": "string"},
        "target": {"type": ["string", "null"], "enum": ["branch", "base", None]},
        "resume_id": INT_OR_NULL,
    },
)
def evaluate_posting(user, ctx, posting, target=None, resume_id=None):
    from resume.services import evaluation_service

    resume, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error
    try:
        job = evaluation_service.add_posting(user, posting, ctx.get("lang", "en"))
    except evaluation_service.EvaluationError as exc:
        return ToolResult(data={"error": str(exc)})

    base = resume.root
    branch = Resume.objects.filter(
        user=user, derived_from=base, derived_kind=Resume.DERIVED_JOB, job_posting=job
    ).first()
    if branch is not None:
        resume = branch  # this posting already has its branch: keep working there
    elif target is None:
        return ToolResult(
            data={
                "asked_user": True,
                "evaluated": False,
                "posting_id": job.pk,
                "posting": job.label,
                "note": (
                    "The posting is saved but NOT evaluated yet. A card below your "
                    "reply asks the user: a branch for this posting, or the base "
                    "resume. Reply with ONE short sentence, in the user's language, "
                    "that points to the card below. Do not ask the question yourself "
                    "and do not describe the options."
                ),
            },
            ui=[{
                "type": "evaluation_target",
                "posting_id": job.pk,
                "posting_label": job.label,
                "resume_id": base.pk,
                "resume_name": base.display_name,
                "message": "",
            }],
        )
    else:
        try:
            resume = (
                evaluation_service.create_branch(base, job) if target == "branch" else base
            )
        except evaluation_service.EvaluationError as exc:
            return ToolResult(data={"error": str(exc)})

    try:
        evaluation, previous = evaluation_service.evaluate(resume, job)
    except evaluation_service.EvaluationError as exc:
        return ToolResult(data={"error": str(exc)})
    panel = evaluation_service.panel(resume, job, evaluation, previous)
    return ToolResult(
        data={
            "posting_id": job.pk,
            "posting": job.label,
            "evaluated_resume_id": resume.pk,
            "is_branch": resume.is_job_branch,
            "score": evaluation.score,
            "requirements": [
                {"label": r["label"], "required": r["required"], "status": r["status"],
                 "evidence": (r.get("evidence") or "")[:160]}
                for r in panel["rows"]
            ],
        },
        ui=[panel],
    )


@tool(
    name="improve_for_posting",
    description=(
        "Start improving the active resume for the active posting's gaps. Pass "
        "the requirement ids (from the context) the user wants, or none for "
        "every gap. Nothing changes yet: the user answers what is needed "
        "(where they did something missing, in their own words) and approves "
        "each change in a card. Never write those bullets yourself with "
        "modify_resume."
    ),
    parameters={"requirement_ids": {"type": ["array", "null"], "items": {"type": "string"}}},
)
def improve_for_posting(user, ctx, requirement_ids=None):
    resume = ctx.get("active_resume")
    posting = ctx.get("active_posting")
    if resume is None or posting is None:
        return ToolResult(data={"error": "No evaluation is open. Evaluate a posting first."})
    return ToolResult(
        data={
            "started": True,
            "note": (
                "Cards below walk the user through it. Reply with one short "
                "sentence in the user's language; do not list the changes."
            ),
        },
        ui=[{
            "type": "improve_start",
            "resume_id": resume.pk,
            "posting_id": posting.pk,
            "requirement_ids": requirement_ids or [],
            "message": "",
        }],
    )


@tool(
    name="list_evaluations",
    description=(
        "Postings evaluated for this resume and its job branches, with the "
        "latest score of each and whether the resume changed since."
    ),
    parameters={"resume_id": INT_OR_NULL},
)
def list_evaluations(user, ctx, resume_id=None):
    from resume.services import evaluation_service

    resume, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error
    return ToolResult(data={"evaluations": evaluation_service.postings_for(resume)})


# ---------------------------------------------------------------------------
# Destructive tools — the loop pauses for approval before these run
# ---------------------------------------------------------------------------


@tool(
    name="promote_branch",
    description=(
        "Replace the base resume with one of its job branches: the base's "
        "content becomes the branch's, with no merge. The base keeps a restore "
        "point, so this can be undone from its history. Only when the user "
        "asks to make the branch their main resume."
    ),
    parameters={"resume_id": INT_OR_NULL},
    destructive=True,
)
def promote_branch(user, ctx, resume_id=None):
    from resume.services import evaluation_service

    branch, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error
    if not branch.is_job_branch:
        return ToolResult(data={"error": "That resume is not a job branch."})
    base = evaluation_service.promote(branch)
    return ToolResult(
        data={"ok": True, "base_resume_id": base.pk, "branch_resume_id": branch.pk},
        ui=[{"type": "branch_promoted", "base": evaluation_service.resume_meta(base),
             "branch_id": branch.pk, "message": ""}],
    )


@tool(
    name="modify_resume",
    description=(
        "Apply a natural-language edit to a resume's content: update fields, add or "
        "remove experience, education, skills or projects, rewrite descriptions, or "
        "tailor it for a role. Pass the user's instruction verbatim."
    ),
    parameters={"resume_id": INT_OR_NULL, "instruction": {"type": "string"}},
    destructive=True,
)
def modify_resume(user, ctx, instruction, resume_id=None):
    resume, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error
    legacy = _service()._exec_modify_resume(
        user, {"resume_id": resume.id}, ctx["lang"], instruction, resume
    )
    if legacy.get("type") != "modify_resume":
        return ToolResult(
            data={"error": legacy.get("message", "The edit could not be applied.")},
            ui=[legacy],
        )
    return ToolResult(
        data={
            "ok": True,
            "resume_id": resume.id,
            "changes_summary": legacy.get("changes_summary", ""),
        },
        ui=[legacy],
    )


@tool(
    name="switch_template",
    description="Change which layout a resume renders with.",
    parameters={
        "resume_id": INT_OR_NULL,
        "template": {"type": "string", "enum": list(settings.TEMPLATE_SELECTOR_HTML_MAP)},
    },
    destructive=True,
)
def switch_template(user, ctx, template, resume_id=None):
    resume, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error
    legacy = _service()._exec_switch_template(
        user, {"resume_id": resume.id, "template": template}, ctx["lang"], resume
    )
    return ToolResult(
        data={"ok": True, "resume_id": resume.id, "template": template}, ui=[legacy]
    )


@tool(
    name="translate_resume",
    description=(
        "Translate a resume IN PLACE, replacing its content — the original wording "
        "is gone (recoverable only through the change history). Prefer "
        "create_translated_copy when the user wants to keep both languages."
    ),
    parameters={"resume_id": INT_OR_NULL, "target_language": {"type": "string"}},
    destructive=True,
)
def translate_resume(user, ctx, target_language, resume_id=None):
    resume, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error
    legacy = _service()._exec_translate_resume(
        user,
        {"resume_id": resume.id, "target_language": target_language},
        ctx["lang"],
        resume,
    )
    return ToolResult(
        data={"ok": True, "resume_id": resume.id, "language": target_language},
        ui=[legacy],
    )


@tool(
    name="create_translated_copy",
    description=(
        "Create a linked copy of a resume in another language, keeping the original "
        "untouched. Use this when the user wants the same resume available in both "
        "languages. Translated copies do not count against the resume limit."
    ),
    parameters={
        "resume_id": INT_OR_NULL,
        "target_language": {"type": "string", "enum": ["en", "tr"]},
    },
    destructive=True,
)
def create_translated_copy(user, ctx, target_language, resume_id=None):
    source, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error

    if source.is_job_branch:
        # A copy would hang off the base (derived_from is one level deep), so it
        # would look like a language version of the main resume while carrying
        # this posting's edits. Translate in place, or promote first.
        return ToolResult(
            data={
                "error": (
                    "This is a job branch, so it cannot get its own language "
                    "version. Translate it in place with translate_resume, or "
                    "promote it to the main resume first and translate that."
                ),
                "base_resume_id": source.derived_from_id,
            }
        )

    target_language = Resume.normalize_language(target_language)
    if source.language == target_language:
        return ToolResult(
            data={"error": f"This resume is already in {target_language}."}
        )

    family = source.language_family()
    existing = family.filter(language=target_language).first()
    if existing:
        return ToolResult(
            data={
                "error": f"A {target_language} version already exists (id {existing.id}).",
                "existing_resume_id": existing.id,
            }
        )

    copy = Resume.objects.create(
        user=user,
        title=f"{source.title} ({target_language.upper()})",
        content=copy_module.deepcopy(source.content),
        template_selector=source.template_selector,
        language=source.language,
        derived_from=source.root,
        derived_kind=Resume.DERIVED_TRANSLATION,
    )
    legacy = _service()._exec_translate_resume(
        user,
        {"resume_id": copy.id, "target_language": target_language},
        ctx["lang"],
        copy,
    )
    copy.refresh_from_db()
    copy.language = target_language
    copy.save(update_fields=["language"])

    return ToolResult(
        data={
            "ok": True,
            "source_resume_id": source.id,
            "new_resume_id": copy.id,
            "language": target_language,
            "counts_against_limit": False,
        },
        ui=[
            {
                "type": "preview",
                "resume_id": copy.id,
                "resume_name": copy.display_name,
                "message": "",
            }
        ],
    )


@tool(
    name="list_language_versions",
    description="Show which language versions exist for a resume and how they are linked.",
    parameters={"resume_id": INT_OR_NULL},
)
def list_language_versions(user, ctx, resume_id=None):
    resume, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error
    return ToolResult(
        data={
            "root_id": resume.root.id,
            "versions": [
                {
                    "id": r.id,
                    "name": r.display_name,
                    "language": r.language,
                    "is_original": r.derived_from_id is None,
                    "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M"),
                }
                for r in resume.language_family()
            ],
        }
    )


@tool(
    name="delete_resume",
    description=(
        "Permanently delete an ENTIRE resume. Never use this to remove a single "
        "experience, skill or section — that is modify_resume."
    ),
    parameters={"resume_id": {"type": "integer"}},
    destructive=True,
)
def delete_resume(user, ctx, resume_id):
    resume = Resume.objects.filter(pk=resume_id, user=user).first()
    if not resume:
        return ToolResult(
            data={"error": f"No resume with id {resume_id} belongs to this user."}
        )
    name = resume.display_name
    resume.delete()
    return ToolResult(
        data={"ok": True, "deleted_resume_id": resume_id, "name": name},
        ui=[{"type": "resume_deleted", "resume_id": resume_id}],
    )


@tool(
    name="revert_last_change",
    description="Undo the most recent change to a resume, restoring the previous version.",
    parameters={"resume_id": INT_OR_NULL},
    destructive=True,
)
def revert_last_change(user, ctx, resume_id=None):
    from resume.services import revision_service

    resume, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error
    revision = revision_service.history(resume).first()
    if not revision:
        return ToolResult(data={"error": "This resume has no recorded changes to undo."})
    revision_service.restore(resume, revision)
    return ToolResult(
        data={"ok": True, "resume_id": resume.id, "restored_from": revision.pk},
        ui=[
            {
                "type": "modify_resume",
                "resume_id": resume.id,
                "resume_name": resume.display_name,
                "message": "",
            }
        ],
    )
