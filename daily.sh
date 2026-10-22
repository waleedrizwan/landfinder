#!/bin/zsh
# Daily job: full fresh scrape -> rebuild site -> deploy to Cloudflare Pages.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
mkdir -p logs
LOG="logs/daily-$(date +%F).log"
if pgrep -f "landfinder.py" >/dev/null; then
  echo "$(date) another landfinder run is active; skipping" >> "$LOG"; exit 0
fi
{
  echo "== start $(date)"
  ./run.sh --fresh --hidden --pages 10 && ./publish.sh
  echo "== end $(date) (exit $?)"
} >> "$LOG" 2>&1
