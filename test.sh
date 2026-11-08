#!/bin/zsh
# Run the test suite: sets up .venv like run.sh on first use, adds pytest, then runs it.
#   ./test.sh                 # everything
#   ./test.sh -m "not e2e"    # skip the browser tests
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && \
  .venv/bin/pip install -q requests beautifulsoup4 lxml playwright
fi
.venv/bin/python -c "import pytest" 2>/dev/null || .venv/bin/pip install -q pytest
exec .venv/bin/python -m pytest "$@"
