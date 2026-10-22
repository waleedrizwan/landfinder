#!/bin/zsh
# One-command runner: sets up the Python environment on first run, then scrapes.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && \
  .venv/bin/pip install -q requests beautifulsoup4 lxml playwright
fi
exec .venv/bin/python -W ignore landfinder.py "$@"
