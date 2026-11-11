# SPDX-License-Identifier: GPL-3.0-or-later
"""A small web server on this computer only, so that the buttons' reports share one browser tab.

A browser tab cannot be closed from outside, and a page opened from a file can neither tell anyone it is still open
nor see that the file changed. So the buttons hand each report to this server (show()), and the tab it serves asks it
every second whether there is a newer one, reloading when there is. A button opens a tab only when none has asked
lately. The server answers on 127.0.0.1 only, serves only the latest report and the files it links to, takes reports
only with the token in its state file, and stops once no tab has asked for IDLE_EXIT seconds.

Started as a script, it runs the server; the buttons import it and call show().
"""

import json
import os
import pathlib
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = os.path.join(tempfile.gettempdir(), "mcboard_report_server.json")
POLL_MS = 1000       # how often the tab asks
WATCHED = 3.0        # seconds since the last question for a tab to count as open
IDLE_EXIT = 60.0     # seconds with no tab asking before the server stops
START_WAIT = 3.0     # seconds to wait for a new server to write its state file

RELOAD = """<script>
(function () {
  var seen = %d;
  function ask() {
    fetch("/version", {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (v) {
      if (v.version !== seen) { location.reload(); }
    }).catch(function () {}).finally(function () { setTimeout(ask, %d); });
  }
  setTimeout(ask, %d);
})();
</script>"""


class Report:
    """The latest report: its page, the files it links to, and how often it was replaced."""

    def __init__(self):
        self.lock = threading.Lock()
        self.version = 0
        self.page = "<!doctype html><title>MCboard</title><p>No report yet.</p>"
        self.files = []
        self.last_asked = 0.0
        self.started = time.monotonic()


def serve():
    """Run the server until no tab has asked for a while."""
    report = Report()
    token = secrets.token_hex(16)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, kind):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            with report.lock:
                if path == "/":
                    page = report.page.replace("</body>", RELOAD % (report.version, POLL_MS, POLL_MS) + "</body>", 1)
                    self.send(200, page, "text/html; charset=utf-8")
                elif path == "/version":
                    report.last_asked = time.monotonic()
                    self.send(200, json.dumps({"version": report.version}), "application/json")
                elif path.startswith("/file/") and path[6:].isdigit() and int(path[6:]) < len(report.files):
                    name = report.files[int(path[6:])]
                    try:
                        data = pathlib.Path(name).read_bytes()
                    except OSError:
                        self.send(404, "That file is gone.", "text/plain; charset=utf-8")
                        return
                    kind = "application/pdf" if name.lower().endswith(".pdf") else "application/octet-stream"
                    self.send(200, data, kind)
                else:
                    self.send(404, "Not here.", "text/plain; charset=utf-8")

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                asked = json.loads(self.rfile.read(length))
            except ValueError:
                asked = {}
            if self.path != "/show" or asked.get("token") != token:
                self.send(403, "No.", "text/plain; charset=utf-8")
                return
            files = [str(f) for f in asked.get("files", [])]
            page = str(asked.get("page", ""))
            for i, name in enumerate(files):
                page = page.replace(f'href="{pathlib.Path(name).absolute().as_uri()}"', f'href="/file/{i}"')
            with report.lock:
                watched = time.monotonic() - report.last_asked <= WATCHED
                report.page, report.files = page, files
                report.version += 1
            self.send(200, json.dumps({"watched": watched}), "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state = {"port": server.server_address[1], "token": token, "pid": os.getpid()}
    with open(STATE + ".new", "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(STATE + ".new", STATE)

    def stop_when_idle():
        while True:
            time.sleep(5)
            with report.lock:
                idle = time.monotonic() - max(report.last_asked, report.started)
            if idle > IDLE_EXIT:
                server.shutdown()
                return

    threading.Thread(target=stop_when_idle, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        try:
            with open(STATE, encoding="utf-8") as fh:
                if json.load(fh).get("pid") == os.getpid():
                    os.remove(STATE)
        except (OSError, ValueError):
            pass


def read_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def hand_over(state, page, files):
    """Give the running server the report: whether a tab is watching, or None if no server answers."""
    body = json.dumps({"token": state["token"], "page": page, "files": files}).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{state['port']}/show", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=2) as answer:
            return bool(json.loads(answer.read()).get("watched"))
    except (OSError, ValueError):
        return None


def start():
    """Start a server in a process of its own, which outlives the button, and return its state, or None."""
    old = read_state()
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
               "close_fds": True}
    if sys.platform.startswith("win"):
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    try:
        subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__)], **options)
    except OSError:
        return None
    give_up = time.monotonic() + START_WAIT
    while time.monotonic() < give_up:
        state = read_state()
        if state and state != old:
            return state
        time.sleep(0.05)
    return None


def show(page, files, open_url):
    """Show a report page in the shared tab. open_url(url) opens a new tab when none is watching. Return False if no
    server could be had, so that the caller opens the page's file instead."""
    state = read_state()
    watched = hand_over(state, page, files) if state else None
    if watched is None:
        state = start()
        watched = hand_over(state, page, files) if state else None
        if watched is None:
            return False
    if not watched:
        open_url(f"http://127.0.0.1:{state['port']}/")
    return True


if __name__ == "__main__":
    serve()
