from django.db import migrations


def backfill_language(apps, schema_editor):
    """
    Adopt the language the AI extractor already recorded in content["language"].
    Resumes without one keep the "en" default.
    """
    Resume = apps.get_model("resume", "Resume")
    aliases = {
        "english": "en", "ingilizce": "en", "en": "en",
        "turkish": "tr", "türkçe": "tr", "turkce": "tr", "tr": "tr",
    }
    updates = []
    for resume in Resume.objects.all().only("id", "content", "language"):
        detected = (resume.content or {}).get("language")
        code = aliases.get(str(detected or "").strip().lower())
        if code and code != resume.language:
            resume.language = code
            updates.append(resume)
    if updates:
        Resume.objects.bulk_update(updates, ["language"])


class Migration(migrations.Migration):
    dependencies = [
        ("resume", "0011_resume_language_resume_translation_of"),
    ]

    operations = [
        migrations.RunPython(backfill_language, migrations.RunPython.noop),
    ]
