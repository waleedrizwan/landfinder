#!/bin/zsh
# Install (or update) a launchd job that runs daily.sh every day at the given time (default 06:00).
# If the Mac is asleep then, macOS runs it as soon as it wakes.
#   ./install_daily.sh          # 6:00 am
#   ./install_daily.sh 7 30     # 7:30 am
#   ./install_daily.sh off      # remove the job
cd "$(dirname "$0")"
LABEL="com.$(whoami).landfinder"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
if [ "$1" = "off" ]; then rm -f "$PLIST"; echo "daily run removed"; exit 0; fi
HOUR=${1:-6}; MIN=${2:-0}
mkdir -p "$HOME/Library/LaunchAgents" logs
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/zsh</string><string>$PWD/daily.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
  <key>StandardOutPath</key><string>$PWD/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$PWD/logs/launchd.log</string>
</dict></plist>
PL
launchctl bootstrap "gui/$(id -u)" "$PLIST" && printf "daily run installed: every day at %02d:%02d\n" $HOUR $MIN
