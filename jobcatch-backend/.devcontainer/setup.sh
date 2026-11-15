#!/usr/bin/env bash
# Runs once when the Codespace is first created.
set -e

echo "==> Ensuring .env exists"
[ -f .env ] || cp .env.example .env

echo "==> Generating model migrations"
python manage.py makemigrations

echo "==> Applying migrations (PostGIS + pgvector extensions run first)"
python manage.py migrate

echo "==> Seeding demo data (categories + one user per role)"
python manage.py seed_demo

echo ""
echo "======================================================"
echo " Setup complete."
echo " Start the server with:"
echo "   python manage.py runserver 0.0.0.0:8000"
echo " Then open the forwarded port 8000 -> /api/docs/"
echo " Admin login: admin@jobcatch.test / Demo!Pass123"
echo "======================================================"
