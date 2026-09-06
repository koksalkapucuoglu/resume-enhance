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


def analyze_match(resume, description):
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
    - "tags" are 2-5 lowercase labels for the KIND of role this is
      (e.g. "python", "backend", "devops", "senior"), used to group postings.
    - Write "verdict" and "suggestions" in the resume's language.

    Respond ONLY with JSON:
    {
      "score": 0-100,
      "matched_keywords": ["..."],
      "missing_keywords": ["..."],
      "tags": ["..."],
      "title": "job title from the posting",
      "company": "company name, or empty string",
      "verdict": "two sentences on the fit",
      "suggestions": ["concrete change 1", "concrete change 2", "concrete change 3"]
    }
    """.strip()

    user_message = (
        f"RESUME (JSON):\n{_resume_text(resume)}\n\n"
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
        "tags": JobPosting.normalize_tags(parsed.get("tags", []))[:5],
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
    Which resume the user actually sends for which kind of role.

    Built from applications rather than a label the user has to maintain, so it
    reflects what they do, not what they once wrote down.
    """
    groups = {}
    postings = (
        JobPosting.objects.filter(user=user, resume__isnull=False)
        .select_related("resume")
        .order_by("-updated_at")
    )
    for posting in postings:
        for tag in posting.tags or []:
            bucket = groups.setdefault(tag, {})
            entry = bucket.setdefault(
                posting.resume_id,
                {
                    "resume_id": posting.resume_id,
                    "resume_name": posting.resume.display_name,
                    "uses": 0,
                },
            )
            entry["uses"] += 1

    return [
        {
            "tag": tag,
            "resumes": sorted(
                bucket.values(), key=lambda e: e["uses"], reverse=True
            ),
        }
        for tag, bucket in sorted(groups.items())
    ]
