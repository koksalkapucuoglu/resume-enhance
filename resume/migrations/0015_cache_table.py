from django.core.cache.backends.db import BaseDatabaseCache
from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    """
    The database cache needs its table.

    Created through the migration rather than a deploy step so an existing
    install picks it up automatically — the agent's approval flow stops working
    the moment the cache is unavailable.
    """
    call_command("createcachetable", database=schema_editor.connection.alias)


def drop_cache_table(apps, schema_editor):
    from django.conf import settings

    for cache in settings.CACHES.values():
        if cache["BACKEND"].endswith("db.DatabaseCache"):
            table = cache.get("LOCATION")
            if table:
                schema_editor.execute(f'DROP TABLE IF EXISTS "{table}"')


class Migration(migrations.Migration):
    dependencies = [
        ("resume", "0014_userprofile_premium_until_purchase"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]
