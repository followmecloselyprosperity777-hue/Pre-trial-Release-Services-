#!/usr/bin/env bash
# Start PocketBolt (test mode). Usage: ./run.sh   (PORT defaults to 8000)
set -e
cd "$(dirname "$0")"
[ -d venv ] || { python3 -m venv venv && ./venv/bin/pip install -r requirements.txt; }
./venv/bin/python seed.py
exec ./venv/bin/gunicorn -w 1 --threads 8 -b "0.0.0.0:${PORT:-8000}" app:app
