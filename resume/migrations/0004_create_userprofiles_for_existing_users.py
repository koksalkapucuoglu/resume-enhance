# Data migration to create UserProfile for all existing users

from django.db import migrations


def create_profiles_for_existing_users(apps, schema_editor):
    """Create UserProfile for all existing users."""
    User = apps.get_model('auth', 'User')
    UserProfile = apps.get_model('resume', 'UserProfile')
    for user in User.objects.all():
        UserProfile.objects.get_or_create(user=user)


def reverse_create_profiles(apps, schema_editor):
    """Reverse: delete all UserProfile instances."""
    UserProfile = apps.get_model('resume', 'UserProfile')
    UserProfile.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('resume', '0003_userprofile'),
    ]

    operations = [
        migrations.RunPython(create_profiles_for_existing_users, reverse_create_profiles),
    ]
