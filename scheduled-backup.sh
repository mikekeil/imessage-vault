#!/bin/zsh
# Scheduled iMessage backup, run by launchd (see install-schedule.sh).
#   daily:   incremental export into the export folder
#   monthly: same, then an archive (full once a year, monthly otherwise)
#
# Configured through environment variables, which install-schedule.sh bakes
# into the launchd jobs:
#   IMESSAGE_EXPORT_FLAGS  extra flags for extract_imessages.py; must match the
#                          flags your export was made with (e.g. --max-image-dim)
#   IMESSAGE_ARCHIVE_DIR   where archives go (default ~/iMessage-Export-Archives)
#   IMESSAGE_MIRROR_DIR    optional second copy, e.g. a Google Drive folder
#   IMESSAGE_KEEP_FULL     full archives to keep (default 2)
#
# Needs Full Disk Access for /bin/zsh, the launchd job's program, to read chat.db.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

MODE="${1:?usage: scheduled-backup.sh daily|monthly}"
REPO="${0:A:h}"
ARCHIVE_DIR="${IMESSAGE_ARCHIVE_DIR:-$HOME/iMessage-Export-Archives}"
EXPORT_FLAGS=(${=IMESSAGE_EXPORT_FLAGS:-})

notify_failure() {
  osascript -e "display notification \"$MODE backup failed; see ~/Library/Logs/imessage-vault\" with title \"iMessage Vault\"" || true
}
trap notify_failure ERR

cd "$REPO"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') $MODE backup"
python3 extract_imessages.py "${EXPORT_FLAGS[@]}"

if [[ "$MODE" == "monthly" ]]; then
  ARCHIVE_ARGS=(--archive-dir "$ARCHIVE_DIR" --keep-full "${IMESSAGE_KEEP_FULL:-2}")
  [[ -n "${IMESSAGE_MIRROR_DIR:-}" ]] && ARCHIVE_ARGS+=(--mirror-dir "$IMESSAGE_MIRROR_DIR")
  python3 backup_archive.py create "${ARCHIVE_ARGS[@]}"
fi
echo "=== done"
