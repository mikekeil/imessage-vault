#!/bin/zsh
# Install (or update) the launchd jobs that run scheduled-backup.sh:
#   daily at 03:15, and on the 1st of each month at 04:15.
# If the Mac is asleep then, launchd runs the job when it wakes.
#
#   ./install-schedule.sh [--export-flags "--max-image-dim 2000"] \
#                         [--archive-dir DIR] [--mirror-dir DIR] [--keep-full N]
#   ./install-schedule.sh --uninstall
#
# Afterwards, grant Full Disk Access to /bin/zsh (System Settings > Privacy &
# Security > Full Disk Access > + > Cmd-Shift-G > /bin/zsh), or the jobs can't
# read chat.db.

set -euo pipefail
REPO="${0:A:h}"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs/imessage-vault"
PREFIX="${IMESSAGE_LAUNCHD_PREFIX:-local.imessage-vault}"

EXPORT_FLAGS="" ARCHIVE_DIR="$HOME/iMessage-Export-Archives" MIRROR_DIR="" KEEP_FULL=2 UNINSTALL=0
while (( $# )); do
  case "$1" in
    --export-flags) EXPORT_FLAGS="$2"; shift 2 ;;
    --archive-dir)  ARCHIVE_DIR="${2:A}"; shift 2 ;;
    --mirror-dir)   MIRROR_DIR="${2:A}"; shift 2 ;;
    --keep-full)    KEEP_FULL="$2"; shift 2 ;;
    --uninstall)    UNINSTALL=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

xml_escape() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<< "$1"; }

for MODE in daily monthly; do
  LABEL="$PREFIX.$MODE"
  PLIST="$AGENTS/$LABEL.plist"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  if (( UNINSTALL )); then
    rm -f "$PLIST"; echo "Removed $LABEL"; continue
  fi

  if [[ $MODE == daily ]]; then
    CAL="<key>Hour</key><integer>3</integer><key>Minute</key><integer>15</integer>"
  else
    CAL="<key>Day</key><integer>1</integer><key>Hour</key><integer>4</integer><key>Minute</key><integer>15</integer>"
  fi

  mkdir -p "$AGENTS" "$LOGS"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>$(xml_escape "$REPO/scheduled-backup.sh")</string>
        <string>$MODE</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>IMESSAGE_EXPORT_FLAGS</key>
        <string>$(xml_escape "$EXPORT_FLAGS")</string>
        <key>IMESSAGE_ARCHIVE_DIR</key>
        <string>$(xml_escape "$ARCHIVE_DIR")</string>
        <key>IMESSAGE_MIRROR_DIR</key>
        <string>$(xml_escape "$MIRROR_DIR")</string>
        <key>IMESSAGE_KEEP_FULL</key>
        <string>$KEEP_FULL</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict>$CAL</dict>
    <key>StandardOutPath</key>
    <string>$(xml_escape "$LOGS/$MODE.log")</string>
    <key>StandardErrorPath</key>
    <string>$(xml_escape "$LOGS/$MODE.log")</string>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
EOF
  plutil -lint -s "$PLIST"
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "Installed $LABEL"
done

(( UNINSTALL )) || cat <<EOF

Logs: $LOGS
Test the daily job now:   launchctl kickstart gui/$(id -u)/$PREFIX.daily
Remember: /bin/zsh needs Full Disk Access for these jobs to read chat.db.
EOF
