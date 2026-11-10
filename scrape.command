#!/bin/zsh
# Double-click (or run) to do what the daily job does, with a window to watch: full fresh scrape of every source
# (the scraper opens its dashboard window), then the site is rebuilt and published. The Mac stays awake until it's done.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
set -o pipefail
mkdir -p logs
LOG="logs/daily-$(date +%F).log"
if pgrep -f "landfinder.py" >/dev/null; then
  echo "A scrape is already running; opening its dashboard window."
  exec .venv/bin/python dashboard.py
fi
echo "== start $(date)" | tee -a "$LOG"
echo "Log: $LOG"
if caffeinate -i ./run.sh --fresh --hidden "$@" 2>&1 | tee -a "$LOG"; then
  ./publish.sh 2>&1 | tee -a "$LOG"
fi
echo "== end $(date) (exit $?)" | tee -a "$LOG"
echo; echo "Done. You can close this window."
