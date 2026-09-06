import django.db.models.deletion
from django.db import migrations, models


def carry_translations_over(apps, schema_editor):
    """
    Move existing translation links onto the generalised field.

    Adding and removing in one auto-generated step would have dropped them.
    """
    Resume = apps.get_model("resume", "Resume")
    linked = Resume.objects.filter(translation_of__isnull=False)
    for resume in linked:
        resume.derived_from_id = resume.translation_of_id
        resume.derived_kind = "translation"
    if linked:
        Resume.objects.bulk_update(list(linked), ["derived_from", "derived_kind"])


def restore_translations(apps, schema_editor):
    Resume = apps.get_model("resume", "Resume")
    derived = Resume.objects.filter(derived_kind="translation")
    for resume in derived:
        resume.translation_of_id = resume.derived_from_id
    if derived:
        Resume.objects.bulk_update(list(derived), ["translation_of"])


class Migration(migrations.Migration):
    dependencies = [
        ("resume", "0016_userprofile_confirm_destructive"),
    ]

    operations = [
        migrations.AddField(
            model_name="resume",
            name="derived_from",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="derivatives",
                to="resume.resume",
            ),
        ),
        migrations.AddField(
            model_name="resume",
            name="derived_kind",
            field=models.CharField(
                blank=True,
                choices=[
                    ("translation", "Language version"),
                    ("tailored", "Tailored for a job"),
                ],
                default="",
                max_length=20,
            ),
        ),
        migrations.RunPython(carry_translations_over, restore_translations),
        migrations.RemoveField(model_name="resume", name="translation_of"),
        migrations.AddField(
            model_name="jobposting",
            name="content_hash",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="jobposting",
            name="score_history",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
