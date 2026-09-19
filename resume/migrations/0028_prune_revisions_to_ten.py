"""
Keep the newest ten restore points per resume.

Retention was five on the free plan and unlimited on Pro; it is now ten for
everyone (settings.REVISION_HISTORY_LIMIT). This trims what accumulated
before, once; revision_service.prune keeps it there afterwards.
"""

from django.db import migrations

KEEP = 10


def prune(apps, schema_editor):
    Resume = apps.get_model("resume", "Resume")
    ResumeRevision = apps.get_model("resume", "ResumeRevision")
    for resume_id in Resume.objects.values_list("pk", flat=True).iterator():
        stale = list(
            ResumeRevision.objects.filter(resume_id=resume_id)
            .order_by("-created_at", "-id")
            .values_list("pk", flat=True)[KEEP:]
        )
        if stale:
            ResumeRevision.objects.filter(pk__in=stale).delete()


class Migration(migrations.Migration):
    dependencies = [("resume", "0027_job_postings_v2")]

    operations = [migrations.RunPython(prune, migrations.RunPython.noop)]
