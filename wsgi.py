"""
wsgi.py - the entry point a production server uses (gunicorn wsgi:app).

`python app.py` runs startup() itself; a production server imports the app instead,
so this file does the same set-up: create or upgrade the database, seed it on first start,
requeue half-finished invoices and start the inbox watcher and the agent worker.

IMPORTANT: run exactly ONE worker process (gunicorn --workers 1 --threads 4). The agent queue,
the inbox watcher and the sign-in rate limits live in memory; two workers would each run their own.
"""
from app import app, startup

startup()
