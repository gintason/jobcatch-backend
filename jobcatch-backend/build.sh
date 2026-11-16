#!/usr/bin/env bash
# Render build step.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

python manage.py collectstatic --no-input
python manage.py migrate

# Reference data — safe and idempotent.
python manage.py seed_categories

# The AI knowledge base needs a funded LLM provider. It's an optional feature,
# so a failure here must not take the whole marketplace down with it.
python manage.py seed_kb || echo "WARNING: seed_kb failed (AI assistant will be unavailable). Continuing."
