#!/usr/bin/env bash
# Render build step. Exit on any failure so a broken deploy never goes live.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

python manage.py collectstatic --no-input
python manage.py migrate

# Category taxonomies are reference data, not user data — safe and idempotent.
python manage.py seed_categories
python manage.py seed_kb
