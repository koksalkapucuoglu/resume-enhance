from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('resume', '0005_resume_template_selector'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='ui_mode',
            field=models.CharField(
                choices=[('standard', 'Standard'), ('agentic', 'Agentic')],
                default='standard',
                max_length=20,
            ),
        ),
    ]
