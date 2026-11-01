#!/bin/zsh
# Health check for the scheduled backups set up by install-schedule.sh.
# Prints one line per check, "OK" or "FAIL", and exits 1 if any check fails, so
# a monitor (cron, launchd, a morning script) can alert on it.
# Reads only the job logs and file sizes, never your messages, so it needs no
# Full Disk Access.
#
#   ./check-backup.sh [--max-age-days 2] [--max-archive-age-days 35]
#
# Checks:
#   daily    the last daily run finished, and ran within --max-age-days
#   monthly  the last monthly run finished
#   archive  the newest archive is at most --max-archive-age-days old
#   mirror   that archive is also in the mirror folder, at the same size
# Folders come from the installed launchd job, or IMESSAGE_ARCHIVE_DIR and
# IMESSAGE_MIRROR_DIR if set.

set -uo pipefail
LOGS="$HOME/Library/Logs/imessage-vault"
PREFIX="${IMESSAGE_LAUNCHD_PREFIX:-local.imessage-vault}"
PLIST="$HOME/Library/LaunchAgents/$PREFIX.monthly.plist"
MAX_AGE=2 MAX_ARCHIVE_AGE=35
while (( $# )); do
  case "$1" in
    --max-age-days)         MAX_AGE="$2"; shift 2 ;;
    --max-archive-age-days) MAX_ARCHIVE_AGE="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

plist_env() { [[ -f "$PLIST" ]] && plutil -extract "EnvironmentVariables.$1" raw -o - "$PLIST" 2>/dev/null; }
ARCHIVE_DIR="${IMESSAGE_ARCHIVE_DIR:-$(plist_env IMESSAGE_ARCHIVE_DIR)}"
ARCHIVE_DIR="${ARCHIVE_DIR:-$HOME/iMessage-Vault/cloud-backup/archives}"
MIRROR_DIR="${IMESSAGE_MIRROR_DIR:-$(plist_env IMESSAGE_MIRROR_DIR)}"

NOW=$(date +%s)
FAILED=0
ok()  { print -r -- "OK    $*"; }
bad() { print -r -- "FAIL  $*"; FAILED=1; }
days_since() { print $(( (NOW - $1) / 86400 )); }

# Find the last "=== <date> <mode> backup" header in the log, and whether a
# "=== done" line follows it.
check_job() {
  local mode=$1 max=$2 log="$LOGS/$1.log" header line stamp when age reason
  if [[ ! -f "$log" ]]; then bad "$mode: no log at $log (has the job ever run?)"; return; fi
  header=$(grep -n "^=== [0-9-]* [0-9:]* $mode backup" "$log" | tail -1)
  if [[ -z "$header" ]]; then bad "$mode: no runs in $log"; return; fi
  line=${header%%:*}
  stamp=$(sed -E 's/^[0-9]+:=== ([0-9-]+ [0-9:]+).*/\1/' <<< "$header")
  when=$(date -j -f '%Y-%m-%d %H:%M:%S' "$stamp" +%s 2>/dev/null || print 0)
  age=$(days_since $when)
  if ! tail -n "+$line" "$log" | grep -q '^=== done'; then
    reason=$(tail -n "+$line" "$log" | grep -m1 -E 'Underlying error|Error|Traceback|failed' || true)
    bad "$mode: last run $stamp did not finish${reason:+: $reason} (or is still running)"
  elif [[ -n "$max" ]] && (( age > max )); then
    bad "$mode: last run $stamp is $age days old (limit $max); is the job loaded?"
  else
    ok "$mode: last run $stamp finished"
  fi
}

check_job daily "$MAX_AGE"
check_job monthly ""

newest=$(ls -t "$ARCHIVE_DIR"/iMessage-Export-*.zip 2>/dev/null | head -1)
if [[ -z "$newest" ]]; then
  bad "archive: none in $ARCHIVE_DIR"
else
  age=$(days_since $(stat -f %m "$newest"))
  if (( age > MAX_ARCHIVE_AGE )); then
    bad "archive: newest is ${newest:t}, $age days old (limit $MAX_ARCHIVE_AGE)"
  else
    ok "archive: ${newest:t}, $age days old"
  fi
  if [[ -n "$MIRROR_DIR" ]]; then
    copy="$MIRROR_DIR/${newest:t}"
    if [[ ! -e "$copy" ]]; then
      bad "mirror: ${newest:t} is not in $MIRROR_DIR"
    elif [[ $(stat -f %z "$copy") != $(stat -f %z "$newest") ]]; then
      bad "mirror: ${newest:t} in $MIRROR_DIR is a different size (still syncing?)"
    else
      ok "mirror: ${newest:t} is in $MIRROR_DIR"
    fi
  fi
fi

exit $FAILED
