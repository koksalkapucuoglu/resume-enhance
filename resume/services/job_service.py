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
        "req_required": "Required",
        "req_nice": "Nice to have",
        "st_covered": "Shown in your resume",
        "st_partial": "Partly shown",
        "st_missing": "Not shown",
        "evidence": "From your resume",
        "posting_line": "The posting says",
        "uncertain": "unsure",
        "notice_injection": "This posting had lines addressed to AI screeners. They were ignored.",
        "method_lines": "{n} requirements measured one by one",
        "method_estimate": "Estimated in one pass",
        "chip_apply": "Apply the suggestions",
        "chip_tailor": "Tailor a copy for this job",
        "msg_apply": "Apply the suggestions from the {job} match to my resume.",
        "msg_tailor": "Tailor my resume for the {job} application.",
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
        "req_required": "Zorunlu",
        "req_nice": "Tercihen",
        "st_covered": "CV'nizde var",
        "st_partial": "Kısmen var",
        "st_missing": "Yok",
        "evidence": "CV'nizden",
        "posting_line": "İlanda",
        "uncertain": "emin değil",
        "notice_injection": "Bu ilanda yapay zekâ taramasına yönelik satırlar vardı. Dikkate alınmadılar.",
        "method_lines": "{n} gereksinim tek tek ölçüldü",
        "method_estimate": "Tek seferde tahmin edildi",
        "chip_apply": "Önerileri uygula",
        "chip_tailor": "Bu ilana göre kopya uyarla",
        "msg_apply": "{job} eşleşmesindeki önerileri CV'me uygula.",
        "msg_tailor": "CV'mi {job} başvurusu için uyarla.",
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


def analyze_match(resume, description, lang="en", prose=True):
    return _analyze(resume.content, description, lang, prose=prose)


def match_and_record(user, resume, description, lang="en", apply_to=None,
                     prose=True, title=None, company=None):
    """
    Measure a posting against a resume and file it as an application.

    Shared by the chat assistant and the MCP server. Returns one of:
      {"error": ...}
      {"needs_choice": True, "reason", "existing"}  — a posting for the same
          role at the same company is already tracked; ask which one is meant
          and call again with apply_to = its id or "new"
      {"capped": True}  — the free plan's application limit
      {"posting", "previous", "is_new", "result"}

    `title` and `company`, when the caller knows them, take precedence over
    what the measurement found — an MCP client passes them because the
    server writes no prose for it (`prose=False`).
    """
    digest = JobPosting.fingerprint(description)

    # Same text as something already tracked: re-measure it, no question needed.
    posting = (
        JobPosting.objects.filter(user=user, content_hash=digest).first()
        if digest
        else None
    )

    # The cache only exists so that answering our own question does not pay for
    # a second look at the same text. It must not serve a re-measurement: the
    # point of pasting a posting again is to see the score move.
    result = cached_analysis(user.id, digest) if digest and apply_to else None
    if result is None:
        result = analyze_match(resume, description, lang, prose=prose)
        if "error" in result:
            return result
    if title:
        result["title"] = title.strip()[:255]
    if company is not None and company.strip():
        result["company"] = company.strip()[:255]

    if posting is None and apply_to not in (None, "", "new"):
        try:
            target_id = int(apply_to)
        except (TypeError, ValueError):
            return {"error": "apply_to must be 'new' or an id."}
        posting = JobPosting.objects.filter(pk=target_id, user=user).first()
        if not posting:
            return {"error": f"No saved job with id {target_id}."}
        # Replacing the text the application tracks
        posting.description = description[:MAX_DESCRIPTION_CHARS]
        posting.content_hash = digest

    if posting is None and apply_to is None:
        # A posting for the same role at the same company, but not the same
        # text. It could be a re-paste or a genuinely different opening, and
        # guessing either way loses something — so ask.
        similar = JobPosting.objects.filter(
            user=user,
            title__iexact=result["title"],
            company__iexact=result["company"],
        ).first()
        if similar:
            # Hold the analysis so their answer costs nothing extra.
            if digest:
                remember_analysis(user.id, digest, result)
            return {
                "needs_choice": True,
                "reason": "An application for this role at this company already exists.",
                "existing": {
                    "job_id": similar.id,
                    "title": similar.title,
                    "company": similar.company,
                    "score": similar.match_score,
                    "status": similar.status,
                },
            }

    is_new = posting is None
    if is_new:
        if not user.profile.can_track_application():
            return {"capped": True}
        posting = JobPosting(
            user=user,
            description=description[:MAX_DESCRIPTION_CHARS],
            content_hash=digest,
        )
    posting.title = result["title"]
    posting.company = result["company"]
    # Freeze what would go out, so the application still knows what was sent
    # after the base resume moves on.
    posting.take_snapshot(resume.content, resume.template_selector, resume)
    previous = posting.record_score(
        result["score"], resume.id, result["missing_keywords"],
        requirements=result.get("requirements"),
        scoring_version=result.get("scoring_version", "llm-v1"),
    )
    posting.save()
    return {"posting": posting, "previous": previous, "is_new": is_new, "result": result}


def _analyze(content, description, lang="en", prose=True):
    """
    Score how well a resume answers a posting.

    Jev measures it line by line (services/job_match.py); when Jev cannot be
    reached, a single OpenAI call estimates it instead. `scoring_version` says
    which one did, since their numbers are not directly comparable.
    """
    description = (description or "").strip()[:MAX_DESCRIPTION_CHARS]
    if len(description) < 40:
        return {"error": "The job description is too short to analyse."}

    from resume.services import job_match

    measured = job_match.analyze(content, description, lang, prose=prose)
    if measured is not None:
        return measured
    return _analyze_llm(content, description, lang)


def _analyze_llm(content, description, lang="en"):
    """
    The single-call scorer: one LLM estimate of the whole match.

    Returns a dict with score, matched/missing keywords and a short verdict,
    or {"error": ...} when the model's answer is unusable.
    """

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
        # Length only: the response describes the user's resume, and logs are
        # promised to hold technical information, not personal data.
        logger.warning("Job match returned unparseable JSON (%d chars)", len(raw or ""))
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
        "partial_keywords": [],
        "requirements": [],
        "scoring_version": "llm-v1",
        "notices": [],
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
        logger.warning("Tailoring returned unparseable JSON (%d chars)", len(raw or ""))
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
