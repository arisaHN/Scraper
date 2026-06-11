#!/usr/bin/env bash
set -e  

# Apply any pending Alembic migrations
python -m alembic upgrade head

# Hand off to cli.py with whatever arguments were passed to the container
# e.g. "scrape mybrand" or "list-brands"
exec python cli.py "$@"