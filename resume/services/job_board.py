"""
What the Applications page shows: a summary of the pipeline and one card per
application, built from what each measurement stored.

Nothing here calls a model. The requirement table was paid for when the
posting was measured (services/job_match.py); this page only reads it back —
so the "most common gaps" across applications cost nothing to show.
"""

from collections import Counter

from resume.models import JobPosting

SORTS = ("recent", "score")
TREND_POINTS = 10
SPARK_WIDTH = 72
SPARK_HEIGHT = 22


def board(user, status=None, sort="recent"):
    postings = list(
        JobPosting.objects.filter(user=user).select_related("source_resume")
    )
    summary = _summary(postings)

    shown = [p for p in postings if not status or p.status == status]
    if sort == "score":
        shown.sort(key=lambda p: (p.match_score is None, -(p.match_score or 0)))
    else:
        shown.sort(key=lambda p: p.updated_at, reverse=True)
    return {"summary": summary, "cards": [card(p) for p in shown]}


def _summary(postings):
    counts = Counter(p.status for p in postings)
    scored = [p.match_score for p in postings if p.match_score is not None]
    sent = [p for p in postings if p.status in JobPosting.SENT_STATUSES]
    answered = [
        p for p in sent
        if p.status in (JobPosting.STATUS_INTERVIEW, JobPosting.STATUS_OFFER)
    ]

    # Required lines the resume did not show, across every measured posting:
    # the skills worth working on, not just the edits worth making.
    gaps = Counter()
    spellings = {}
    for p in postings:
        seen = set()
        for r in p.requirements or []:
            if r.get("must_have", 0) >= 0.5 and r.get("status") == "missing":
                label = (r.get("label") or r.get("text") or "").strip()
                key = label.lower()
                if key and key not in seen:
                    seen.add(key)
                    gaps[key] += 1
                    spellings.setdefault(key, Counter())[label] += 1
    return {
        "total": len(postings),
        "by_status": [
            {"status": value, "label": label, "count": counts.get(value, 0)}
            for value, label in JobPosting.STATUS_CHOICES
        ],
        "average_score": round(sum(scored) / len(scored)) if scored else None,
        # Only meaningful once a few have gone out.
        "response_rate": round(100 * len(answered) / len(sent)) if len(sent) >= 3 else None,
        "sent": len(sent),
        "top_gaps": [
            {"label": _display(spellings[key]), "count": n}
            for key, n in gaps.most_common(5)
            if n >= 2 or len(postings) == 1
        ],
    }


def _display(spellings):
    """The commonest way a gap was written; on a tie, the one with capitals."""
    return max(spellings, key=lambda label: (spellings[label], label != label.lower()))


def card(posting):
    requirements = posting.requirements or []
    required = [r for r in requirements if r.get("must_have", 0) >= 0.5]
    nice = [r for r in requirements if r.get("must_have", 0) < 0.5]
    order = {"missing": 0, "partial": 1, "covered": 2}

    def sorted_(items):
        return sorted(items, key=lambda r: order.get(r.get("status"), 0))

    gaps = [r for r in sorted_(required) if r.get("status") != "covered"][:3]
    history = _comparable_history(posting)
    return {
        "posting": posting,
        "score": posting.match_score,
        "tone": _tone(posting.match_score),
        "measured": bool(requirements),
        "required_total": len(required),
        "required_covered": sum(1 for r in required if r.get("status") == "covered"),
        "partial": sum(1 for r in requirements if r.get("status") == "partial"),
        "required_ratio": (
            f"{sum(1 for r in required if r.get('status') == 'covered')}/{len(required)}"
        ),
        "gaps": [{"label": r.get("label") or r.get("text"), "status": r.get("status")} for r in gaps],
        "required": sorted_(required),
        "nice": sorted_(nice),
        "trend": _sparkline(history),
        "delta": history[-1] - history[-2] if len(history) >= 2 else None,
        "can_rescore": posting.has_snapshot and bool(posting.description),
    }


def _tone(score):
    if score is None:
        return "none"
    if score >= 75:
        return "good"
    if score >= 50:
        return "fair"
    return "low"


def _comparable_history(posting):
    """Scores from the scorer that produced the current one, oldest first."""
    version = posting.scoring_version or "llm-v1"
    return [
        h["score"]
        for h in posting.score_history or []
        if h.get("score") is not None and h.get("version", "llm-v1") == version
    ][-TREND_POINTS:]


def _sparkline(scores):
    """SVG polyline points for a small trend line, or "" with fewer than two."""
    if len(scores) < 2:
        return ""
    # Scaled to the scores' own range (at least 20 points tall), so a move
    # from 48 to 63 is visible rather than a flat line on a 0-100 axis.
    low, high = min(scores), max(scores)
    span = max(high - low, 20)
    floor = max(0, min(low - (span - (high - low)) / 2, 100 - span))
    step = SPARK_WIDTH / (len(scores) - 1)
    return " ".join(
        f"{i * step:.1f},{SPARK_HEIGHT - ((s - floor) / span) * SPARK_HEIGHT:.1f}"
        for i, s in enumerate(scores)
    )
