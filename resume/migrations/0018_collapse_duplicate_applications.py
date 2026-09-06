import hashlib
import re

from django.db import migrations


def _fingerprint(description):
    normalized = re.sub(r"\s+", " ", (description or "")).strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest() if normalized else ""


def collapse(apps, schema_editor):
    """
    Fold the duplicate applications the old create-on-every-paste behaviour left
    behind, and tidy the variant titles that compounded with each tailoring.

    Keeping the best score and the furthest-along status means nothing the user
    achieved is lost when rows merge.
    """
    JobPosting = apps.get_model("resume", "JobPosting")
    Resume = apps.get_model("resume", "Resume")

    status_rank = {
        "saved": 0, "applied": 1, "interview": 2, "offer": 3, "rejected": 1,
    }

    keepers = {}
    for posting in JobPosting.objects.all().order_by("created_at", "id"):
        digest = _fingerprint(posting.description)
        posting.content_hash = digest
        if not digest:
            posting.save(update_fields=["content_hash"])
            continue

        key = (posting.user_id, digest)
        first = keepers.get(key)
        if first is None:
            keepers[key] = posting
            posting.save(update_fields=["content_hash"])
            continue

        # Merge into the one we are keeping
        if (posting.match_score or 0) > (first.match_score or 0):
            first.match_score = posting.match_score
            first.missing_keywords = posting.missing_keywords
        if status_rank.get(posting.status, 0) > status_rank.get(first.status, 0):
            first.status = posting.status
        if posting.resume_id and not first.resume_id:
            first.resume_id = posting.resume_id
        first.save(
            update_fields=["match_score", "missing_keywords", "status", "resume"]
        )
        posting.delete()

    # "Main — Role — Role — Role" collapses back to "Main — Role"
    for resume in Resume.objects.filter(title__contains=" — "):
        head, _, tail = resume.title.partition(" — ")
        parts, seen = [], set()
        for part in tail.split(" — "):
            part = part.strip()
            if part and part not in seen:
                seen.add(part)
                parts.append(part)
        rebuilt = f"{head} — {parts[0]}" if parts else head
        if rebuilt != resume.title:
            resume.title = rebuilt[:255]
            resume.save(update_fields=["title"])


class Migration(migrations.Migration):
    dependencies = [
        ("resume", "0017_derived_resumes_and_score_history"),
    ]

    operations = [
        migrations.RunPython(collapse, migrations.RunPython.noop),
    ]
