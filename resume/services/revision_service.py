"""
Restore points for resumes.

Every mutation of Resume.content or Resume.template_selector records the
*previous* state first, so a single revision can be restored to undo exactly
one step. Call `snapshot()` before writing, never after — the revision stores
what the resume looked like before the change, not the result of it.
"""

import copy
import logging

from django.conf import settings

from resume.models import ResumeRevision

logger = logging.getLogger(__name__)


def snapshot(resume, source, summary="", tool_name=""):
    """
    Record the resume's current state as a restore point.

    Must be called BEFORE mutating the resume. Returns the created revision,
    or None if nothing was recorded (a resume with no content yet has no
    meaningful state to restore).
    """
    if not resume.pk or not resume.content:
        return None

    revision = ResumeRevision.objects.create(
        resume=resume,
        content=copy.deepcopy(resume.content),
        template_selector=resume.template_selector,
        source=source,
        summary=summary or "",
        tool_name=tool_name or "",
    )
    prune(resume)
    return revision


def prune(resume):
    """Drop revisions beyond the account's retention limit. Pro keeps all."""
    if resume.user.profile.is_pro():
        return 0

    limit = settings.FREE_TIER_LIMITS["revision_history"]
    stale_ids = list(
        ResumeRevision.objects.filter(resume=resume)
        .values_list("pk", flat=True)[limit:]
    )
    if not stale_ids:
        return 0
    ResumeRevision.objects.filter(pk__in=stale_ids).delete()
    return len(stale_ids)


def restore(resume, revision):
    """
    Roll the resume back to a revision.

    The current state is snapshotted first, so restoring is itself undoable.
    Returns the revision created for the pre-restore state.
    """
    if revision.resume_id != resume.pk:
        raise ValueError("Revision does not belong to this resume")

    undo_point = snapshot(
        resume,
        source=ResumeRevision.SOURCE_REVERT,
        summary=f"Before restoring the version from {revision.created_at:%Y-%m-%d %H:%M}",
    )
    resume.content = copy.deepcopy(revision.content)
    resume.template_selector = revision.template_selector
    resume.save(update_fields=["content", "template_selector", "updated_at"])
    logger.info("Resume %s restored to revision %s", resume.pk, revision.pk)
    return undo_point


def history(resume):
    """Revisions for a resume, newest first."""
    return ResumeRevision.objects.filter(resume=resume)
