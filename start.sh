#!/bin/bash
set -e
python manage.py migrate
python manage.py seed_data
exec gunicorn config.wsgi --bind "0.0.0.0:${PORT}" --workers 2 --timeout 120
