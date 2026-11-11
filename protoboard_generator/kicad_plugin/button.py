# SPDX-License-Identifier: GPL-3.0-or-later
"""What the four buttons share: reaching the board open in the editor, reading it, writing the PDF, and showing the
report page, or why the button did nothing.

KiCad starts each button's script in a process of its own, and the script talks to the open PCB editor through
KiCad's IPC API (the kicad-python package, which KiCad installs from requirements.txt). The buttons are kept thin on
purpose: the files in this folder are the only ones that use the API, and the planner, apply and the PDF work from a
file. Importing this module puts the plugin's own packages first on the path, so each button imports it before
anything else.

What the API cannot do in KiCad 9, and so the buttons do not do either: add or rename a layer (the board must
already have MCboard.Plan and, for apply, the inner layers, as working copies do), and make groups.
"""

import os
import re
import subprocess
import sys
import tempfile
import traceback


def own_packages_first():
    """Put the plugin's environment ahead of anything KiCad adds to the path. On Linux KiCad can pass on the system's
    dist-packages, whose older protobuf would then hide the one kicad-python needs."""
    prefix = os.path.realpath(sys.prefix)
    own = [p for p in sys.path if os.path.realpath(p or ".").startswith(prefix + os.sep) and "packages" in p]
    sys.path[:] = [sys.path[0]] + own + [p for p in sys.path[1:] if p not in own]


own_packages_first()

from protoboard.constants import PLAN_MARKS_LAYER  # noqa: E402
from protoboard.kicad_file import NotAPlacedBoard, read_board  # noqa: E402
from protoboard.plan_pdf import write_plan_pdf  # noqa: E402

TIMEOUT_MS = 30000  # sending a large board as text takes a while
BUSY_WAIT = 2.0  # seconds to keep asking while KiCad says it is busy
BUSY_POLL = 0.2


class KiCadBusy(Exception):
    """KiCad kept turning the requests away as busy."""

    def __str__(self):
        return ("KiCad is busy: a tool other than selection is active, such as drawing a line, routing or moving. "
                "Press Esc until the selection arrow is back, then press the button again. Nothing was changed.")


def connect():
    """The board open in the editor. KiCad 10 turns API requests that read or change items away as busy, doing
    nothing with them, while any tool but selection is active (PCB_EDIT_FRAME::interactiveOperationInProgress), and
    for a moment during a save or a zone fill. Every request is sent again for BUSY_WAIT seconds, then KiCadBusy is
    raised. One such request is made here, so that a run stops before doing anything."""
    import time

    from kipy import KiCad
    from kipy.client import KiCadClient
    from kipy.errors import ApiError
    from kipy.proto.common.types import KiCadObjectType
    from kipy.proto.common import ApiStatusCode

    if not getattr(KiCadClient.send, "retries_busy", False):
        send = KiCadClient.send

        def send_again_while_busy(self, command, response_type):
            give_up = time.monotonic() + BUSY_WAIT
            while True:
                try:
                    return send(self, command, response_type)
                except ApiError as error:
                    if error.code != ApiStatusCode.AS_BUSY:
                        raise
                    if time.monotonic() > give_up:
                        raise KiCadBusy() from None
                time.sleep(BUSY_POLL)

        send_again_while_busy.retries_busy = True
        KiCadClient.send = send_again_while_busy
    board = KiCad(timeout_ms=TIMEOUT_MS).get_board()
    board.get_items(types=[KiCadObjectType.KOT_PCB_ARC])  # few or none, but turned away like the rest when busy
    return board


def read_current(board, locked=False):
    """(the PlacedBoard of what the editor has now, saved or not; the board's text), through a temporary copy.
    locked is read_board()'s."""
    text = board.get_as_string()
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "plan.kicad_pcb")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return read_board(path, locked), text


def check_marks_layer(text):
    """Make sure User.5 is the marks' layer, so that whatever is on it is an earlier run's and can go. The layer
    table of the board file lists only enabled layers, as (<number> "User.5" user ["<name>"])."""
    found = re.search(r'\(\s*\d+\s+"%s"\s+\w+(?:\s+"([^"]*)")?\s*\)' % re.escape(PLAN_MARKS_LAYER[0]), text)
    if found and found.group(1) == PLAN_MARKS_LAYER[1]:
        return
    how = (f"In Board Setup > Board Layers, enable {PLAN_MARKS_LAYER[0]} and name it {PLAN_MARKS_LAYER[1]}. "
           "Working copies made by the generator have it already.")
    if found:
        raise NotAPlacedBoard(f"the plan draws its marks on {PLAN_MARKS_LAYER[0]}, which this board uses as "
                              f"{found.group(1) or PLAN_MARKS_LAYER[0]}. Rename that layer to {PLAN_MARKS_LAYER[1]} "
                              "in Board Setup > Board Layers if its contents can go, or move them to another layer")
    raise NotAPlacedBoard(f"it has no {PLAN_MARKS_LAYER[1]} layer for the marks. {how}")


def board_path(board):
    """The board's file, or None if it has never been saved."""
    document = board.document
    name = document.board_filename
    if not name:
        return None
    return name if os.path.isabs(name) else os.path.join(document.project.path, name)


def output_path(board, suffix):
    """Next to the board, or in the temporary folder if it has never been saved."""
    path = board_path(board)
    if not path:
        return os.path.join(tempfile.gettempdir(), "mcboard" + suffix)
    return os.path.splitext(path)[0] + suffix


def write_pdf(board, plan):
    """Write <board>_plan.pdf: (lines that say where it went, its path). The apply button writes it too."""
    path = output_path(board, "_plan.pdf")
    written, scale = write_plan_pdf(plan, path)
    # A PDF viewer that locks the file sends the PDF to <name>-2.pdf and so on: say so in the report.
    locked = [f"{path} is open in another program, so the PDF went to {written}."] if str(written) != path else []
    path = str(written)
    where = f"Drawing: {path}" if scale else f"The board does not fit on A4. Report: {path}"
    return locked + [where], path

REPORT_STYLE = """
:root { color-scheme: light dark; --bg: #fafafa; --fg: #1e1e1e; --box: #ffffff; --line: #d0d0d0; --link: #0b5cad; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #1e1e1e; --fg: #e4e4e4; --box: #262626; --line: #444444; --link: #6cb4ff; }
}
body { margin: 0; padding: 16px 24px; background: var(--bg); color: var(--fg); font: 14px system-ui, sans-serif; }
h1 { font-size: 16px; font-weight: 600; margin: 0 0 4px; }
p.when { margin: 0 0 12px; opacity: 0.7; }
pre { margin: 0; padding: 12px 16px; background: var(--box); border: 1px solid var(--line); border-radius: 6px;
      font: 13px ui-monospace, "DejaVu Sans Mono", Consolas, monospace;
      white-space: pre-wrap; overflow-wrap: anywhere; }
a { color: var(--link); }
"""


def report_html(title, text, links=()):
    """The report as a page: the text as it is, so that its columns line up, with each path in links made a link."""
    import html
    import pathlib
    import time

    body = html.escape(text)
    for path in links:
        if path:
            body = body.replace(html.escape(path), f'<a href="{pathlib.Path(path).absolute().as_uri()}">'
                                                   f'{html.escape(path)}</a>')
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f"<style>{REPORT_STYLE}</style></head>\n<body><h1>{html.escape(title)}</h1>"
            f'<p class="when">{time.strftime("%Y-%m-%d %H:%M:%S")}</p><pre>{body}</pre></body></html>\n')


def open_file(path):
    """Open a file with the system's program for it."""
    if sys.platform.startswith("win"):
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def show(title, text, board=None, suffix="_plan.html", links=()):
    """Write the report next to the board, named with suffix, as a page, and show it in the browser's one MCboard tab
    (report_server.py), or open the file if that cannot be had."""
    print(text)
    path = output_path(board, suffix) if board else os.path.join(tempfile.gettempdir(), "mcboard" + suffix)
    page = report_html(title, text, links)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    try:
        import report_server
        if report_server.show(page, [link for link in links if link], open_file):
            return
    except Exception:
        pass
    open_file(path)


def fail(title, text, board, suffix):
    """Say why the button did nothing, on the report page. Only if the page cannot be written does it go to stderr with
    exit code 1, which KiCad 10 turns into a warning in the editor's status bar; a warning there besides the page would
    stay until the next action and say nothing more."""
    try:
        show(title + ": error", text, board, suffix)
    except OSError:
        sys.stderr.write(f"{text}\n")
        sys.exit(1)
    sys.exit(0)


def run_button(title, work, refused, why, suffix):
    """Connect, do the work, and show its report, or say why it could not be done. work(board) gives (lines of the
    report, paths to link in it); refused is the exceptions that mean the board or the plan cannot be used, why the
    words that come before their message."""
    board = None
    try:
        board = connect()
        lines, links = work(board)
    except refused as error:
        fail(title, f"{why}: {error}", board, suffix)
        return
    except KiCadBusy as error:
        fail(title, str(error), board, suffix)
        return
    except (Exception, SystemExit):  # font.py can exit
        fail(title, traceback.format_exc(), board, suffix)
        return
    show(title, "\n".join(lines), board, suffix, links)
