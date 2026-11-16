"""
Enables required PostgreSQL extensions BEFORE any app creates tables that use
them (PostGIS geography columns, pgvector embeddings).

`run_before` forces this migration ahead of the first migration of every app
that depends on these extensions. Requires the DB role to have CREATE
privilege, or the extensions to be pre-created by a superuser on managed
Postgres (e.g. Render: enable via dashboard, then this becomes a no-op).
"""
from django.contrib.postgres.operations import CreateExtension
from django.db import migrations


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    # Ensure extensions exist before these apps build their tables.
    run_before = [
        ("accounts", "__first__"),
        ("catalog", "__first__"),
        ("bookings", "__first__"),
        ("jobs", "__first__"),
    ]

    operations = [
        CreateExtension("postgis"),
        # pgvector ("vector") is deferred to Phase 3 (AI chatbot KB); the
        # managed Postgres image used in dev does not ship it yet.
    ]
