import django.db.models.deletion
from django.db import migrations, models


def freeze_snapshots(apps, schema_editor):
    """
    Give every application a copy of the resume it was using, and fold the
    per-job resume variants into the applications they belong to.

    A variant that was tailored for one posting is what that application sent;
    it belongs inside the application, not in the user's resume list. A variant
    with no application attached is something the user may still be working on,
    so it is promoted to a resume of its own rather than deleted — quietly
    destroying someone's document to satisfy a schema change is not a trade
    worth making, even if it briefly puts them over the resume limit.
    """
    JobPosting = apps.get_model("resume", "JobPosting")
    Resume = apps.get_model("resume", "Resume")

    absorbed = set()
    for posting in JobPosting.objects.select_related("source_resume"):
        attached = posting.source_resume
        if attached is None:
            continue

        posting.snapshot_content = attached.content or {}
        posting.snapshot_template = attached.template_selector or "faangpath-simple"
        posting.snapshot_taken_at = posting.updated_at

        if attached.derived_kind == "tailored":
            absorbed.add(attached.pk)
            # Point the application at the base resume, for grouping
            posting.source_resume_id = attached.derived_from_id
        posting.save(
            update_fields=[
                "snapshot_content",
                "snapshot_template",
                "snapshot_taken_at",
                "source_resume",
            ]
        )

    if absorbed:
        Resume.objects.filter(pk__in=absorbed).delete()

    Resume.objects.filter(derived_kind="tailored").update(
        derived_from=None, derived_kind=""
    )


class Migration(migrations.Migration):
    dependencies = [
        ("resume", "0018_collapse_duplicate_applications"),
    ]

    operations = [
        migrations.RenameField(
            model_name="jobposting", old_name="resume", new_name="source_resume"
        ),
        migrations.AlterField(
            model_name="jobposting",
            name="source_resume",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="job_postings",
                to="resume.resume",
            ),
        ),
        migrations.AddField(
            model_name="jobposting",
            name="snapshot_content",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="jobposting",
            name="snapshot_template",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AddField(
            model_name="jobposting",
            name="snapshot_taken_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(freeze_snapshots, migrations.RunPython.noop),
        migrations.RemoveField(model_name="jobposting", name="tags"),
        migrations.AlterField(
            model_name="resume",
            name="derived_kind",
            field=models.CharField(
                blank=True,
                choices=[("translation", "Language version")],
                default="",
                max_length=20,
            ),
        ),
    ]
