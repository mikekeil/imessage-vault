---
name: imessage-export
description: Export macOS iMessage/SMS history from ~/Library/Messages/chat.db into a browsable, offline HTML viewer with contact names resolved, attachments included, and spam/marketing conversations flagged. Use this whenever the user asks to export, back up, view, browse, search, or archive their text messages, iMessages, SMS history, or Messages.app data, even if they don't name this tool directly (e.g. "can you get my old texts", "I want to see all my messages with someone", "back up my iMessages before I get a new phone"). Only applies on macOS.
---

# iMessage / SMS export & viewer

This skill runs `extract_imessages.py` (bundled in this repo, next to this
skill directory) to export the user's Messages history into a folder with a
self-contained `viewer.html`, then helps them open it.

## Before running: Full Disk Access

The script needs to read `~/Library/Messages/chat.db`, which macOS blocks
unless the terminal app has Full Disk Access. If the script fails with a
permissions error, tell the user:

1. Open System Settings > Privacy & Security > Full Disk Access
2. Add their terminal app (Terminal.app, iTerm, etc.) and enable it
3. Restart that terminal app, then re-run

Don't try to work around this (e.g. by disabling a sandbox flag). It's a
real macOS permission the user has to grant themselves, and it applies to
whatever terminal process actually runs the script, not to Claude Code
itself.

## Running the export

Find `extract_imessages.py` in this repo (it lives at the repo root, as a
sibling of the `.claude/` directory this skill is under) and run it:

```bash
python3 extract_imessages.py
```

By default this writes everything to `~/iMessage-Export/`, deliberately
*outside* the repo, so exported personal data never risks being committed to
git.

**This runs incrementally by default.** If `~/iMessage-Export/messages.json`
already exists, the script only pulls messages newer than what's already
there and merges them in: fast, and safe to run repeatedly (e.g. as a
recurring backup). The first-ever run, or any run with `--full-rebuild`,
pulls the entire history and copies every attachment, which takes a few
minutes (HEIC photo preview generation and copying files are the slow
parts); mention this to the user before running a full pull so they're not
surprised by the wait. Don't pass `--full-rebuild` by default; only use it
if the user explicitly wants to start over or you suspect the existing
export is corrupted.

Useful flags, pick based on what the user actually wants:
- `--output-dir PATH`: export somewhere other than `~/iMessage-Export`
- `--full-rebuild`: ignore the existing export and pull everything again
  from scratch, instead of just what's new
- `--skip-attachments`: text only, much faster, nothing copied
- `--skip-previews`: copy attachments but skip the slow HEIC preview
  generation step (HEIC photos will still be copied, just not viewable
  inline; the user can still open the original file)
- `--skip-videos`: leave out video attachments, usually the biggest
  contributor to export size
- `--max-image-dim [PIXELS]`: downscale photos to at most this many pixels
  on the longest side (2000 if given with no value) instead of copying
  full-resolution originals. Suggest it when the user is worried about
  export size or upload time; it trades some photo quality for space
- `--skip-contacts`: don't resolve names against Contacts.app
- `--db PATH`: point at a different chat.db, e.g. from a Time Machine
  backup, if the user is trying to recover messages from an old machine

Ask the user which tradeoffs they want only if it matters for their request
(e.g. they said "quickly", suggest `--skip-previews`; they said "I want
photos too", run the full default). Otherwise just run the plain default
command: it's the best experience out of the box, and it self-adjusts to
incremental automatically on repeat runs.

## After it finishes

Tell the user where the export landed and that they can open `viewer.html`
in that folder directly (double-click, or drag it into a browser tab, no
server needed). Mention the sidebar's "Hide automated / marketing" toggle
and that they can correct any conversation it misclassifies with the
"Mark as spam" / "Not spam" button in that conversation's thread view.

The whole output folder (`messages.json`, `messages_data.js`, `viewer.html`,
and the `attachments/` folder with every copied attachment except GIFs,
which are skipped entirely) is self-contained and portable. The user can
zip it, move it to a backup drive, or copy it to another machine and it
still works, since it no longer depends on the originals in
`~/Library/Messages/Attachments`. Worth mentioning if the user's goal is
backup, not just browsing.

If the user's goal is specifically backing up to somewhere like Google
Drive, mention `python3 extract_imessages.py --archive` (or the "Create
Archive Backup" button in `export_panel.py`). It zips the current export
together with a fresh `chat.db` snapshot into one dated file, ready to
upload as a single unit. It doesn't pull new messages itself, so run a
normal export first if they want the archive to include the latest ones.

## Why this doesn't need a local server

`viewer.html` loads its data via a plain `<script src="messages_data.js">`
tag, not `fetch()`, so there's no CORS restriction: it works from a
double-clicked `file://` page. Don't suggest `python3 -m http.server` unless
the user specifically wants to view the export from a different device.

## If the user seems non-technical or resists the command line

There's also `export_panel.py`, a small stdlib-only local web server that
opens a browser page with an "Export My Texts" button, a live progress log,
and buttons to open the viewer or export folder when done. It calls
`extract_imessages.py` the same way this skill does, just with a GUI instead
of a terminal. Offer it (`python3 export_panel.py`) when the user seems put
off by flags or terminal output, rather than walking them through options
they won't remember.

## If something looks wrong in the output

- **A conversation shows the wrong name or a raw phone number**: this is a
  Contacts-resolution limitation (see README's "Known limitations"), not a
  bug to silently patch around. The underlying data may just not have that
  number/email saved.
- **A message shows `[no text, reaction or unreadable message]`**: normal
  for tapback-only or malformed rows. If this happens for the *majority* of
  messages in a conversation, that likely is a real bug; see the "How the
  text decoder works" section of the README before assuming it's expected.
- **A conversation is wrongly flagged (or not flagged) as automated**: this
  is a heuristic, not a bug. Point the user at the manual override in the
  viewer rather than trying to tune the regex for one conversation.

## Scheduled backups, archives, and restoring

If the user wants recurring backups, point them at `install-schedule.sh`
(daily export + monthly archive via launchd; see the README). Pass
`--export-flags` matching the flags their existing export was made with,
or incremental runs will switch to full-size originals. The jobs don't
inherit Terminal's Full Disk Access: they need `/bin/zsh`, the real
`python3` binary (`realpath "$(which python3)"`), and, for framework
Pythons, `Python.app`, each with its toggle turned on. You can't grant
these yourself. Give the user the exact paths, then confirm with the
read-only `TCC.db` query and the `launchctl kickstart` test in the README's
"Full Disk Access for scheduled jobs" section. Don't treat a grant as working
until the log shows `Pulled N new messages`.

To rebuild messages from archives, run
`python3 backup_archive.py restore <zips...> -o <new folder>` (add
`--verify-only` to just check what's recoverable). Any single archive
restores all message text up to its date; attachments come from whichever
archives are present.
