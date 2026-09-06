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
from django.urls import reverse

from resume.models import JobPosting, Resume, ResumeRevision

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
    pro_only: bool = False

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


def tool(name, description, parameters=None, destructive=False, pro_only=False):
    """Register a function as a tool the model may call."""

    def decorator(fn):
        TOOL_REGISTRY[name] = Tool(
            name=name,
            description=description,
            parameters=parameters or {},
            handler=fn,
            destructive=destructive,
            pro_only=pro_only,
        )
        return fn

    return decorator


def tool_schemas(user=None):
    """
    Schemas the model may choose from.

    Pro-only tools are withheld from free accounts rather than offered and then
    refused: it keeps the catalogue short — selection accuracy drops as the list
    grows — and stops the assistant repeatedly proposing something the user
    cannot run. The handlers still check the tier themselves.
    """
    is_pro = bool(user and user.profile.is_pro())
    return [t.schema() for t in TOOL_REGISTRY.values() if is_pro or not t.pro_only]


def pro_tool_names():
    return [name for name, t in TOOL_REGISTRY.items() if t.pro_only]


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
# Job matching and application tracking
# ---------------------------------------------------------------------------


def _premium_required(user, feature):
    """Job tracking is the paid tier. Returns an error result, or None."""
    if user.profile.is_pro():
        return None
    return ToolResult(
        data={
            "error": f"{feature} is a Pro feature.",
            "upgrade_required": True,
        }
    )


@tool(
    name="match_job",
    description=(
        "Score how well a resume fits a job posting the user pasted, and list "
        "the requirements it does not evidence. Saves the posting so it can be "
        "tracked. Pass the posting text verbatim."
    ),
    parameters={"description": {"type": "string"}, "resume_id": INT_OR_NULL},
    pro_only=True,
)
def match_job(user, ctx, description, resume_id=None):
    blocked = _premium_required(user, "Job matching")
    if blocked:
        return blocked
    resume, error = _resume_or_error(user, resume_id, ctx)
    if error:
        return error

    from resume.services import job_service

    result = job_service.analyze_match(resume, description, ctx.get("lang", "en"))
    if "error" in result:
        return ToolResult(data=result)

    # One record per posting: pasting the same advert again re-measures the
    # application rather than opening a second one.
    digest = JobPosting.fingerprint(description)
    posting = JobPosting.objects.filter(user=user, content_hash=digest).first() if digest else None
    is_new = posting is None
    if is_new:
        posting = JobPosting(
            user=user,
            description=description[: job_service.MAX_DESCRIPTION_CHARS],
            content_hash=digest,
        )
    posting.title = result["title"]
    posting.company = result["company"]
    posting.tags = result["tags"]
    posting.resume = resume
    previous = posting.record_score(
        result["score"], resume.id, result["missing_keywords"]
    )
    posting.save()

    return ToolResult(
        data={
            "job_id": posting.id,
            "resume_id": resume.id,
            "score": result["score"],
            "previous_score": previous,
            "is_new_application": is_new,
            "matched_keywords": result["matched_keywords"],
            "missing_keywords": result["missing_keywords"],
            "verdict": result["verdict"],
            "suggestions": result["suggestions"],
        },
        ui=[
            {
                "type": "job_match",
                "job_id": posting.id,
                "job_label": posting.label,
                "resume_id": resume.id,
                "resume_name": resume.display_name,
                "score": result["score"],
                "previous_score": previous,
                "matched_keywords": result["matched_keywords"],
                "missing_keywords": result["missing_keywords"],
                "suggestions": result["suggestions"],
                "message": "",
            }
        ],
    )


@tool(
    name="tailor_resume_for_job",
    description=(
        "Rewrite a resume for one saved job posting, as a variant that leaves "
        "the original untouched. Running it again for the same job updates that "
        "same variant rather than making another. Run match_job first."
    ),
    parameters={"job_id": {"type": "integer"}, "resume_id": INT_OR_NULL},
    destructive=True,
    pro_only=True,
)
def tailor_resume_for_job(user, ctx, job_id, resume_id=None):
    blocked = _premium_required(user, "Tailoring a resume to a job")
    if blocked:
        return blocked

    posting = JobPosting.objects.filter(pk=job_id, user=user).first()
    if not posting:
        return ToolResult(data={"error": f"No saved job with id {job_id}."})

    from resume.services import job_service, resume_content, revision_service

    # Always tailor from the base resume. Tailoring a previous variant is what
    # made titles compound into "Main — Role — Role".
    requested = (
        Resume.objects.filter(pk=resume_id, user=user).first() if resume_id else None
    )
    source = (requested or posting.resume or ctx.get("active_resume"))
    if not source:
        return ToolResult(data={"error": "No resume to tailor. Ask which one."})
    source = source.root

    existing = Resume.objects.filter(
        user=user,
        derived_from=source,
        derived_kind=Resume.DERIVED_TAILORED,
        job_postings=posting,
    ).first()

    result = job_service.tailor_content(
        source, posting.description, posting.missing_keywords
    )
    if "error" in result:
        return ToolResult(data=result)

    content = resume_content.normalize(result["content"])
    title = f"{source.title} → {posting.title}"[:255]

    if existing:
        # Update in place, with a restore point, instead of adding another CV.
        revision_service.snapshot(
            existing,
            source=ResumeRevision.SOURCE_AGENT,
            tool_name="tailor_resume_for_job",
            summary=result["changes_summary"],
        )
        existing.content = content
        existing.title = title
        existing.save(update_fields=["content", "title", "updated_at"])
        variant, created = existing, False
    else:
        variant = Resume.objects.create(
            user=user,
            title=title,
            content=content,
            template_selector=source.template_selector,
            language=source.language,
            derived_from=source,
            derived_kind=Resume.DERIVED_TAILORED,
        )
        created = True

    posting.resume = variant
    posting.save(update_fields=["resume", "updated_at"])

    return ToolResult(
        data={
            "ok": True,
            "resume_id": variant.id,
            "created_new_variant": created,
            "job_id": posting.id,
            "source_resume_id": source.id,
            "counts_against_limit": False,
            "changes_summary": result["changes_summary"],
            "next": (
                "Offer to re-run match_job on the same posting so the user can "
                "see whether the score moved."
            ),
        },
        ui=[
            {
                "type": "preview",
                "resume_id": variant.id,
                "resume_name": variant.display_name,
                "message": "",
            }
        ],
    )


@tool(
    name="rescore_job",
    description=(
        "Re-measure a saved application against the resume currently attached "
        "to it, and report the change. Use after editing or tailoring so the "
        "user can see whether the score moved."
    ),
    parameters={"job_id": {"type": "integer"}},
    pro_only=True,
)
def rescore_job(user, ctx, job_id):
    blocked = _premium_required(user, "Job matching")
    if blocked:
        return blocked

    posting = JobPosting.objects.filter(pk=job_id, user=user).first()
    if not posting:
        return ToolResult(data={"error": f"No saved job with id {job_id}."})

    resume = posting.resume or ctx.get("active_resume")
    if not resume:
        return ToolResult(
            data={"error": "No resume is attached to this application."}
        )

    from resume.services import job_service

    result = job_service.analyze_match(
        resume, posting.description, ctx.get("lang", "en")
    )
    if "error" in result:
        return ToolResult(data=result)

    previous = posting.record_score(
        result["score"], resume.id, result["missing_keywords"]
    )
    posting.save(update_fields=["match_score", "score_history", "missing_keywords",
                                "updated_at"])

    delta = None if previous is None else result["score"] - previous
    return ToolResult(
        data={
            "job_id": posting.id,
            "resume_id": resume.id,
            "score": result["score"],
            "previous_score": previous,
            "change": delta,
            "missing_keywords": result["missing_keywords"],
            "suggestions": result["suggestions"],
        },
        ui=[
            {
                "type": "job_match",
                "job_id": posting.id,
                "job_label": posting.label,
                "resume_id": resume.id,
                "resume_name": resume.display_name,
                "score": result["score"],
                "previous_score": previous,
                "matched_keywords": result["matched_keywords"],
                "missing_keywords": result["missing_keywords"],
                "suggestions": result["suggestions"],
                "message": "",
            }
        ],
    )


@tool(
    name="list_jobs",
    description="List the job postings the user is tracking, with status, score and which resume was used.",
    parameters={"status": STR_OR_NULL},
    pro_only=True,
)
def list_jobs(user, ctx, status=None):
    blocked = _premium_required(user, "Job tracking")
    if blocked:
        return blocked

    postings = JobPosting.objects.filter(user=user).select_related("resume")
    if status:
        postings = postings.filter(status=status)
    jobs = [
        {
            "id": p.id,
            "title": p.title,
            "company": p.company,
            "status": p.status,
            "score": p.match_score,
            "first_score": (p.score_history or [{}])[0].get("score"),
            "measurements": len(p.score_history or []),
            "tags": p.tags,
            "resume_id": p.resume_id,
            "resume_name": p.resume.display_name if p.resume else None,
            "updated_at": p.updated_at.strftime("%Y-%m-%d"),
        }
        for p in postings[:50]
    ]
    return ToolResult(
        data={"jobs": jobs, "count": len(jobs)},
        ui=[{"type": "job_list", "jobs": jobs, "message": ""}],
    )


@tool(
    name="update_job",
    description=(
        "Update a tracked job: change its status (saved, applied, interview, "
        "offer, rejected) or the resume attached to it."
    ),
    parameters={
        "job_id": {"type": "integer"},
        "status": STR_OR_NULL,
        "resume_id": INT_OR_NULL,
    },
    pro_only=True,
)
def update_job(user, ctx, job_id, status=None, resume_id=None):
    blocked = _premium_required(user, "Job tracking")
    if blocked:
        return blocked

    posting = JobPosting.objects.filter(pk=job_id, user=user).first()
    if not posting:
        return ToolResult(data={"error": f"No saved job with id {job_id}."})

    fields = []
    if status:
        valid = dict(JobPosting.STATUS_CHOICES)
        if status not in valid:
            return ToolResult(
                data={"error": f"Status must be one of: {', '.join(valid)}."}
            )
        posting.status = status
        fields.append("status")
    if resume_id:
        resume = Resume.objects.filter(pk=resume_id, user=user).first()
        if not resume:
            return ToolResult(data={"error": f"No resume with id {resume_id}."})
        posting.resume = resume
        fields.append("resume")

    if not fields:
        return ToolResult(data={"error": "Nothing to update — give a status or a resume."})

    posting.save(update_fields=fields + ["updated_at"])
    return ToolResult(
        data={
            "ok": True,
            "job_id": posting.id,
            "status": posting.status,
            "resume_id": posting.resume_id,
        }
    )


@tool(
    name="resume_groups",
    description=(
        "Show which resume the user sends for which kind of role, grouped by "
        "the tags of the jobs they applied to. Answers 'which CV do I use for "
        "C++ jobs?'."
    ),
    pro_only=True,
)
def resume_groups(user, ctx):
    blocked = _premium_required(user, "Resume grouping")
    if blocked:
        return blocked

    from resume.services import job_service

    groups = job_service.resume_groups(user)
    return ToolResult(
        data={"groups": groups},
        ui=[{"type": "resume_groups", "groups": groups, "message": ""}],
    )


# ---------------------------------------------------------------------------
# Destructive tools — the loop pauses for approval before these run
# ---------------------------------------------------------------------------


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
