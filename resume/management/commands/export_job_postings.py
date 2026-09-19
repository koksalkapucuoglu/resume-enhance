"""
Dump every tracked application to JSON before the job-posting tables go.

    python manage.py export_job_postings > job_postings_backup.json

Read-only. The output holds users' posting texts, resume snapshots and notes,
so treat the file as personal data: keep it off shared storage and delete it
once it is no longer needed.
"""

import json

from django.core.management.base import BaseCommand

from resume.models import JobPosting


class Command(BaseCommand):
    help = "Write all JobPosting rows to stdout as JSON (read-only backup)."

    def handle(self, *args, **options):
        rows = []
        for posting in JobPosting.objects.select_related("user").order_by("pk").iterator():
            rows.append(
                {
                    "id": posting.pk,
                    "user_id": posting.user_id,
                    "username": posting.user.username,
                    "title": posting.title,
                    "company": posting.company,
                    "url": posting.url,
                    "description": posting.description,
                    "status": posting.status,
                    "applied_at": posting.applied_at.isoformat() if posting.applied_at else None,
                    "status_changed_at": (
                        posting.status_changed_at.isoformat() if posting.status_changed_at else None
                    ),
                    "notes": posting.notes,
                    "match_score": posting.match_score,
                    "scoring_version": posting.scoring_version,
                    "score_history": posting.score_history,
                    "requirements": posting.requirements,
                    "missing_keywords": posting.missing_keywords,
                    "source_resume_id": posting.source_resume_id,
                    "snapshot_content": posting.snapshot_content,
                    "snapshot_template": posting.snapshot_template,
                    "snapshot_taken_at": (
                        posting.snapshot_taken_at.isoformat() if posting.snapshot_taken_at else None
                    ),
                    "created_at": posting.created_at.isoformat(),
                    "updated_at": posting.updated_at.isoformat(),
                }
            )
        self.stdout.write(json.dumps({"job_postings": rows}, ensure_ascii=False, indent=2))
        self.stderr.write(f"Exported {len(rows)} job postings.")
