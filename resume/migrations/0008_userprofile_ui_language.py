from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('resume', '0007_add_agent_message_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='ui_language',
            field=models.CharField(default='en', max_length=5),
        ),
    ]
