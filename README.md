# iMessage Vault

Export your macOS Messages history (iMessage + SMS) into a self-contained,
searchable, offline viewer that runs in any browser. Everything stays on
your machine; the tools make no network calls and upload nothing (a backup
copy only leaves your machine if you point `--mirror-dir` at a synced folder).

**Read this first:** this tool reads your entire Messages database, which
holds every message and attachment you have. It opens `chat.db` read-only and
never modifies or sends anything, but it needs Full Disk Access to do so, and
the output it creates is sensitive. See [Privacy](#privacy).

Not affiliated with or endorsed by Apple. "iMessage" is a trademark of
Apple Inc.

## What it does

- Reads `~/Library/Messages/chat.db` directly (the SQLite database Messages.app
  itself uses) and every conversation you've ever had.
- **Resolves names** against your local Contacts database, so conversations
  show a real name (e.g. "Jane Smith") instead of a phone number or
  `chat727492075218276533`.
- **Decodes rich-text messages.** A large fraction of iMessages store their
  text in an Apple-internal binary format (`attributedBody`) instead of plain
  text. This recovers the real text with a from-scratch parser (see
  [How the text decoder works](#how-the-text-decoder-works) below).
- **Includes attachments.** Photos, videos, audio, and files are copied
  into the same output folder (GIFs are skipped) and render inline. HEIC
  photos, which no browser can display natively, additionally get a small
  JPEG preview generated automatically via macOS's built-in `sips`.
- **Shows reactions and replies.** Tapbacks (love, like, laugh, custom emoji,
  and so on) appear as badges on the message they react to, and inline replies
  show a quote of the message they answer, instead of separate
  `Loved "..."` lines.
- **Flags likely spam/marketing/OTP conversations** (bank alerts, verification
  codes, shipping notifications) so you can filter them out, with a manual
  override per conversation if the heuristic gets one wrong.
- **Shows message times and status.** Each message shows when it was sent
  or received, plus "Edited", "Delivered", and "Read" times when Messages
  recorded them. Hover a time for the full dates. Read times for messages
  you sent only exist if the other person shares read receipts. Unsent
  messages show as "unsent a message" instead of an empty bubble.
- **Shows group events** ("Jane added Bob to the conversation", renamed
  groups, people leaving, group photo changes, location sharing) as centered
  notes, the way Messages does, instead of empty bubbles.
- **Filters Screen Time requests** ("X asked for more time for ...") and Ask
  to Buy requests ("X asked to get the app ...") out of conversations by
  default, with a dropdown to show them, or to show only
  them. Pass `--skip-screen-time` to leave them out of the export entirely.
- **Runs incrementally by default.** Re-running the script finds the newest
  message already in your export and only pulls what's changed since, so you
  can keep it as a recurring backup without redoing a full pull every time.
  Pass `--full-rebuild` to start over from scratch.
- **Self-contained and portable.** Everything (the data, the viewer, and
  every attachment) lives under `~/iMessage-Vault`. Zip it, move it to a
  backup drive, copy it to another machine: it still works, even after the
  originals in
  `~/Library/Messages/Attachments` are gone (e.g. evicted by iCloud's
  "Optimize Mac Storage").

## Requirements

- macOS (this reads Messages' and Contacts' native databases; won't work on
  other platforms)
- Python 3 (comes preinstalled on macOS)
- **Full Disk Access** for whatever terminal app you run this from:
  System Settings > Privacy & Security > Full Disk Access > add Terminal
  (or iTerm, etc.) and restart it. Without this, the script can't read
  Messages' database at all. Scheduled backups need separate grants; see
  [Full Disk Access for scheduled jobs](#full-disk-access-for-scheduled-jobs).

## Usage

Open Terminal (Applications > Utilities > Terminal), paste this, and press
Enter:

```bash
cd ~/path/to/imessage-vault && python3 export_panel.py
```

(Replace `~/path/to/imessage-vault` with wherever you cloned or downloaded
this repo.) A page opens in your browser with an "Export My Texts" button, a
live progress log, and buttons to open the viewer or the export folder when
it's done. This starts a small local web server (nothing installed, no
external network access beyond your own machine); closing the terminal
window stops it.

There's also a "Create Archive Backup" button. It zips your current export
(everything: messages, attachments, the viewer) together with a fresh
snapshot of `chat.db` itself into one dated file, e.g.
`iMessage-Export-Archive-2026-09-19.zip`, in `cloud-backup/archives/`.
It's self-contained, and it
includes a copy of the raw database in case anything ever needs re-exporting
from scratch. It doesn't pull new messages first, so run "Export My Texts"
beforehand if you want the archive to include your latest messages. Because
it contains the raw database, treat the archive as sensitive as the export
itself (see [Privacy](#privacy)).

The first run pulls your entire history and copies every attachment, so it
takes a while (for ~50,000 messages and ~1,600 HEIC photos, expect several
minutes, mostly spent generating photo previews and copying files). After
that, just run the same command again whenever you want to catch up: it
only pulls messages newer than what's already in the export.

Everything lands in `~/iMessage-Vault/`, deliberately *outside* this repo,
so your exported messages can never end up in git history.

### Folder layout

```
~/iMessage-Vault/
├── attachments/          photos and files: almost all of the size
└── cloud-backup/         back up this folder to Google Drive, iCloud Drive, etc.
    ├── viewer.html       open this to browse your messages
    ├── messages.json
    ├── messages_data.js
    └── archives/         monthly and yearly zips, attachments included
```

Attachments are gigabytes; the message text is tens of MB. Keeping them out
of `cloud-backup/` means a folder backup (for example Google Drive's "Back up
folders from your computer") uploads the text every day without re-scanning
gigabytes of photos. The photos still reach the cloud inside the archive zips,
in `cloud-backup/archives/`: each monthly zip has that month's new attachments,
and a yearly full zip has all of them.

The viewer loads attachments from `../attachments`, so keep the two folders
side by side. The location is recorded in `messages.json`; to use a different
one, pass `--attachments-dir` once (see below) and the files move there.

### Upgrading from 1.x

Before 2.0, everything lived in `~/iMessage-Export/` (attachments included)
and archives in `~/iMessage-Export-Archives/`. Move both into the new layout
once:

```bash
python3 extract_imessages.py --migrate
```

This only renames folders, so it's instant and leaves the files untouched.
Until you run it, exports stop with a message pointing here rather than
starting a second, empty export. Afterwards:

- If you use scheduled backups, re-run `install-schedule.sh` with your usual
  flags. Leave out `--mirror-dir` if it pointed at the same cloud service you
  now back up `cloud-backup/` with, or every archive gets uploaded twice.
- In Google Drive (or similar), stop backing up `~/iMessage-Export` and back
  up `~/iMessage-Vault/cloud-backup` instead. The old copy in the cloud isn't
  deleted for you.
- If your archives were somewhere other than `~/iMessage-Export-Archives`,
  move them into `cloud-backup/archives/` yourself, including the hidden
  `.archive-state.json`.

### More control: the command line

If you're comfortable with flags, you can call the export script directly
instead of going through the browser panel:

```bash
python3 extract_imessages.py
```

```
--output-dir PATH     Where to write the export (default:
                       ~/iMessage-Vault/cloud-backup)
--full-rebuild        Ignore the existing export and pull everything from
                       scratch again, instead of just what's new
--db PATH             Path to chat.db (default: ~/Library/Messages/chat.db,
                       useful if you're pointing this at a backup)
--skip-contacts       Don't resolve names against Contacts
--skip-attachments    Don't include attachments at all
--skip-previews       Copy attachments but skip HEIC->JPEG preview
                       generation (faster, but HEIC photos won't display
                       inline; you can still open the original file)
--skip-videos         Leave out video attachments (.mov/.mp4/etc.), usually
                       the biggest contributor to export size
--skip-screen-time    Leave out Screen Time and Ask to Buy requests ("X
                       asked for more time for ...", "X asked to get the
                       app ..."), and remove any already in the export
                       that are still in chat.db
--max-image-dim [PX]  Downscale photos so the longest side is at most PX
                       pixels (default 2000 if you pass the flag with no
                       value) instead of copying full-resolution originals.
                       Smaller export, some quality loss. HEIC photos are
                       converted to JPEG. Photos already under the limit
                       are copied as-is.
--attachments-dir PATH
                      Keep attachment files in this folder (default:
                       ~/iMessage-Vault/attachments for the default
                       export, otherwise <output-dir>/attachments).
                       Existing attachments are moved there, and later
                       runs remember it, so you only pass it once
--archive             Don't pull new messages -- zip the current export
                       plus a fresh chat.db snapshot into one dated file
                       (run an export first if you haven't)
--archive-dir PATH    Where to write the archive zip (default: archives/
                       inside --output-dir)
--migrate             Move a pre-2.0 export into the ~/iMessage-Vault
                       layout (see "Upgrading from 1.x")
```

## Scheduled backups and archives

For an ongoing backup, `install-schedule.sh` sets up two launchd jobs: a
daily incremental export, and a monthly archive run.

```bash
./install-schedule.sh --export-flags "--max-image-dim 2000"
```

- `--export-flags`: the same flags you made your export with. Incremental
  runs don't remember them, so a mismatch (e.g. leaving out
  `--max-image-dim`) copies full-size originals from then on.
- `--archive-dir`: where archives go (default
  `~/iMessage-Vault/cloud-backup/archives`, so a backup of `cloud-backup/`
  includes them).
- `--mirror-dir`: optional second copy of each archive in another folder,
  such as an external drive. Not needed for a cloud service that already
  backs up `cloud-backup/`.
- `--keep-full N`: full archives to keep (default 2); see below.
- `--python PATH`: the Python the jobs run. Defaults to macOS's own
  `/usr/bin/python3` when the Command Line Tools are installed (otherwise the
  `python3` on your `PATH`), because its Full Disk Access grant survives
  upgrades. See [Choosing a Python that keeps its access](#choosing-a-python-that-keeps-its-access).
- `--uninstall`: remove both jobs.

Logs go to `~/Library/Logs/imessage-vault/`, and a failed run tries to post a
macOS notification. Don't rely on that alone: a notification from a background
job is easy to miss, or may not show at all. See
[Checking that backups are running](#checking-that-backups-are-running). The
jobs need their own Full Disk Access; see the next section before relying on
them.

### Full Disk Access for scheduled jobs

A launchd job does **not** inherit Terminal's Full Disk Access. An export that
works from Terminal can still fail every night with this in
`~/Library/Logs/imessage-vault/daily.log`:

```
Couldn't read chat.db (likely a permissions issue).
Underlying error: unable to open database file
```

The job runs `/bin/zsh`, which runs `python3`, and macOS may check either
one. Grant all of these:

| Grant | How to find it |
| --- | --- |
| `/bin/zsh` | Always this path |
| The real `python3` binary, not the symlink | `realpath "$(which python3)"` (Homebrew example: `/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/bin/python3.14`) |
| `Python.app`, for Homebrew/python.org framework builds | The same `Python.framework/Versions/3.x/` folder, under `Resources/Python.app` |

On a Homebrew Python, granting only `/bin/zsh` and `Python.app` was not
enough; the job started working once the `bin/python3.x` binary was enabled
too.

**To add each one:** System Settings > Privacy & Security > Full Disk Access >
**+**, press **Cmd-Shift-G**, paste the path, and click Open. **Then check that
its toggle is on.** An entry can be added in the off state, and an entry
that's in the list but switched off looks set up but does nothing.

**Check what's granted** from a terminal that already has Full Disk Access
(read-only; `auth_value` 2 means on, 0 means off):

```bash
sqlite3 "file:/Library/Application Support/com.apple.TCC/TCC.db?mode=ro" \
  "select client, auth_value from access where service='kTCCServiceSystemPolicyAllFiles';"
```

**Test it:** start the job now instead of waiting for its schedule, then read
the log once it finishes. `-k` stops a run that's already going, so run this
once and then wait:

```bash
launchctl kickstart -k gui/$(id -u)/local.imessage-vault.daily
sleep 15; tail -8 ~/Library/Logs/imessage-vault/daily.log
```

A working run prints `Pulled N new messages; ...` followed by `=== done`.
Check the timestamp on the last `=== ... daily backup` line: if you read the
log too soon, you're looking at the previous run.

### Choosing a Python that keeps its access

A Full Disk Access grant belongs to one specific binary, not to a name. A
symlink such as `/opt/homebrew/bin/python3` doesn't help: macOS checks the
real file it points to, and `brew upgrade` (even a patch release) installs a
new file in a new versioned folder. The grant stays with the old one, and the
jobs fail silently every night from then on until someone notices.

The installer therefore defaults to `/usr/bin/python3`, the Python that ships
with Apple's Command Line Tools (`xcode-select --install`). It's
Apple-signed and changes only with macOS or Command Line Tools updates, so its
grant holds. The scripts use only the standard library and run on its
Python 3.9. Grant both paths the installer prints: `/usr/bin/python3` and the
real binary it runs
(`/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9`).

If you'd rather keep a Homebrew or python.org Python, pass `--python` with its
path, and after every Python upgrade grant the new real binary and test again.
[`check-backup.sh`](#checking-that-backups-are-running) tells you when it
breaks.

Already installed with an older version of this repo? Re-run
`install-schedule.sh` with your usual flags to switch the jobs to the stable
Python, then grant the printed paths.

Granting `/bin/zsh` lets any zsh script read your Messages. To narrow that,
grant only the Python entries and change the plist's `ProgramArguments` to
call `python3` directly.

**For AI agents setting this up:** an agent can't grant Full Disk Access; a
person has to click through System Settings. Find the exact paths, give them
to the person, and use the read-only `TCC.db` query above to confirm the
toggles are on, instead of assuming a grant worked. A sandboxed agent may
also be able to read `chat.db` but not write to `~/iMessage-Vault`. If an
export fails with `PermissionError` on `messages.json`, that's the agent's
sandbox, not Full Disk Access; have the person run the command.

### Checking that backups are running

A backup that stopped weeks ago looks the same as one that ran last night,
until you need it. `check-backup.sh` reads only the job logs and file sizes
(never your messages, so it needs no Full Disk Access) and prints one line per
check:

```
$ ./check-backup.sh
OK    daily: last run 2026-11-02 03:15:00 finished
OK    monthly: last run 2026-11-01 04:15:00 finished
OK    archive: iMessage-Export-Monthly-2026-11-01-60087-61210.zip, 1 days old
OK    mirror: iMessage-Export-Monthly-2026-11-01-60087-61210.zip is in /path/to/mirror
```

It checks that the last daily and monthly runs finished, that the daily job
ran within 2 days (`--max-age-days`), that the newest archive is at most 35
days old (`--max-archive-age-days`), and that the archive is in your
`--mirror-dir` at the same size. It reads the folders from the installed
jobs. It exits 1 if any check fails, so you can hook it into whatever already
alerts you, for example a daily script that posts a notification or opens a
task:

```bash
~/path/to/imessage-vault/check-backup.sh > /tmp/imessage-check.txt || \
  osascript -e 'display notification "iMessage backup is failing" with title "iMessage Vault"'
```

### How the archives stay small but redundant

`backup_archive.py` writes two kinds of zip, each with a manifest:

| Archive | When | Contains |
| --- | --- | --- |
| Full | Once a year (January), or the first run ever | All message text, every attachment, a `chat.db` snapshot |
| Monthly | Every other month | All message text, plus only the attachments of messages added since the last archive |

All message text is only a few MB compressed, so every archive carries all
of it: **any single archive restores every message up to its date.** Losing
a monthly archive loses only that month's photos, never text. Once more than
`--keep-full` full archives exist, older archives are deleted, since
everything in them is also in a newer full archive.

Rebuild a normal, viewable export from whatever archives you have:

```bash
python3 backup_archive.py restore ~/iMessage-Vault/cloud-backup/archives/*.zip -o ~/iMessage-Restored
python3 backup_archive.py restore ... --verify-only   # report only, write nothing
```

Restore merges the text from every archive (so messages you later deleted
from Messages are still kept), collects attachments from all of them, and
reports any ID ranges or attachment files it couldn't recover. It also
accepts zips made by the panel's "Create Archive Backup" button.

The `chat.db` snapshot in each full archive is there because the export
can't capture everything: edit history, read receipts, and message effects
live only in the database, and it lets you re-export with a fixed decoder
later (`extract_imessages.py --db chat-backup.db`).

## Using it as a Claude Code Skill

This repo also ships a [Claude Code](https://claude.com/claude-code) skill
(`.claude/skills/imessage-export/`). If you have Claude Code, just clone this
repo and open it: "export my texts" or "show me my iMessage history" will
trigger the skill automatically, and Claude will walk you through Full Disk
Access if needed.

## Privacy

- Nothing leaves your machine. There's no network call anywhere in this
  project.
- Everything under `~/iMessage-Vault` (`messages.json`, `messages_data.js`,
  `attachments/`, the archive zips) is your actual message content and
  media; treat it like the sensitive personal data it is. `.gitignore` in
  this repo blocks it from ever being committed, but that only protects
  *this* repo. Archive zips hold your message text and attachments (and, for
  full archives, a copy of `chat.db`). If you back up `cloud-backup/` to a
  cloud service, or use `--mirror-dir` with a cloud folder, that copy is
  stored by that provider.
- The spam/automated flag and any manual overrides you set in the viewer are
  stored in your browser's local storage, scoped to the file, not synced or
  uploaded anywhere.

## How the text decoder works

When Messages has no plain `text` for a row, the content lives in
`attributedBody`, an `NSAttributedString` archived with the legacy NeXTSTEP
"typedstream" format (not a binary plist, despite what you'd guess). There's
no public parser for this format in Python, so `extract_imessages.py` walks
the raw bytes looking for length-prefixed runs that decode as UTF-8, discards
the ones known to be archiver plumbing (class names, attribute keys,
data-detector metadata, attachment GUIDs, embedded binary plists), and keeps
the longest survivor as the message text.

This is a best-effort reverse-engineering of an undocumented, private Apple
format. It's been checked against tens of thousands of real messages and
gets the overwhelming majority right, but on rare messages it may return
nothing or something odd. If you find a message that decodes wrong, please
open an issue with the message's `ROWID` from `chat.db` (never the message
content itself).

## Known limitations

- The "likely automated/marketing" heuristic is a simple keyword/pattern
  match. It can misfire on short real conversations that happen to mention a
  matching phrase. Use the "Mark as spam" / "Not spam" toggle in the viewer
  to correct it; your override is remembered.
- Attachments whose files have already been evicted from disk *before you
  ever export them* (e.g. by iCloud's "Optimize Mac Storage") show as
  unavailable. The reference to them still exists in the database, but the
  actual file is gone, so there's nothing to copy. Export sooner rather than
  later if you're relying on this as a backup.
- Group chat "participants" come from Messages' own handle records, which
  are sometimes split across a person's phone number and email/Apple ID as
  if they were different people, if Contacts doesn't have both linked.
- Incremental mode tracks progress by message ID, not by re-scanning
  everything. If you ever edit `messages.json` by hand or restore an older
  copy over a newer one, use `--full-rebuild` once to get back to a known
  consistent state.

## License

MIT. See [LICENSE](LICENSE).
