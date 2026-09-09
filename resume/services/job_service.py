"""
Matching a resume against a job description, and tailoring it to one.

The scoring prompt is deliberately conservative: it reports what the resume
already evidences and what the posting asks for that is missing. It never
invents experience — a resume that wins an ATS screen and then falls apart in
the interview is worse than one that never got through.
"""

import json
import logging

from resume.models import JobPosting
from resume.openai_engine import send_openai_message

logger = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 8000

# Panel wording, served with the data so it follows the conversation language.
JOB_COPY = {
    "en": {
        "match_title": "Job match",
        "match_score": "Match score",
        "matched": "Already covered",
        "missing": "Not evidenced",
        "suggestions": "What to fix first",
        "none": "None",
        "jobs_title": "Applications",
        "jobs_empty": "No applications tracked yet.",
        "groups_title": "Which resume for which role",
        "groups_empty": "Apply to a few jobs and I'll show the pattern here.",
        "one_use": "1 application",
        "many_uses": "applications",
        "status": {
            "saved": "Saved", "applied": "Applied", "interview": "Interview",
            "offer": "Offer", "rejected": "Rejected",
        },
    },
    "tr": {
        "match_title": "İlan eşleşmesi",
        "match_score": "Eşleşme puanı",
        "matched": "Zaten karşılanan",
        "missing": "Belgelenmemiş",
        "suggestions": "Önce şunları düzelt",
        "none": "Yok",
        "jobs_title": "Başvurular",
        "jobs_empty": "Henüz takip edilen başvuru yok.",
        "groups_title": "Hangi rol için hangi CV",
        "groups_empty": "Birkaç ilana başvurun, deseni burada göstereyim.",
        "one_use": "1 başvuru",
        "many_uses": "başvuru",
        "status": {
            "saved": "Kaydedildi", "applied": "Başvuruldu", "interview": "Mülakat",
            "offer": "Teklif", "rejected": "Reddedildi",
        },
    },
}


def copy(lang):
    """Localized labels for the job panels."""
    return JOB_COPY.get(lang, JOB_COPY["en"])


def _resume_text(resume):
    return json.dumps(resume.content or {}, ensure_ascii=False)


def cached_analysis(user_id, fingerprint):
    """A recent analysis of this posting, if one is waiting to be applied."""
    from django.core.cache import cache

    return cache.get(f"job_match_{user_id}_{fingerprint}")


def remember_analysis(user_id, fingerprint, result, seconds=900):
    """
    Hold an analysis briefly.

    When a posting collides with one already tracked we ask the user what they
    meant; their answer should not cost a second look at the same text.
    """
    from django.core.cache import cache

    cache.set(f"job_match_{user_id}_{fingerprint}", result, seconds)


def analyze_snapshot(content, description, lang="en"):
    """Measure a frozen resume payload, rather than a Resume row."""
    return _analyze(content, description, lang)


def analyze_match(resume, description, lang="en"):
    return _analyze(resume.content, description, lang)


def _analyze(content, description, lang="en"):
    """
    Score how well a resume answers a posting.

    Returns a dict with score, matched/missing keywords, tags and a short
    verdict, or {"error": ...} when the model's answer is unusable.
    """
    description = (description or "").strip()[:MAX_DESCRIPTION_CHARS]
    if len(description) < 40:
        return {"error": "The job description is too short to analyse."}

    meta_prompt = """
    You compare a resume against a job posting for a candidate deciding whether
    to apply and what to fix first.

    Rules:
    - Judge ONLY what the resume evidences. Never assume unstated experience.
    - "missing_keywords" are requirements in the posting with no support in the
      resume. Skills the resume demonstrates through experience count as
      present even if the exact word is absent.
    - Write "verdict" and "suggestions" in REPLY_LANGUAGE, given below — the
      person reading them is the one chatting, not the resume.
    - Leave keywords as they appear in the posting or resume; do not translate
      a technology name.

    Respond ONLY with JSON:
    {
      "score": 0-100,
      "matched_keywords": ["..."],
      "missing_keywords": ["..."],
      "title": "job title from the posting",
      "company": "company name, or empty string",
      "verdict": "two sentences on the fit",
      "suggestions": ["concrete change 1", "concrete change 2", "concrete change 3"]
    }
    """.strip()

    language_name = {"tr": "Turkish", "en": "English"}.get(lang, "English")
    user_message = (
        f"REPLY_LANGUAGE: {language_name}\n\n"
        f"RESUME (JSON):\n{json.dumps(content or {}, ensure_ascii=False)}\n\n"
        f"JOB POSTING:\n{description}"
    )
    raw = send_openai_message(
        user_message=user_message,
        meta_prompt=meta_prompt,
        is_json=True,
        temperature=0,
        max_tokens=1500,
    )

    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("Job match returned unparseable JSON: %s", raw[:200])
        return {"error": "Could not analyse this posting. Please try again."}

    try:
        score = int(parsed.get("score", 0))
    except (TypeError, ValueError):
        score = 0

    return {
        "score": max(0, min(100, score)),
        "matched_keywords": [str(k) for k in parsed.get("matched_keywords", [])][:20],
        "missing_keywords": [str(k) for k in parsed.get("missing_keywords", [])][:20],
        "title": str(parsed.get("title") or "Untitled role")[:255],
        "company": str(parsed.get("company") or "")[:255],
        "verdict": str(parsed.get("verdict") or ""),
        "suggestions": [str(x) for x in parsed.get("suggestions", [])][:5],
    }


def tailor_content(resume, description, missing_keywords=None):
    """
    Rewrite a resume's content for one posting.

    Returns the new content dict, or {"error": ...}. Only rewrites and
    reorders what is already there — the caller saves this to a *copy*, never
    over the original.
    """
    description = (description or "").strip()[:MAX_DESCRIPTION_CHARS]
    if len(description) < 40:
        return {"error": "The job description is too short to tailor against."}

    meta_prompt = """
    You adapt an existing resume for one specific job posting.

    Hard rules:
    - NEVER invent employers, titles, dates, degrees, metrics or technologies
      the candidate has not claimed. Fabrication is the one unacceptable outcome.
    - You may reword bullets to foreground relevant work, reorder entries and
      skills by relevance, and adopt the posting's vocabulary where it genuinely
      describes what the candidate already did.
    - Keep the exact JSON structure, keys and language of the input resume.
    - Keep every experience and education entry. Reorder, do not drop.

    Respond ONLY with JSON: {"resume": { ...the complete modified resume... },
    "changes_summary": "one sentence on what you changed"}
    """.strip()

    hint = ""
    if missing_keywords:
        hint = (
            "\n\nThe posting asks for these, which the resume does not evidence. "
            "Only surface them where the candidate's existing work genuinely "
            "supports it; otherwise leave them out: "
            + ", ".join(missing_keywords[:15])
        )

    raw = send_openai_message(
        user_message=(
            f"RESUME (JSON):\n{_resume_text(resume)}\n\n"
            f"JOB POSTING:\n{description}{hint}"
        ),
        meta_prompt=meta_prompt,
        is_json=True,
        temperature=0.3,
        max_tokens=4000,
    )

    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("Tailoring returned unparseable JSON: %s", raw[:200])
        return {"error": "Could not tailor the resume. Please try again."}

    tailored = parsed.get("resume")
    if not isinstance(tailored, dict) or "user_info" not in tailored:
        logger.warning("Tailoring returned a resume without user_info")
        return {"error": "The tailored resume came back malformed. Please try again."}

    return {
        "content": tailored,
        "changes_summary": str(parsed.get("changes_summary") or ""),
    }


def resume_groups(user):
    """
    Which resume the user sends for which kind of role.

    Grouped by the base resume each application's snapshot came from. That is a
    fact about what was sent, where the tags this used to key on were guesses —
    and mostly generic ones, so a single posting drew the same resume into
    several identical groups.
    """
    from resume.models import JobPosting

    groups = {}
    postings = (
        JobPosting.objects.filter(user=user, source_resume__isnull=False)
        .select_related("source_resume")
        .order_by("-updated_at")
    )
    for posting in postings:
        bucket = groups.setdefault(
            posting.source_resume_id,
            {
                "resume_id": posting.source_resume_id,
                "resume_name": posting.source_resume.display_name,
                "applications": [],
            },
        )
        bucket["applications"].append(
            {
                "job_id": posting.id,
                "title": posting.title,
                "company": posting.company,
                "status": posting.status,
                "score": posting.match_score,
            }
        )

    return sorted(
        groups.values(), key=lambda g: len(g["applications"]), reverse=True
    )
