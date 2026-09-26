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
- **Runs incrementally by default.** Re-running the script finds the newest
  message already in your export and only pulls what's changed since, so you
  can keep it as a recurring backup without redoing a full pull every time.
  Pass `--full-rebuild` to start over from scratch.
- **Self-contained and portable.** Everything (the data, the viewer, and
  every attachment) lives in one folder. Zip it, move it to a backup drive,
  copy it to another machine: it still works, even after the originals in
  `~/Library/Messages/Attachments` are gone (e.g. evicted by iCloud's
  "Optimize Mac Storage").

## Requirements

- macOS (this reads Messages' and Contacts' native databases; won't work on
  other platforms)
- Python 3 (comes preinstalled on macOS)
- **Full Disk Access** for whatever terminal app you run this from:
  System Settings > Privacy & Security > Full Disk Access > add Terminal
  (or iTerm, etc.) and restart it. Without this, the script can't read
  Messages' database at all.

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
`iMessage-Export-Archive-2026-09-19.zip`, next to your export folder. That's
the file to upload to Google Drive or another backup: self-contained, and it
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

Everything lands in `~/iMessage-Export/`, deliberately *outside* this repo,
so your exported messages can never end up in git history.

### More control: the command line

If you're comfortable with flags, you can call the export script directly
instead of going through the browser panel:

```bash
python3 extract_imessages.py
```

```
--output-dir PATH     Where to write the export (default: ~/iMessage-Export)
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
--max-image-dim [PX]  Downscale photos so the longest side is at most PX
                       pixels (default 2000 if you pass the flag with no
                       value) instead of copying full-resolution originals.
                       Smaller export, some quality loss. HEIC photos are
                       converted to JPEG. Photos already under the limit
                       are copied as-is.
--archive             Don't pull new messages -- zip the current export
                       plus a fresh chat.db snapshot into one dated file
                       (run an export first if you haven't)
--archive-dir PATH    Where to write the archive zip (default: next to
                       --output-dir)
```

## Scheduled backups and archives

For an ongoing backup, `install-schedule.sh` sets up two launchd jobs: a
daily incremental export, and a monthly archive run.

```bash
./install-schedule.sh --export-flags "--max-image-dim 2000" \
    --mirror-dir "/path/to/any/synced/or/backup/folder"
```

- `--export-flags`: the same flags you made your export with. Incremental
  runs don't remember them, so a mismatch (e.g. leaving out
  `--max-image-dim`) copies full-size originals from then on.
- `--archive-dir`: where archives go (default `~/iMessage-Export-Archives`).
- `--mirror-dir`: optional second copy in any folder you choose (an external
  drive, or a Google Drive / iCloud Drive / Dropbox folder). Leave it out to
  keep everything local.
- `--keep-full N`: full archives to keep (default 2); see below.
- `--uninstall`: remove both jobs.

The jobs run `/bin/zsh`, so it needs Full Disk Access (System Settings >
Privacy & Security > Full Disk Access > + > Cmd-Shift-G > `/bin/zsh`). Logs
go to `~/Library/Logs/imessage-vault/`, and a failed run posts a macOS
notification.

**"Couldn't read chat.db" in the scheduled log?** Full Disk Access is granted
per process, and a launchd job doesn't inherit Terminal's. Add `/bin/zsh` as
above; if it still fails, also add the real `python3` the script uses (run
`which python3`; pick the binary, not a symlink). Then re-run with
`launchctl kickstart -k gui/$(id -u)/local.imessage-vault.daily` and check
`~/Library/Logs/imessage-vault/daily.log`. Granting `/bin/zsh` lets any zsh
script read your Messages; to avoid that, grant only `python3` and point the
plist at it directly.

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
python3 backup_archive.py restore ~/iMessage-Export-Archives/*.zip -o ~/iMessage-Restored
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
- The exported `messages.json` / `messages_data.js` / `attachments/` contain
  your actual message content and media; treat that output folder like the
  sensitive personal data it is. `.gitignore` in this repo blocks it from
  ever being committed, but that only protects *this* repo, not wherever you
  choose to back up the export folder itself. The same goes for archive
  zips, which hold your message text (and, for full archives, a copy of
  `chat.db`). If you use `--mirror-dir` with a cloud folder, that copy is
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
