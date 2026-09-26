#!/usr/bin/env python3
"""
Space-efficient, redundant archives of an iMessage Vault export.

Two kinds of archive, both self-describing zips with a manifest:

  full     everything: all message text, every attachment, and a chat.db
           snapshot. Made once a year (or on demand); the anchor for the chain.
  monthly  all message text (a few MB, so any single archive restores every
           message up to its date) plus only the attachments of messages added
           since the previous archive.

So losing any one archive costs at most that month's photos/videos, never
text. `restore` rebuilds a normal, viewable export from whatever archives you
have, and reports anything it couldn't recover.

  python3 backup_archive.py create --archive-dir ~/iMessage-Export-Archives
  python3 backup_archive.py restore ~/iMessage-Export-Archives/*.zip -o ~/restored

`create` picks the kind automatically: full if the folder has no full archive
yet or none from this year once --full-month has come around, otherwise
monthly. Force one with --kind. Where the last archive ended is kept in
<archive-dir>/.archive-state.json.

Like incremental export, new messages are tracked by ID: an edit or unsend
made after a message was archived isn't reflected in older archives.
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from extract_imessages import (
    ATTACHMENTS_DIRNAME, DEFAULT_DB, DEFAULT_OUTPUT_DIR, IMAGE_EXTS, VIDEO_EXTS, AUDIO_EXTS,
    backup_database,
)

MANIFEST_NAME = "archive_manifest.json"
LEGACY_DELTA_MANIFEST = "delta_manifest.json"
STATE_NAME = ".archive-state.json"
FORMAT_VERSION = 2
ARCHIVE_RE = re.compile(r"-(Full|Monthly)-\d{4}-\d{2}-\d{2}-(\d+)-(\d+)\.zip$")
# Already-compressed media: storing instead of deflating saves a lot of CPU for ~0 size.
STORED_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS | {".jpg"}


def attachment_paths(message):
    for att in message.get("attachments", []):
        for key in ("path", "preview_path"):
            if att.get(key):
                yield att[key]


def read_state(archive_dir: Path):
    path = archive_dir / STATE_NAME
    return json.loads(path.read_text()) if path.exists() else None


def write_state(archive_dir: Path, last_id: int, archive_name: str, last_full_year=None):
    state = read_state(archive_dir) or {}
    state.update({
        "last_archived_message_id": last_id,
        "last_archive": archive_name,
        "updated_at": datetime.now().isoformat(),
    })
    if last_full_year is not None:
        state["last_full_year"] = last_full_year
    (archive_dir / STATE_NAME).write_text(json.dumps(state, indent=2))


def listed_archives(archive_dir: Path):
    """(kind, from_id, to_id, path) for archives named by this tool, oldest first."""
    found = []
    for p in archive_dir.glob("*.zip"):
        m = ARCHIVE_RE.search(p.name)
        if m:
            found.append((m.group(1).lower(), int(m.group(2)), int(m.group(3)), p))
    return sorted(found, key=lambda a: (a[2], a[0] != "full"))


def choose_kind(archive_dir: Path, full_month: int):
    state = read_state(archive_dir) or {}
    has_full = any(k == "full" for k, *_ in listed_archives(archive_dir)) or state.get("last_full_year")
    if not has_full:
        return "full"
    now = datetime.now()
    if now.month >= full_month and state.get("last_full_year", 0) < now.year:
        return "full"
    return "monthly"


def write_zip(path: Path, manifest, data, output_dir: Path, files, db_path):
    tmp_path = path.with_name(path.name + ".partial")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
        zf.writestr("messages.json", json.dumps(data, ensure_ascii=False))
        for rel in sorted(files):
            method = zipfile.ZIP_STORED if Path(rel).suffix.lower() in STORED_EXTS else zipfile.ZIP_DEFLATED
            zf.write(output_dir / rel, rel, compress_type=method)
        if db_path:
            with tempfile.TemporaryDirectory() as tmp:
                db_copy = Path(tmp) / "chat-backup.db"
                backup_database(db_path, db_copy)
                zf.write(db_copy, "chat-backup.db")
    # Verify every member's CRC before the archive counts: a truncated or corrupt
    # zip must never advance the chain.
    with zipfile.ZipFile(tmp_path) as zf:
        bad = zf.testzip()
    if bad:
        tmp_path.unlink()
        sys.exit(f"Archive verification failed at {bad}; nothing was recorded.")
    tmp_path.rename(path)


def create(output_dir: Path, archive_dir: Path, kind: str, full_month: int, db_path: Path,
           since_id=None):
    export_path = output_dir / "messages.json"
    if not export_path.exists():
        sys.exit(f"No export found at {export_path}. Run extract_imessages.py first.")
    archive_dir.mkdir(parents=True, exist_ok=True)
    if kind == "auto":
        kind = choose_kind(archive_dir, full_month)

    data = json.loads(export_path.read_text())
    to_id = data["last_message_id"]

    if kind == "full":
        since_id = 0
    elif since_id is None:
        state = read_state(archive_dir)
        if state is None:
            sys.exit(f"No {STATE_NAME} in {archive_dir}: make a full archive first (--kind full).")
        since_id = state["last_archived_message_id"]
        if to_id <= since_id:
            print(f"Nothing new since message {since_id}; no archive written.")
            return None

    all_files = {f for c in data["chats"] for m in c["messages"] for f in attachment_paths(m)}
    new_files = all_files if kind == "full" else {
        f for c in data["chats"] for m in c["messages"] if m["id"] > since_id for f in attachment_paths(m)
    }
    missing = sorted(f for f in new_files if not (output_dir / f).exists())
    files = new_files - set(missing)
    new_messages = sum(1 for c in data["chats"] for m in c["messages"] if m["id"] > since_id)

    manifest = {
        "format_version": FORMAT_VERSION,
        "kind": kind,
        "attachments_from_message_id_exclusive": since_id,
        "to_message_id_inclusive": to_id,
        "created_at": datetime.now().isoformat(),
        "export_exported_at": data.get("exported_at"),
        "total_messages": sum(len(c["messages"]) for c in data["chats"]),
        "new_messages": new_messages,
        "attachment_file_count": len(files),
        "missing_attachment_files": missing,
        "includes_chat_db": kind == "full",
    }
    label = "Full" if kind == "full" else "Monthly"
    stamp = datetime.now().strftime("%Y-%m-%d")
    path = archive_dir / f"{output_dir.name}-{label}-{stamp}-{since_id + 1}-{to_id}.zip"
    write_zip(path, manifest, data, output_dir, files, db_path if kind == "full" else None)
    write_state(archive_dir, to_id, path.name, datetime.now().year if kind == "full" else None)

    size_mb = path.stat().st_size / (1024 * 1024)
    print(f"{label} archive: {path} ({size_mb:.1f} MB)")
    print(f"  all {manifest['total_messages']} messages' text; {new_messages} new; "
          f"{len(files)} attachment files" + (f"; {len(missing)} referenced files missing" if missing else ""))
    return path


def mirror(path: Path, mirror_dir: Path):
    """Copy a finished archive to a second location (e.g. a synced cloud folder)."""
    mirror_dir.mkdir(parents=True, exist_ok=True)
    dest = mirror_dir / path.name
    tmp = mirror_dir / (path.name + ".partial")
    shutil.copyfile(path, tmp)
    if tmp.stat().st_size != path.stat().st_size:
        tmp.unlink()
        sys.exit(f"Mirror copy to {mirror_dir} came out the wrong size; removed it.")
    tmp.rename(dest)
    src_state = path.parent / STATE_NAME
    if src_state.exists():
        shutil.copyfile(src_state, mirror_dir / STATE_NAME)
    print(f"Mirrored to {dest}")


def prune(archive_dir: Path, keep_full: int, dry_run: bool = False):
    """Keep the newest `keep_full` full archives. Anything older than the oldest
    kept full archive is redundant: its text is in every later archive and its
    attachments are in that full archive."""
    archives = listed_archives(archive_dir)
    fulls = [a for a in archives if a[0] == "full"]
    if len(fulls) <= keep_full:
        return []
    kept = fulls[-keep_full:]
    cutoff = kept[0][2]
    doomed = [a[3] for a in archives if a not in kept and (a[0] == "full" or a[2] < cutoff)]
    for p in doomed:
        print(("Would remove " if dry_run else "Removing ") + p.name)
        if not dry_run:
            p.unlink()
    return doomed


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def load_archive(path: Path):
    """Return a dict describing any archive this project has produced:
    v2 full/monthly, the earlier delta format, or extract_imessages --archive."""
    zf = zipfile.ZipFile(path)
    names = set(zf.namelist())
    if MANIFEST_NAME in names or LEGACY_DELTA_MANIFEST in names:
        legacy = MANIFEST_NAME not in names
        manifest = json.loads(zf.read(LEGACY_DELTA_MANIFEST if legacy else MANIFEST_NAME))
        data = json.loads(zf.read("messages.json"))
        return {
            "path": path, "zip": zf, "prefix": "", "data": data,
            "kind": "delta-v1" if legacy else manifest["kind"],
            "att_from": manifest.get("attachments_from_message_id_exclusive",
                                     manifest.get("from_message_id_exclusive")),
            "to": manifest["to_message_id_inclusive"],
            # v1 deltas held only the new messages' text, v2 archives hold all of it
            "text_from": manifest.get("from_message_id_exclusive") if legacy else 0,
        }
    top = [n for n in names if n.endswith("/messages.json") and n.count("/") == 1]
    if not top:
        sys.exit(f"{path} isn't an archive this tool recognizes.")
    data = json.loads(zf.read(top[0]))
    return {"path": path, "zip": zf, "prefix": top[0].rsplit("/", 1)[0] + "/", "data": data,
            "kind": "full", "att_from": 0, "to": data["last_message_id"], "text_from": 0}


def coverage_gaps(archives, key_from, upto):
    """Message-ID ranges (lo, hi] not covered by any archive's [key_from, to] range."""
    gaps, covered = [], 0
    for a in sorted(archives, key=lambda a: a[key_from]):
        if a[key_from] > covered:
            gaps.append((covered, a[key_from]))
        covered = max(covered, a["to"])
    return gaps + ([(covered, upto)] if covered < upto else [])


def restore(paths, output_dir: Path, verify_only: bool):
    if output_dir.exists() and any(output_dir.iterdir()) and not verify_only:
        sys.exit(f"{output_dir} isn't empty; restore into a new folder.")

    archives = sorted((load_archive(p) for p in paths), key=lambda a: a["to"])
    latest = archives[-1]["to"]
    problems = 0

    # Text: union of every archive, oldest first, so newer copies of a message
    # win and messages later deleted from Messages are still kept.
    chats, seen = {}, {}
    for a in archives:
        for chat in a["data"]["chats"]:
            cid = chat["chat_id"]
            if cid not in chats:
                chats[cid] = {**chat, "messages": []}
                seen[cid] = {}
            else:
                for key in ("name", "identifier", "participants", "is_automated"):
                    if key in chat:
                        chats[cid][key] = chat[key]
            for m in chat["messages"]:
                # a message can be linked to more than one chat, so key per chat
                if m["id"] in seen[cid]:
                    chats[cid]["messages"][seen[cid][m["id"]]] = m
                else:
                    seen[cid][m["id"]] = len(chats[cid]["messages"])
                    chats[cid]["messages"].append(m)

    for gap in coverage_gaps(archives, "text_from", latest):
        problems += 1
        print(f"WARNING: no text for message IDs {gap[0] + 1}..{gap[1]}")
    for gap in coverage_gaps(archives, "att_from", latest):
        print(f"WARNING: attachments for message IDs {gap[0] + 1}..{gap[1]} aren't in these archives "
              "(missing monthly archive?)")

    available = {}
    for a in archives:
        attach_prefix = a["prefix"] + ATTACHMENTS_DIRNAME + "/"
        for name in a["zip"].namelist():
            if name.startswith(attach_prefix) and not name.endswith("/"):
                available[name[len(a["prefix"]):]] = (a["zip"], name)
    referenced = {p for c in chats.values() for m in c["messages"] for p in attachment_paths(m)}
    missing = sorted(referenced - set(available))
    if missing:
        problems += 1

    for chat in chats.values():
        # IDs and timestamps don't always agree, so merged messages can interleave;
        # put each chat back in date order (stable for equal dates).
        chat["messages"].sort(key=lambda m: m["date"] or "")
        msgs = chat["messages"]
        last = msgs[-1] if msgs else None
        chat["_last_date"] = last["date"] if last else None
        chat["_last_preview"] = (last["text"] or "")[:120] if last else ""
        chat["_count"] = len(msgs)

    total = sum(len(c["messages"]) for c in chats.values())
    print(f"{len(archives)} archives through message {latest}: {total} messages in {len(chats)} chats; "
          f"{len(referenced) - len(missing)}/{len(referenced)} attachment files recoverable")
    for m in missing[:10]:
        print(f"  missing: {m}")
    if len(missing) > 10:
        print(f"  ...and {len(missing) - 10} more")

    if verify_only:
        return 1 if problems else 0

    # Show lost files as "unavailable" in the viewer instead of broken images.
    lost = set(missing)
    for chat in chats.values():
        for m in chat["messages"]:
            for att in m.get("attachments", []):
                if att.get("path") in lost:
                    att.update(path=None, preview_path=None, available=False)
                elif att.get("preview_path") in lost:
                    att["preview_path"] = None

    output_dir.mkdir(parents=True, exist_ok=True)
    for rel in referenced - set(missing):
        zf, name = available[rel]
        dest = output_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(name) as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out)
    dbs = [a for a in archives if "chat-backup.db" in {n[len(a["prefix"]):] for n in a["zip"].namelist()}]
    if dbs:
        a = dbs[-1]
        with a["zip"].open(a["prefix"] + "chat-backup.db") as src, (output_dir / "chat-backup.db").open("wb") as out:
            shutil.copyfileobj(src, out)

    out = {
        "exported_at": datetime.now().isoformat(),
        "source_db": f"restored from {len(archives)} archives",
        "chat_count": len(chats),
        "last_message_id": latest,
        "chats": sorted(chats.values(), key=lambda c: c["chat_id"]),
    }
    (output_dir / "messages.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    with (output_dir / "messages_data.js").open("w", encoding="utf-8") as f:
        f.write("window.MESSAGES_DATA = ")
        json.dump(out, f, ensure_ascii=False)
        f.write(";")
    viewer_src = Path(__file__).resolve().parent / "viewer.html"
    if viewer_src.exists():
        shutil.copy(viewer_src, output_dir / "viewer.html")
    print(f"Restored to {output_dir}. Open {output_dir / 'viewer.html'} to view.")
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="Write a full or monthly archive")
    c.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_OUTPUT_DIR, help="The export folder")
    c.add_argument("--archive-dir", type=Path, required=True, help="Where archives and chain state live")
    c.add_argument("--kind", choices=["auto", "monthly", "full"], default="auto")
    c.add_argument("--full-month", type=int, default=1,
                   help="Month (1-12) from which auto makes that year's full archive (default: January)")
    c.add_argument("--mirror-dir", type=Path, default=None,
                   help="Also copy the archive here, e.g. a Google Drive or iCloud Drive folder")
    c.add_argument("--keep-full", type=int, default=2,
                   help="Full archives to keep; older, redundant archives are deleted (0 = never prune)")
    c.add_argument("--db", type=Path, default=DEFAULT_DB)

    r = sub.add_parser("restore", help="Rebuild an export from any set of archives")
    r.add_argument("archives", type=Path, nargs="+")
    r.add_argument("--output-dir", "-o", type=Path, required=True)
    r.add_argument("--verify-only", action="store_true", help="Report what's recoverable; write nothing")

    args = parser.parse_args()
    if args.cmd == "create":
        archive_dir = args.archive_dir.expanduser()
        path = create(args.output_dir.expanduser(), archive_dir, args.kind, args.full_month, args.db)
        if path and args.mirror_dir:
            mirror(path, args.mirror_dir.expanduser())
        if path and args.keep_full:
            prune(archive_dir, args.keep_full)
            if args.mirror_dir:
                prune(args.mirror_dir.expanduser(), args.keep_full)
    else:
        sys.exit(restore(args.archives, args.output_dir.expanduser(), args.verify_only))


if __name__ == "__main__":
    main()
