#!/bin/sh
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec gunicorn core.wsgi:application \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --workers 2 \
    --worker-class gthread \
    --threads 4 \
    --max-requests 10000
