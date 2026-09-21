#!/usr/bin/env python3
"""Export macOS Messages history (chat.db) into a self-contained folder you
can browse in any web browser.

Runs incrementally by default: on repeat runs it finds the newest message
already in your export and only pulls messages after that, merging them in.
Pass --full-rebuild to ignore the existing export and start over.

Resolves sender/participant names against the local Contacts database (all
sources under ~/Library/Application Support/AddressBook), flags chats that
look like automated/marketing/OTP traffic, and copies every attachment
(except GIFs) into the same output folder so the whole export is a single,
portable directory -- move it, zip it, put it on a backup drive, and it still
works, even after the originals in ~/Library/Messages/Attachments are gone.
HEIC photos additionally get a JPEG preview generated, since no browser can
display HEIC natively.
"""

import argparse
import glob
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

APPLE_EPOCH = datetime(2001, 1, 1)
DEFAULT_DB = Path.home() / "Library" / "Messages" / "chat.db"
DEFAULT_OUTPUT_DIR = Path.home() / "iMessage-Export"
CONTACTS_GLOB = str(
    Path.home() / "Library" / "Application Support" / "AddressBook" / "Sources" / "*" / "AddressBook-v22.abcddb"
)
ATTACHMENTS_DIRNAME = "attachments"

SKIP_EXTS = {".gif"}  # not worth the space -- excluded entirely, not just hidden
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heics"}
NEEDS_PREVIEW_EXTS = {".heic", ".heics"}
VIDEO_EXTS = {".mov", ".mp4", ".3gp", ".m4v"}
AUDIO_EXTS = {".m4a", ".caf", ".amr"}


def classify_attachment(ext):
    ext = ext.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"


def apple_time_to_iso(raw):
    if not raw:
        return None
    # Modern macOS stores nanoseconds since the Apple epoch; older versions used seconds.
    seconds = raw / 1e9 if raw > 1e13 else raw
    try:
        return (APPLE_EPOCH + timedelta(seconds=seconds)).isoformat()
    except (OverflowError, OSError):
        return None


# ---------------------------------------------------------------------------
# attributedBody decoding
#
# When Messages has no plain `text` for a row, the content instead lives in
# `attributedBody`, an NSAttributedString archived with the legacy NeXTSTEP
# "typedstream" format (magic bytes "\x04\x0bstreamtyped", NOT a binary
# plist). There's no public parser for this on generic Python, so this walks
# the blob looking for length-prefixed byte runs that decode as UTF-8, then
# discards the ones we know are archiver plumbing (class names, attribute
# keys, data-detector scan results, attachment GUIDs, single-byte ObjC type
# codes that show up near-universally) and keeps the longest survivor.
# Best-effort: some rare messages may still resolve to noise or nothing.
# ---------------------------------------------------------------------------

_META_EXACT = {
    "streamtyped", "NSString", "NSMutableString", "NSAttributedString",
    "NSMutableAttributedString", "NSDictionary", "NSMutableDictionary",
    "NSArray", "NSMutableArray", "NSNumber", "NSValue", "NSObject",
    "NSURL", "NSUUID", "NSData", "NSMutableData", "NSNull", "NSColor",
    "NSFont", "NSParagraphStyle", "NSTextAttachment", "NSFontManager",
    "NSKeyedArchiver", "iI", "\x0bstr",
    # single-char ObjC type-encoding codes that appear as scan artifacts
    # in effectively every blob's fixed archive-version header
    "@", "#", ":", "*", "+", "c", "i", "s", "l", "q", "f", "d", "B", "v",
    "C", "I", "S", "L", "Q", "F", "D", "r", "R", "N", "n", "^",
}
_UUID_RE = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")


def _typedstream_candidates(blob):
    # Everything from the first embedded binary plist onward is structured
    # metadata (data-detector results, calendar events, etc.), never the
    # human-visible message text -- and its bytes can coincidentally decode
    # as long, garbage-looking "text" that would otherwise win by length.
    bplist_at = blob.find(b"bplist00")
    if bplist_at != -1:
        blob = blob[:bplist_at]
    out = []
    n = len(blob)
    i = 0
    while i < n:
        b = blob[i]
        length = start = None
        if 1 <= b <= 0x7F:
            length, start = b, i + 1
        elif b == 0x81 and i + 3 <= n:
            length = int.from_bytes(blob[i + 1:i + 3], "little")
            start = i + 3
        if length and start is not None and start + length <= n:
            try:
                s = blob[start:start + length].decode("utf-8")
                if s:
                    out.append(s)
            except UnicodeDecodeError:
                pass
        i += 1
    return out


def _is_typedstream_meta(s):
    if s.startswith("__kIM") or s.startswith("__kMB"):
        return True
    if "NS." in s or "rangeval" in s or "DDScannerResult" in s:
        return True
    if s in _META_EXACT:
        return True
    if _UUID_RE.search(s):
        return True
    if s.startswith("at_") and "_" in s[3:]:
        return True
    control = sum(1 for ch in s if ord(ch) < 0x20 and ch not in "\n\t\r")
    if control / len(s) > 0.1:
        return True
    return False


def extract_text_from_attributed_body(blob):
    if not blob:
        return None
    real = [s for s in _typedstream_candidates(blob) if not _is_typedstream_meta(s)]
    if not real:
        return None
    real.sort(key=len, reverse=True)
    return real[0]


# ---------------------------------------------------------------------------
# Contacts resolution
# ---------------------------------------------------------------------------

def normalize_phone(raw):
    digits = re.sub(r"\D", "", raw or "")
    return digits[-10:] if len(digits) >= 10 else digits


def load_contacts():
    """Merge every Contacts source DB into phone/email -> name maps, plus a
    list of (first, last) pairs for fuzzy email-local-part matching."""
    phone_map, email_map, name_pairs = {}, {}, []

    for path in glob.glob(CONTACTS_GLOB):
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.OperationalError:
            continue

        try:
            records = {
                row["Z_PK"]: row
                for row in conn.execute(
                    "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZNICKNAME, ZORGANIZATION FROM ZABCDRECORD"
                )
            }

            def full_name(rec):
                first, last = rec["ZFIRSTNAME"], rec["ZLASTNAME"]
                if first or last:
                    return " ".join(p for p in (first, last) if p)
                return rec["ZNICKNAME"] or rec["ZORGANIZATION"] or None

            for row in conn.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                rec = records.get(row["ZOWNER"])
                name = full_name(rec) if rec else None
                if name:
                    norm = normalize_phone(row["ZFULLNUMBER"])
                    if norm:
                        phone_map[norm] = name

            for row in conn.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
                rec = records.get(row["ZOWNER"])
                name = full_name(rec) if rec else None
                if name and row["ZADDRESS"]:
                    email_map[row["ZADDRESS"].lower()] = name

            for rec in records.values():
                first, last = rec["ZFIRSTNAME"], rec["ZLASTNAME"]
                if first and last:
                    name_pairs.append((first, last, full_name(rec)))
        finally:
            conn.close()

    return phone_map, email_map, name_pairs


def resolve_name(identifier, phone_map, email_map, name_pairs):
    if not identifier:
        return None
    if "@" in identifier:
        exact = email_map.get(identifier.lower())
        if exact:
            return exact
        local = identifier.split("@", 1)[0].lower()
        tokens = set(re.split(r"[^a-z]+", local)) - {""}
        for first, last, full in name_pairs:
            if first.lower() in tokens and last.lower() in tokens:
                return full
        return None
    return phone_map.get(normalize_phone(identifier))


# ---------------------------------------------------------------------------
# Automated / marketing / OTP detection
# ---------------------------------------------------------------------------

_AUTOMATED_PATTERNS = re.compile(
    r"verification code|one[- ]time (password|passcode|code)|\botp\b|security code|"
    r"autopay|account ending in|unsubscribe|reply stop|text stop|alert:|"
    r"your .*(code|otp) is|msg (and|&) data rates|do not reply|fraud alert|"
    r"confirm(ation)? code|delivery (attempt|update)|has shipped|order (has|is) (shipped|out)",
    re.IGNORECASE,
)


def is_short_code(identifier):
    return bool(identifier) and identifier.isdigit() and len(identifier) <= 6


def compute_is_automated(identifier, has_contact_match, messages):
    if is_short_code(identifier):
        return True
    if has_contact_match:
        return False
    if not messages:
        return False
    hits = sum(1 for m in messages if m["text"] and _AUTOMATED_PATTERNS.search(m["text"]))
    return hits / len(messages) > 0.3


# ---------------------------------------------------------------------------
# Attachments
#
# Every attachment (except GIFs, which are skipped entirely) is copied into
# ATTACHMENTS_DIRNAME inside the output folder, so the export is a single,
# self-contained, movable directory -- it no longer depends on the originals
# in ~/Library/Messages/Attachments still existing. HEIC/HEIC-sequence images
# additionally get a JPEG preview generated (via macOS's built-in `sips`),
# since no browser can display HEIC natively.
# ---------------------------------------------------------------------------

def image_longest_side(path: Path):
    try:
        out = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    sides = [int(n) for n in re.findall(r"pixel(?:Width|Height): (\d+)", out)]
    return max(sides) if sides else None


def downscale_image(src: Path, dest: Path, max_dim: int, to_jpeg: bool) -> bool:
    """Write a copy of src at dest that's at most max_dim pixels on its longest
    side (converting HEIC to JPEG if to_jpeg). Returns False if nothing was
    written -- the image is already small enough, or sips couldn't handle it --
    so the caller can copy the original instead. Checks the size first because
    `sips -Z` enlarges small images rather than leaving them alone."""
    longest = image_longest_side(src)
    if longest is None or (longest <= max_dim and not to_jpeg):
        return False
    cmd = ["sips"]
    if to_jpeg:
        cmd += ["-s", "format", "jpeg"]
    if longest > max_dim:
        cmd += ["-Z", str(max_dim)]
    cmd += [str(src), "--out", str(dest)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        dest.unlink(missing_ok=True)
        return False
    return dest.exists()


def load_attachments_by_message(conn, attachments_dir: Path, skip_previews: bool, min_message_id: int,
                                 skip_videos: bool = False, max_image_dim: int = None):
    rows = conn.execute(
        """
        SELECT
            maj.message_id AS message_id,
            a.ROWID AS attachment_id,
            a.filename AS filename,
            a.mime_type AS mime_type,
            a.transfer_name AS transfer_name,
            a.total_bytes AS total_bytes
        FROM message_attachment_join maj
        JOIN attachment a ON maj.attachment_id = a.ROWID
        WHERE maj.message_id > ?
        """,
        (min_message_id,),
    ).fetchall()

    by_message = {}
    attachments_dir.mkdir(parents=True, exist_ok=True)
    copied, resized, previewed, skipped_gif, skipped_video, failed = 0, 0, 0, 0, 0, 0
    total = len(rows)

    for idx, row in enumerate(rows, start=1):
        if total > 200 and idx % 200 == 0:
            print(f"Processing attachments: {idx}/{total}...", file=sys.stderr, flush=True)

        raw_path = row["filename"]
        src = Path(raw_path).expanduser() if raw_path else None
        ext = src.suffix.lower() if src else ""

        if ext in SKIP_EXTS:
            skipped_gif += 1
            continue
        if skip_videos and ext in VIDEO_EXTS:
            skipped_video += 1
            continue

        available = bool(src and src.exists())
        kind = classify_attachment(ext)
        safe_name = f"{row['attachment_id']}_{src.name}" if src else f"{row['attachment_id']}_attachment"

        entry = {
            "name": row["transfer_name"] or (src.name if src else "attachment"),
            "kind": kind,
            "mime_type": row["mime_type"],
            "size": row["total_bytes"],
            "path": None,
            "preview_path": None,
            "available": available,
        }

        resized_dest = None
        if available and max_image_dim and kind == "image":
            # Downscale photos in place, one file per attachment, instead of copying a
            # full-resolution original -- HEIC still has to become JPEG for browsers to
            # show it at all, so there's no separate "original" worth keeping alongside.
            # If that isn't possible (already small, or sips can't read it), fall through
            # to the plain copy below rather than losing the attachment.
            is_heic = ext in NEEDS_PREVIEW_EXTS
            candidate = attachments_dir / (f"{row['attachment_id']}_{src.stem}.jpg" if is_heic else safe_name)
            if candidate.exists():
                resized_dest = candidate
            elif downscale_image(src, candidate, max_image_dim, is_heic):
                resized_dest = candidate
                resized += 1

        if resized_dest is not None:
            entry["path"] = f"{ATTACHMENTS_DIRNAME}/{resized_dest.name}"
        elif available:
            dest = attachments_dir / safe_name
            if not dest.exists():
                try:
                    shutil.copy2(src, dest)
                    copied += 1
                except OSError:
                    dest = None
                    entry["available"] = False
            if dest is not None and dest.exists():
                entry["path"] = f"{ATTACHMENTS_DIRNAME}/{dest.name}"

            if entry["path"] and not skip_previews and ext in NEEDS_PREVIEW_EXTS:
                preview_dest = attachments_dir / f"{row['attachment_id']}_preview.jpg"
                if not preview_dest.exists():
                    try:
                        subprocess.run(
                            ["sips", "-s", "format", "jpeg", "-Z", "1200", str(src), "--out", str(preview_dest)],
                            check=True, capture_output=True, timeout=30,
                        )
                        previewed += 1
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
                        failed += 1
                if preview_dest.exists():
                    entry["preview_path"] = f"{ATTACHMENTS_DIRNAME}/{preview_dest.name}"

        by_message.setdefault(row["message_id"], []).append(entry)

    if copied or resized or previewed or skipped_gif or skipped_video or failed:
        parts = []
        if copied:
            parts.append(f"copied {copied}")
        if resized:
            parts.append(f"downscaled {resized} photos")
        if previewed:
            parts.append(f"generated {previewed} HEIC previews")
        if failed:
            parts.append(f"{failed} failed")
        if skipped_gif:
            parts.append(f"skipped {skipped_gif} GIFs")
        if skipped_video:
            parts.append(f"skipped {skipped_video} videos")
        print("Attachments: " + ", ".join(parts), file=sys.stderr, flush=True)
    return by_message


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def open_db(db_path: Path):
    if not db_path.exists():
        sys.exit(f"Can't find {db_path}. Pass --db to point at your chat.db.")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("SELECT count(*) FROM message LIMIT 1")
        return conn
    except sqlite3.OperationalError as e:
        sys.exit(
            "Couldn't read chat.db (likely a permissions issue).\n"
            "Grant Full Disk Access to your terminal app:\n"
            "  System Settings > Privacy & Security > Full Disk Access\n"
            "  add Terminal (or iTerm/whatever you're running this from), then re-run.\n"
            f"Underlying error: {e}"
        )


def pull_new_chats(db_path: Path, output_dir: Path, skip_contacts: bool, skip_attachments: bool,
                    skip_previews: bool, min_message_id: int, skip_videos: bool = False,
                    max_image_dim: int = None):
    """Pull every message with ROWID > min_message_id (0 for a full pull),
    grouped by chat. Does not compute derived per-chat fields (is_automated,
    _last_date, etc.) -- call finalize_chats() after merging with any
    existing export for that, since those depend on the full message list."""
    conn = open_db(db_path)
    conn.row_factory = sqlite3.Row

    if skip_contacts:
        phone_map, email_map, name_pairs = {}, {}, []
    else:
        phone_map, email_map, name_pairs = load_contacts()
        print(f"Loaded {len(phone_map)} phone and {len(email_map)} email contact matches", file=sys.stderr, flush=True)

    if skip_attachments:
        attachments_by_message = {}
    else:
        attachments_dir = output_dir / ATTACHMENTS_DIRNAME
        attachments_by_message = load_attachments_by_message(
            conn, attachments_dir, skip_previews, min_message_id, skip_videos, max_image_dim
        )

    participants_by_chat = {}
    for row in conn.execute(
        "SELECT chj.chat_id, h.id AS identifier FROM chat_handle_join chj JOIN handle h ON chj.handle_id = h.ROWID"
    ):
        participants_by_chat.setdefault(row["chat_id"], []).append(row["identifier"])

    rows = conn.execute(
        """
        SELECT
            message.ROWID AS message_id,
            message.guid AS message_guid,
            message.text AS text,
            message.attributedBody AS attributed_body,
            message.date AS date,
            message.is_from_me AS is_from_me,
            handle.id AS sender,
            chat.ROWID AS chat_id,
            chat.guid AS chat_guid,
            chat.display_name AS chat_display_name,
            chat.chat_identifier AS chat_identifier
        FROM message
        LEFT JOIN handle ON message.handle_id = handle.ROWID
        LEFT JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
        LEFT JOIN chat ON chat_message_join.chat_id = chat.ROWID
        WHERE message.ROWID > ?
        ORDER BY chat.ROWID, message.date ASC
        """,
        (min_message_id,),
    ).fetchall()

    name_cache = {}

    def name_for(identifier):
        if identifier not in name_cache:
            name_cache[identifier] = resolve_name(identifier, phone_map, email_map, name_pairs)
        return name_cache[identifier]

    max_message_id = min_message_id
    chats = {}
    for row in rows:
        chat_id = row["chat_id"]
        max_message_id = max(max_message_id, row["message_id"])
        if chat_id is None:
            continue  # message not linked to any chat (rare, skip)

        if chat_id not in chats:
            identifier = row["chat_identifier"]
            participants = participants_by_chat.get(chat_id, [])
            resolved_participants = [
                {"identifier": p, "name": name_for(p)} for p in participants
            ]

            display_name = row["chat_display_name"] or None
            if display_name:
                name = display_name
            elif len(participants) == 1:
                name = name_for(participants[0]) or identifier or row["chat_guid"]
            elif resolved_participants:
                labels = [p["name"] or p["identifier"] for p in resolved_participants]
                name = ", ".join(labels[:3]) + (f" +{len(labels) - 3} more" if len(labels) > 3 else "")
            else:
                name = identifier or row["chat_guid"]

            chats[chat_id] = {
                "chat_id": chat_id,
                "name": name,
                "identifier": identifier,
                "participants": resolved_participants,
                "messages": [],
            }

        text = row["text"]
        if not text or not text.strip():
            text = extract_text_from_attributed_body(row["attributed_body"])

        sender_identifier = None if row["is_from_me"] else row["sender"]
        chats[chat_id]["messages"].append({
            "id": row["message_id"],
            "guid": row["message_guid"],
            "date": apple_time_to_iso(row["date"]),
            "from_me": bool(row["is_from_me"]),
            "sender": "Me" if row["is_from_me"] else sender_identifier,
            "sender_name": None if row["is_from_me"] else (name_for(sender_identifier) or sender_identifier),
            "text": text,
            "attachments": attachments_by_message.get(row["message_id"], []),
        })

    conn.close()
    return chats, max_message_id, name_for


def merge_chats(existing_chats, new_chats):
    """existing_chats/new_chats are both {chat_id: chat_dict}. New chat
    metadata (name, participants) wins, since it reflects the latest state
    (e.g. a group's display name may have changed); messages are appended."""
    merged = dict(existing_chats)
    for chat_id, new_chat in new_chats.items():
        if chat_id in merged:
            merged[chat_id]["messages"].extend(new_chat["messages"])
            merged[chat_id]["name"] = new_chat["name"]
            merged[chat_id]["identifier"] = new_chat["identifier"]
            merged[chat_id]["participants"] = new_chat["participants"]
        else:
            merged[chat_id] = new_chat
    return merged


def finalize_chats(chats, name_for):
    for chat in chats.values():
        has_contact_match = any(p["name"] for p in chat["participants"]) or bool(name_for(chat["identifier"]))
        chat["is_automated"] = compute_is_automated(chat["identifier"], has_contact_match, chat["messages"])
        msgs = chat["messages"]
        last = msgs[-1] if msgs else None
        chat["_last_date"] = last["date"] if last else None
        chat["_last_preview"] = (last["text"] or "")[:120] if last else ""
        chat["_count"] = len(msgs)
    return chats


def export(db_path: Path, output_dir: Path, skip_contacts: bool, skip_attachments: bool,
           skip_previews: bool, full_rebuild: bool, skip_videos: bool = False, max_image_dim: int = None):
    output_dir.mkdir(parents=True, exist_ok=True)
    existing_path = output_dir / "messages.json"

    existing = None
    if existing_path.exists() and not full_rebuild:
        try:
            existing = json.loads(existing_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = None

    min_message_id = existing.get("last_message_id", 0) if existing else 0
    if existing:
        print(f"Incremental export: pulling messages after ROWID {min_message_id}", file=sys.stderr, flush=True)
    else:
        print("Full export: pulling entire message history", file=sys.stderr, flush=True)

    new_chats, max_message_id, name_for = pull_new_chats(
        db_path, output_dir, skip_contacts, skip_attachments, skip_previews, min_message_id,
        skip_videos, max_image_dim
    )

    if existing:
        existing_chats = {c["chat_id"]: c for c in existing["chats"]}
        all_chats = merge_chats(existing_chats, new_chats)
    else:
        all_chats = new_chats

    all_chats = finalize_chats(all_chats, name_for)

    data = {
        "exported_at": datetime.now().isoformat(),
        "source_db": str(db_path),
        "chat_count": len(all_chats),
        "last_message_id": max_message_id,
        "chats": sorted(all_chats.values(), key=lambda c: c["chat_id"]),
    }

    existing_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    data_js = output_dir / "messages_data.js"
    with data_js.open("w", encoding="utf-8") as f:
        f.write("window.MESSAGES_DATA = ")
        json.dump(data, f, ensure_ascii=False)
        f.write(";")

    viewer_src = Path(__file__).resolve().parent / "viewer.html"
    if viewer_src.exists():
        shutil.copy(viewer_src, output_dir / "viewer.html")

    new_message_count = sum(len(c["messages"]) for c in new_chats.values())
    total_messages = sum(len(c["messages"]) for c in data["chats"])
    automated = sum(1 for c in data["chats"] if c["is_automated"])
    print(f"Pulled {new_message_count} new messages; {total_messages} total across {len(data['chats'])} conversations in {output_dir}")
    print(f"Flagged {automated} conversations as likely automated/marketing")
    print(f"\nOpen {output_dir / 'viewer.html'} in a browser to view.")


def backup_database(db_path: Path, dest_path: Path) -> None:
    """Snapshot chat.db with SQLite's own backup API, not a plain file copy --
    Messages.app can have it open in WAL mode, so a raw `cp` mid-write risks
    an inconsistent copy. This produces one valid, self-contained .db file."""
    if not db_path.exists():
        sys.exit(f"Can't find {db_path}. Pass --db to point at your chat.db.")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dest = sqlite3.connect(dest_path)
    try:
        source.backup(dest)
    except sqlite3.OperationalError as e:
        sys.exit(
            "Couldn't read chat.db to back it up (likely a permissions issue).\n"
            "Grant Full Disk Access to your terminal app:\n"
            "  System Settings > Privacy & Security > Full Disk Access\n"
            "  add Terminal (or iTerm/whatever you're running this from), then re-run.\n"
            f"Underlying error: {e}"
        )
    finally:
        source.close()
        dest.close()


def create_archive(output_dir: Path, db_path: Path, archive_path: Path) -> Path:
    """Zip the current export folder together with a fresh chat.db snapshot
    into one dated archive -- a single file that's easy to upload to Google
    Drive or any other backup, and self-contained even if the export and the
    live chat.db later diverge."""
    if not output_dir.exists():
        sys.exit(f"No export found at {output_dir}. Run an export first.")

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        db_backup_path = Path(tmp) / "chat-backup.db"
        print("Backing up chat.db...", file=sys.stderr, flush=True)
        backup_database(db_path, db_backup_path)

        print(f"Zipping {output_dir} plus the database backup...", file=sys.stderr, flush=True)
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in output_dir.rglob("*"):
                # skip the zip itself if --archive-dir points inside the export folder
                if file.is_file() and file.resolve() != archive_path.resolve():
                    zf.write(file, Path(output_dir.name) / file.relative_to(output_dir))
            zf.write(db_backup_path, Path(output_dir.name) / "chat-backup.db")

    size_mb = archive_path.stat().st_size / (1024 * 1024)
    print(f"Archive created: {archive_path} ({size_mb:.1f} MB)", file=sys.stderr, flush=True)
    return archive_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to chat.db")
    parser.add_argument(
        "--output-dir", "-o", type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Folder to write the export into (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--full-rebuild", action="store_true",
        help="Ignore any existing export in the output folder and re-pull everything from scratch "
             "(default is incremental: only pull messages newer than what's already exported)",
    )
    parser.add_argument(
        "--skip-contacts", action="store_true",
        help="Don't resolve names against the local Contacts database",
    )
    parser.add_argument(
        "--skip-attachments", action="store_true",
        help="Don't include attachments at all",
    )
    parser.add_argument(
        "--skip-previews", "--skip-thumbnails", dest="skip_previews", action="store_true",
        help="Copy attachments but don't generate HEIC->JPEG previews",
    )
    parser.add_argument(
        "--skip-videos", action="store_true",
        help="Don't include video attachments (.mov/.mp4/etc.) at all -- usually the "
             "single largest contributor to export size",
    )
    parser.add_argument(
        "--max-image-dim", type=int, nargs="?", const=2000, default=None, metavar="PIXELS",
        help="Downscale photos to at most this many pixels on the longest side, "
             "recompressing as needed instead of copying full-resolution originals. "
             "Cuts photo storage substantially at some quality cost. Defaults to 2000 "
             "if passed with no value; omit entirely to keep full-resolution originals.",
    )
    parser.add_argument(
        "--archive", action="store_true",
        help="Don't pull new messages -- just zip the current export together with a fresh "
             "chat.db snapshot into one dated file, ready to upload to Google Drive or "
             "another backup (run an export first if you haven't)",
    )
    parser.add_argument(
        "--archive-dir", type=Path, default=None,
        help="Folder to write the archive zip into (default: next to --output-dir)",
    )
    args = parser.parse_args()

    if args.archive:
        archive_dir = args.archive_dir or args.output_dir.parent
        date_str = datetime.now().strftime("%Y-%m-%d")
        archive_path = archive_dir / f"{args.output_dir.name}-Archive-{date_str}.zip"
        create_archive(args.output_dir, args.db, archive_path)
        return

    export(args.db, args.output_dir, args.skip_contacts, args.skip_attachments,
           args.skip_previews, args.full_rebuild, args.skip_videos, args.max_image_dim)


if __name__ == "__main__":
    main()
