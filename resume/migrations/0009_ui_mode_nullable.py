from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Make ui_mode nullable so NULL can mean "the user never chose a mode".
    Unset profiles then resolve to UserProfile.UI_MODE_DEFAULT (agentic),
    while every explicit choice already stored is preserved as-is.
    """

    dependencies = [
        ("resume", "0008_userprofile_ui_language"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userprofile",
            name="ui_mode",
            field=models.CharField(
                blank=True,
                choices=[("standard", "Standard"), ("agentic", "Agentic")],
                default=None,
                max_length=20,
                null=True,
            ),
        ),
    ]
