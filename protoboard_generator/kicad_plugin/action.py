# SPDX-License-Identifier: GPL-3.0-or-later
"""The button. It only reads the board, runs the planner and draws marks on a layer of their own,
MCboard.Plan (User.5). It never regenerates anything, and leaves copper, nets and footprints alone.

It is kept thin on purpose: this is the only file that uses KiCad's own Python interface (SWIG), which
KiCad has deprecated. The planner and PDF work from a file, so they would carry over to another interface.
"""

import os
import tempfile
import traceback

import pcbnew

from .protoboard.constants import PLAN_MARKS_LAYER, PLAN_RING_LINE, PLAN_RING_RADIUS, PLAN_WIRE_LINE
from .protoboard.kicad_file import NotAPlacedBoard, read_board
from .protoboard.plan_pdf import write_plan_pdf
from .protoboard.planner import report, solve

GROUP_NAME = "MCboard plan"
OLD_GROUP_NAME = "protoboard plan"  # on boards planned before the button was renamed
DIALOG_SIZE = (720, 560)
report_window = None  # kept between runs of the button


def read_current(board):
    """The PlacedBoard of what the editor has now, saved or not, through a temporary copy. aSkipSettings
    stops SaveBoard from switching the open project to the temporary name."""
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "plan.kicad_pcb")
        if not pcbnew.SaveBoard(path, board, True):
            raise NotAPlacedBoard("KiCad could not write a temporary copy of the board")
        return read_board(path)


def marks_layer(board):
    """The layer for the marks, enabled and named if the board does not have it yet. Return (whether it was
    already the marks' layer, so that whatever is on it is an earlier run's; whether it had to be enabled). A User.5
    named otherwise is someone else's, so it is left alone."""
    layer = pcbnew.User_5
    name = board.GetLayerName(layer)
    if board.IsLayerEnabled(layer) and name not in PLAN_MARKS_LAYER:
        raise NotAPlacedBoard(f"the plan draws its marks on {PLAN_MARKS_LAYER[0]}, which this board uses as "
                              f"{name}. Rename that layer to {PLAN_MARKS_LAYER[1]} in Board Setup > Board Layers "
                              "if its contents can go, or move them to another layer")
    ours = board.IsLayerEnabled(layer) and name == PLAN_MARKS_LAYER[1]
    added = not board.IsLayerEnabled(layer)
    if added:
        layers = board.GetEnabledLayers()
        layers.AddLayer(layer)
        board.SetEnabledLayers(layers)
    board.SetLayerName(layer, PLAN_MARKS_LAYER[1])
    # Visibility is left to the Layers panel: setting it here would put the view and the panel out of step, and
    # the next click on the panel would hide the layer.
    return ours, added


def remove_marks(board, ours):
    """Delete the marks left by earlier runs: the items of their group, wherever they are (before the marks had a
    layer of their own they were on User.Comments), and, if the marks' layer was already there, everything on
    it, so that marks that lost their group go too."""
    for group in list(board.Groups()):
        if group.GetName() in (GROUP_NAME, OLD_GROUP_NAME):
            for item in list(group.GetItems()):
                board.Remove(item)
            board.Remove(group)
    if ours:
        for item in list(board.GetDrawings()):
            if item.GetLayer() == pcbnew.User_5:
                group = item.GetParentGroup()
                if group:
                    group.RemoveItem(item)
                board.Remove(item)


def shape(board, group, kind, start, end, width):
    """Add one line or circle on the marks' layer, as a member of the marks' group."""
    item = pcbnew.PCB_SHAPE(board, kind)
    item.SetLayer(pcbnew.User_5)
    item.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(start[0]), pcbnew.FromMM(start[1])))
    item.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(end[0]), pcbnew.FromMM(end[1])))
    item.SetWidth(pcbnew.FromMM(width))
    board.Add(item)
    group.AddItem(item)


def add_marks(board, plan):
    """A ring on the marks' layer around each chevron to close, and the path of each wire, replacing
    those of earlier runs. They are one group, so they can be selected and deleted together. Return whether the
    layer had to be added."""
    ours, added = marks_layer(board)
    remove_marks(board, ours)
    group = pcbnew.PCB_GROUP(board)
    group.SetName(GROUP_NAME)
    board.Add(group)
    for _, chevron in plan.chevrons:
        shape(board, group, pcbnew.SHAPE_T_CIRCLE, (chevron.x, chevron.y),
              (chevron.x + PLAN_RING_RADIUS, chevron.y), PLAN_RING_LINE)
    for wire in plan.wires:
        for start, end in zip(wire.drawn, wire.drawn[1:]):
            shape(board, group, pcbnew.SHAPE_T_SEGMENT, start, end, PLAN_WIRE_LINE)
    return added


def pdf_path_for(board):
    """Next to the board, or in the temporary folder if it has never been saved."""
    name = board.GetFileName()
    if not name:
        return os.path.join(tempfile.gettempdir(), "mcboard_plan.pdf")
    return os.path.splitext(name)[0] + "_plan.pdf"


def run_plan(board):
    """All the work except the window: (lines of the report, path of the PDF)."""
    plan = solve(read_current(board))
    added = add_marks(board, plan)
    path = pdf_path_for(board)
    written, scale = write_plan_pdf(plan, path)
    # A PDF viewer that locks the file sends the PDF to <name>-2.pdf and so on: say so in the report.
    locked = [f"{path} is open in another program, so the PDF went to {written}."] if str(written) != path else []
    path = str(written)
    where = f"Drawing: {path}" if scale else f"The board does not fit on A4. Report: {path}"
    lines = report(plan) + [""] + locked + [where, f"Marks are on the {PLAN_MARKS_LAYER[1]} layer "
                                                   f"({PLAN_MARKS_LAYER[0]}). A placed board is a working file: "
                                                   "order from the generator's zip."]
    if added:  # a plugin cannot make KiCad rebuild its Layers panel
        lines.append(f"This run added the {PLAN_MARKS_LAYER[1]} layer. KiCad lists it in the Layers panel once the "
                     "board is saved and opened again.")
    elif not board.IsLayerVisible(pcbnew.User_5):
        lines.append(f"The {PLAN_MARKS_LAYER[1]} layer is hidden: turn it on in the Layers panel to see the marks.")
    return lines, path


def make_window():
    """Make the report window, which belongs to the editor when it can be found, so as to stay in front of it.
    Closing it only hides it, ready for the next run."""
    import wx
    parent = wx.FindWindowByName("PcbFrame")
    dialog = wx.Dialog(parent, size=DIALOG_SIZE, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
    box = wx.BoxSizer(wx.VERTICAL)
    dialog.field = wx.TextCtrl(dialog, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
    dialog.field.SetFont(wx.Font(10, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
    box.Add(dialog.field, 1, wx.EXPAND | wx.ALL, 8)
    box.Add(dialog.CreateButtonSizer(wx.OK), 0, wx.EXPAND | wx.BOTTOM, 8)
    dialog.SetSizer(box)
    dialog.Bind(wx.EVT_BUTTON, lambda event: dialog.Hide(), id=wx.ID_OK)
    dialog.Bind(wx.EVT_CLOSE, lambda event: dialog.Hide())
    return dialog


def show(title, text):
    """Show text in a resizable read-only window that leaves the editor usable, with a fixed-width font so the
    report lines up. There is one such window: pressing the button again updates it where it is."""
    global report_window
    if not report_window:  # also true if the editor was closed, which destroys it
        report_window = make_window()
    report_window.SetTitle(title)
    report_window.field.ChangeValue(text)
    report_window.Show()
    report_window.Raise()


class PlanAction(pcbnew.ActionPlugin):
    """The toolbar button: plans the board open in the editor and shows the result."""
    def defaults(self):
        """Set the name, category and description KiCad shows for the button."""
        self.name = "MCboard plan"
        self.category = "MCboard"
        self.description = "Work out which chevrons to close and which wires to add for the placed parts"
        self.show_toolbar_button = True

    def Run(self):
        """Plan the board and show the report. Errors go to a window, as KiCad would hide them."""
        board = pcbnew.GetBoard()
        try:
            lines, _ = run_plan(board)
        except NotAPlacedBoard as error:
            show("MCboard plan", f"This board cannot be used: {error}")
            return
        except (Exception, SystemExit):  # KiCad would show nothing; font.py can exit
            show("MCboard plan: error", traceback.format_exc())
            return
        pcbnew.Refresh()
        show("MCboard plan", "\n".join(lines))
