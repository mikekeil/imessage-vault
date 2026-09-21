#!/usr/bin/env python3
"""A simple browser-based control panel for exporting iMessage/SMS history,
for people who don't want to open Terminal or learn command-line flags.

Starts a local web server (stdlib only, nothing installed) and opens your
browser to it. Wraps extract_imessages.py; doesn't duplicate its logic.
Nothing here talks to the network beyond your own machine.
"""

import json
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
EXPORT_SCRIPT = SCRIPT_DIR / "extract_imessages.py"
DEFAULT_OUTPUT_DIR = Path.home() / "iMessage-Export"
FDA_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
PORT_RANGE = range(8765, 8785)


class ExportState:
    """Shared, thread-safe state for the one export job this panel can run."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.log_lines = []
        self.returncode = None
        self.started = False  # has a job ever been kicked off this session
        self.job = None  # "export" or "archive"
        self.archive_path = None

    def reset_for_start(self, job):
        with self.lock:
            self.running = True
            self.started = True
            self.job = job
            self.log_lines = []
            self.returncode = None

    def append_line(self, line):
        with self.lock:
            self.log_lines.append(line)

    def finish(self, returncode, archive_path=None):
        with self.lock:
            self.running = False
            self.returncode = returncode
            if archive_path:
                self.archive_path = archive_path

    def snapshot(self, since=0):
        with self.lock:
            return {
                "running": self.running,
                "started": self.started,
                "returncode": self.returncode,
                "job": self.job,
                "archive_path": self.archive_path,
                "new_lines": self.log_lines[since:],
                "total_lines": len(self.log_lines),
            }


state = ExportState()


def run_export(full_rebuild):
    state.reset_for_start("export")
    args = [sys.executable, str(EXPORT_SCRIPT)]
    if full_rebuild:
        args.append("--full-rebuild")
    try:
        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        for line in process.stdout:
            state.append_line(line.rstrip("\n"))
        returncode = process.wait()
    except OSError as e:
        state.append_line(f"Failed to start export: {e}")
        returncode = 1
    state.finish(returncode)


def run_archive():
    state.reset_for_start("archive")
    args = [sys.executable, str(EXPORT_SCRIPT), "--archive"]
    archive_path = None
    try:
        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        for line in process.stdout:
            line = line.rstrip("\n")
            state.append_line(line)
            if line.startswith("Archive created: "):
                archive_path = line[len("Archive created: "):].rsplit(" (", 1)[0]
        returncode = process.wait()
    except OSError as e:
        state.append_line(f"Failed to start archive: {e}")
        returncode = 1
    state.finish(returncode, archive_path)


PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>iMessage Vault</title>
<style>
  :root {
    --bg: #f5f5f7; --panel: #ffffff; --border: #e2e2e5; --text: #1c1c1e;
    --text-secondary: #6e6e73; --accent: #007aff; --accent-dark: #0060c0;
    --danger: #ff3b30;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1c1c1e; --panel: #232326; --border: #38383a; --text: #f2f2f2;
      --text-secondary: #9a9a9e; --accent: #0a84ff; --accent-dark: #409cff;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px; max-width: 720px; margin-inline: auto;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: var(--bg); color: var(--text);
  }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .subtitle { color: var(--text-secondary); margin: 0 0 20px; font-size: 14px; }
  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    padding: 16px 18px; margin-bottom: 16px;
  }
  .fda-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .fda-row p { margin: 0; font-size: 13px; color: var(--text-secondary); }
  button {
    font: inherit; border-radius: 8px; border: 1px solid var(--border);
    background: var(--panel); color: var(--text); padding: 8px 14px; cursor: pointer;
  }
  button:hover:not(:disabled) { filter: brightness(0.96); }
  button:disabled { opacity: 0.5; cursor: default; }
  #export-btn {
    background: var(--accent); color: white; border: none; font-weight: 600;
    font-size: 15px; padding: 12px 20px;
  }
  #export-btn:hover:not(:disabled) { background: var(--accent-dark); }
  .controls { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  .checkbox-label { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text-secondary); }
  #status { margin: 14px 0 8px; font-size: 13px; color: var(--text-secondary); min-height: 18px; }
  #progress-track {
    width: 100%; height: 6px; border-radius: 3px; background: var(--border);
    overflow: hidden; margin-bottom: 14px; display: none;
  }
  #progress-bar {
    width: 40%; height: 100%; background: var(--accent); border-radius: 3px;
    animation: indeterminate 1.2s ease-in-out infinite;
  }
  @keyframes indeterminate {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(250%); }
  }
  #log {
    background: #1c1c1e; color: #d6d6d6; border-radius: 10px; padding: 12px 14px;
    font-family: "SF Mono", Menlo, monospace; font-size: 12.5px; line-height: 1.5;
    height: 260px; overflow-y: auto; white-space: pre-wrap; word-break: break-word;
  }
  .result-actions { display: flex; gap: 10px; margin-top: 14px; }
</style>
</head>
<body>
  <h1>iMessage Vault</h1>
  <p class="subtitle">Back up your iMessage and SMS history to a private folder on this Mac.</p>

  <div class="card fda-row">
    <p>Needs Full Disk Access for Terminal. If the export fails below, grant it here and try again.</p>
    <button id="fda-btn">Open Settings</button>
  </div>

  <div class="card">
    <div class="controls">
      <button id="export-btn">Export My Texts</button>
      <label class="checkbox-label">
        <input type="checkbox" id="full-rebuild"> Start over from scratch (slower)
      </label>
    </div>
    <div class="controls" style="margin-top: 10px;">
      <button id="archive-btn">Create Archive Backup</button>
      <span style="font-size: 13px; color: var(--text-secondary);">
        Zips your export plus a fresh database snapshot into one dated file, ready for Google Drive.
      </span>
    </div>
    <div id="status"></div>
    <div id="progress-track"><div id="progress-bar"></div></div>
    <div id="log"></div>
    <div class="result-actions">
      <button id="open-viewer-btn" disabled>Open Viewer</button>
      <button id="open-folder-btn" disabled>Show Export Folder</button>
      <button id="open-archive-btn" disabled>Show Archive in Finder</button>
    </div>
  </div>

<script>
const exportBtn = document.getElementById('export-btn');
const archiveBtn = document.getElementById('archive-btn');
const fullRebuildEl = document.getElementById('full-rebuild');
const statusEl = document.getElementById('status');
const progressTrack = document.getElementById('progress-track');
const logEl = document.getElementById('log');
const openViewerBtn = document.getElementById('open-viewer-btn');
const openFolderBtn = document.getElementById('open-folder-btn');
const openArchiveBtn = document.getElementById('open-archive-btn');
const fdaBtn = document.getElementById('fda-btn');

let seenLines = 0;
let polling = null;

fdaBtn.addEventListener('click', () => fetch('/api/open-fda-settings', { method: 'POST' }));
openViewerBtn.addEventListener('click', () => fetch('/api/open-viewer', { method: 'POST' }));
openFolderBtn.addEventListener('click', () => fetch('/api/open-folder', { method: 'POST' }));
openArchiveBtn.addEventListener('click', () => fetch('/api/open-archive-folder', { method: 'POST' }));

function startJob(runningStatusText) {
  exportBtn.disabled = true;
  archiveBtn.disabled = true;
  statusEl.textContent = runningStatusText;
  progressTrack.style.display = 'block';
  logEl.textContent = '';
  seenLines = 0;
  openViewerBtn.disabled = true;
  openFolderBtn.disabled = true;
  openArchiveBtn.disabled = true;
}

exportBtn.addEventListener('click', async () => {
  startJob('Exporting, this can take a while on the first run...');
  exportBtn.textContent = 'Exporting...';

  await fetch('/api/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ full_rebuild: fullRebuildEl.checked }),
  });
  startPolling();
});

archiveBtn.addEventListener('click', async () => {
  startJob('Backing up the database and zipping your export...');
  archiveBtn.textContent = 'Archiving...';

  await fetch('/api/archive', { method: 'POST' });
  startPolling();
});

function startPolling() {
  if (polling) return;
  polling = setInterval(pollStatus, 600);
  pollStatus();
}

async function pollStatus() {
  const res = await fetch(`/api/status?since=${seenLines}`);
  const data = await res.json();

  if (data.new_lines.length) {
    logEl.textContent += data.new_lines.join('\\n') + '\\n';
    logEl.scrollTop = logEl.scrollHeight;
    seenLines = data.total_lines;
  }

  if (data.running) {
    exportBtn.disabled = true;
    archiveBtn.disabled = true;
    if (data.job === 'archive') {
      archiveBtn.textContent = 'Archiving...';
    } else {
      exportBtn.textContent = 'Exporting...';
    }
    progressTrack.style.display = 'block';
    return;
  }

  clearInterval(polling);
  polling = null;
  exportBtn.disabled = false;
  exportBtn.textContent = 'Export My Texts';
  archiveBtn.disabled = false;
  archiveBtn.textContent = 'Create Archive Backup';
  progressTrack.style.display = 'none';

  if (!data.started) {
    return;
  }

  if (data.returncode === 0) {
    if (data.job === 'archive') {
      statusEl.textContent = 'Done. Your archive is ready.';
      openArchiveBtn.disabled = !data.archive_path;
    } else {
      statusEl.textContent = 'Done. Your export is ready.';
      openViewerBtn.disabled = false;
      openFolderBtn.disabled = false;
    }
  } else {
    const fullLog = logEl.textContent;
    if (fullLog.includes('Full Disk Access')) {
      statusEl.textContent = 'Needs Full Disk Access. Click "Open Settings" above, enable it for Terminal, then try again.';
    } else {
      statusEl.textContent = 'Something went wrong. See the log below.';
    }
  }
}

// Resume showing progress if an export was already running when this page loaded
(async () => {
  const res = await fetch('/api/status');
  const data = await res.json();
  if (data.total_lines > 0) {
    logEl.textContent = data.new_lines.join('\\n') + '\\n';
    logEl.scrollTop = logEl.scrollHeight;
    seenLines = data.total_lines;
  }
  if (data.running) {
    exportBtn.disabled = true;
    archiveBtn.disabled = true;
    if (data.job === 'archive') {
      archiveBtn.textContent = 'Archiving...';
      statusEl.textContent = 'Backing up the database and zipping your export...';
    } else {
      exportBtn.textContent = 'Exporting...';
      statusEl.textContent = 'Exporting, this can take a while on the first run...';
    }
    progressTrack.style.display = 'block';
    startPolling();
  } else if (data.started && data.returncode === 0) {
    if (data.job === 'archive') {
      statusEl.textContent = 'Done. Your archive is ready.';
      openArchiveBtn.disabled = !data.archive_path;
    } else {
      statusEl.textContent = 'Done. Your export is ready.';
      openViewerBtn.disabled = false;
      openFolderBtn.disabled = false;
    }
  }
})();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # keep the terminal quiet; the web page has its own status area

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _is_trusted_request(self):
        """The server only listens on loopback, but any web page open in your
        browser can still send requests to localhost, and a hostile DNS name can
        be pointed at 127.0.0.1. Accept only requests addressed to a loopback
        host, and (when the browser sends an Origin) only from this panel's own page."""
        host = self.headers.get("Host") or ""
        if host.rsplit(":", 1)[0] not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin == f"http://{host}"

    def _forbid(self):
        self.send_response(403)
        self.end_headers()

    def do_GET(self):
        if not self._is_trusted_request():
            return self._forbid()
        if self.path == "/" or self.path == "":
            body = PAGE_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/status"):
            since = 0
            if "since=" in self.path:
                try:
                    since = int(self.path.split("since=", 1)[1].split("&", 1)[0])
                except ValueError:
                    since = 0
            self._send_json(state.snapshot(since))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if not self._is_trusted_request():
            return self._forbid()
        if self.path == "/api/export":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = {}
            full_rebuild = bool(payload.get("full_rebuild"))

            if not EXPORT_SCRIPT.exists():
                self._send_json({"ok": False, "error": "extract_imessages.py not found"}, status=500)
                return
            if state.snapshot()["running"]:
                self._send_json({"ok": False, "error": "already running"}, status=409)
                return

            threading.Thread(target=run_export, args=(full_rebuild,), daemon=True).start()
            self._send_json({"ok": True})
        elif self.path == "/api/archive":
            if not EXPORT_SCRIPT.exists():
                self._send_json({"ok": False, "error": "extract_imessages.py not found"}, status=500)
                return
            if state.snapshot()["running"]:
                self._send_json({"ok": False, "error": "already running"}, status=409)
                return

            threading.Thread(target=run_archive, daemon=True).start()
            self._send_json({"ok": True})
        elif self.path == "/api/open-archive-folder":
            archive_path = state.snapshot()["archive_path"]
            if archive_path and Path(archive_path).exists():
                subprocess.run(["open", "-R", archive_path])
                self._send_json({"ok": True})
            else:
                self._send_json({"ok": False, "error": "no archive yet"}, status=404)
        elif self.path == "/api/open-viewer":
            viewer = DEFAULT_OUTPUT_DIR / "viewer.html"
            if viewer.exists():
                subprocess.run(["open", str(viewer)])
                self._send_json({"ok": True})
            else:
                self._send_json({"ok": False, "error": "viewer.html not found yet"}, status=404)
        elif self.path == "/api/open-folder":
            if DEFAULT_OUTPUT_DIR.exists():
                subprocess.run(["open", str(DEFAULT_OUTPUT_DIR)])
            self._send_json({"ok": True})
        elif self.path == "/api/open-fda-settings":
            subprocess.run(["open", FDA_SETTINGS_URL])
            self._send_json({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()


def find_open_port():
    import socket
    for port in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("Couldn't find a free local port to run on.")


def main():
    port = find_open_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"iMessage Vault control panel running at {url}")
    print("Press Ctrl+C to stop.")
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
