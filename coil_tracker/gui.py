"""Coil Tracker desktop app (PySide6)."""
import json
import os
import sys
import traceback

os.environ.setdefault("QT_API", "pyside6")
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QProgressDialog, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from coil_tracker import __version__, checks, pipeline, plots, style
from coil_tracker.library import Library
from coil_tracker.pdf_report import export_pdf

APP = "Coil Tracker"

HELP_TEXT = f"""<h3>{APP} {__version__}</h3>
<p><b>What it does.</b> For each scan it rates every element of the coil and lists the ones most worth
checking. Four checks run every time:</p>
<ol>
<li><b>This scan on its own</b>: dead, disconnected or badly detuned elements (compares each element with
its neighbours and mirror element, plus the noise-spectrum tuning indicator).</li>
<li><b>Missing elements</b>: elements this coil should have that are not in the scan.</li>
<li><b>Change since baseline</b>: slight detuning, mismatch or partial faults, by comparing with this coil's
healthy baseline. Works from one baseline scan.</li>
<li><b>Drift</b>: slow steady changes over the scans since the baseline (needs 3 or more scans).</li>
</ol>
<p><b>Levels.</b> <span style="color:{style.RED_DARK}"><b>Fault</b></span>: clearly abnormal in this scan, or missing.
<span style="color:#7A4A00"><b>Watch</b></span>: changed or drifting compared with this coil's baseline.
<b>Routine check</b>: nothing clearly wrong; these are simply the top of the list.
The top 3 are always marked <i>physically verify</i>.</p>
<p><b>Scan protocol (important).</b> Use the <b>same phantom</b>, placed the same way, every time.
Acquire the <b>SNR-map scan</b> (required: it contains the noise measurement) and preferably an
<b>MPRAGE</b> too (better maps). Use the same scans each time.</p>
<p><b>First scan of a coil.</b> There is no baseline yet, so only the single-scan checks run. Verify the
listed elements, then save the scan as the <b>healthy baseline</b>. After a repair or retune, save the next
good scan as a <b>new baseline (after repair)</b>.</p>
<p><b>Known quirks.</b> If an element is always listed but is fine on the bench, click <i>Mark as checked</i>.
It then stops counting as a fault from a single scan (changes over time still count, and a dead element is
always flagged). Checked elements are listed in every report and on the coil page, where you can unmark them.</p>
<p><b>Storage.</b> Each saved scan is a small summary (~130 KB): per-element measurements and the noise
covariance, no images and no patient details. Raw files can be archived afterwards.</p>
<p><b>Limits.</b> Validated on two fault days (10-30 and 11-13). Long-term behaviour (scanner updates,
phantom changes, months of drift) is not tested yet. A coil with no history only gets the single-scan
checks, which miss subtle faults.</p>"""


# ── Settings ──────────────────────────────────────────────────────────────────

def _settings_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "CoilTracker")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "settings.json")


def load_settings() -> dict:
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(s: dict):
    with open(_settings_path(), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)


def default_library() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents", "CoilTracker Library")


# ── Background analysis ───────────────────────────────────────────────────────

class AnalyseWorker(QThread):
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, snr, mprage, notes, operator):
        super().__init__()
        self.args = (snr, mprage, notes, operator)

    def run(self):
        try:
            snr, mprage, notes, operator = self.args
            fp = pipeline.analyse_files(snr, mprage, notes, operator, progress=self.progress.emit)
            self.done.emit(fp)
        except MemoryError:
            self.failed.emit("Not enough memory to read these files. Close other programs and try again.")
        except Exception as e:  # shown to the user, details in the expandable box
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


# ── Small building blocks ─────────────────────────────────────────────────────

def label(text="", kind=None, wrap=True):
    lab = QLabel(text)
    if kind:
        lab.setObjectName(kind)
    lab.setWordWrap(wrap)
    return lab


def card():
    f = QFrame(); f.setObjectName("card")
    lay = QVBoxLayout(f); lay.setContentsMargins(16, 14, 16, 14); lay.setSpacing(8)
    return f, lay


def button(text, kind=None, fn=None, tip=None):
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if fn:
        b.clicked.connect(fn)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def chip(text, level):
    fg, bg = style.LEVEL[level]
    lab = QLabel(text)
    lab.setStyleSheet(f"color:{fg}; background:{bg}; border-radius:9px; padding:2px 9px; font-weight:600;"
                      f"font-size:9pt;")
    lab.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    w = QWidget(); lay = QHBoxLayout(w); lay.setContentsMargins(6, 0, 6, 0)
    lay.addWidget(lab, 0, Qt.AlignVCenter); lay.addStretch(1)
    return w


def clear_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w:
            w.hide(); w.setParent(None); w.deleteLater()


def ro_item(text, color=None, bold=False):
    it = QTableWidgetItem(str(text))
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if color:
        it.setForeground(QColor(color))
    if bold:
        f = it.font(); f.setBold(True); it.setFont(f)
    return it


def table(headers, stretch_col):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    t.setFrameShape(QFrame.NoFrame)
    t.horizontalHeader().setHighlightSections(False)
    t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    for c in range(len(headers)):
        t.horizontalHeader().setSectionResizeMode(c, QHeaderView.Stretch if c == stretch_col
                                                  else QHeaderView.ResizeToContents)
    t.verticalHeader().setDefaultSectionSize(36)
    return t


def fit_height(t):
    t.resizeRowsToContents()
    t.setFixedHeight(t.horizontalHeader().height() + sum(t.rowHeight(i) for i in range(t.rowCount())) + 6)


def canvas(h):
    fig = Figure(figsize=(8, h / 100), facecolor="white")
    c = FigureCanvasQTAgg(fig)
    c.setMinimumHeight(h)
    return c


class FileRow(QWidget):
    def __init__(self, placeholder, start_dir_fn):
        super().__init__()
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(); self.edit.setPlaceholderText(placeholder)
        lay.addWidget(self.edit, 1); lay.addWidget(button("Browse…", fn=self.browse))
        self.start_dir_fn = start_dir_fn

    def browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Choose a raw scan file", self.start_dir_fn(),
                                           "Siemens raw data (*.dat);;All files (*)")
        if p:
            self.edit.setText(p)

    def path(self):
        return self.edit.text().strip().strip('"')


# ── Dialogs ───────────────────────────────────────────────────────────────────

class AnalyseDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("Analyse a new scan")
        self.setMinimumWidth(660)
        self.settings = settings
        lay = QVBoxLayout(self); lay.setSpacing(12); lay.setContentsMargins(20, 18, 20, 18)
        lay.addWidget(label("Analyse a new scan", "h2"))
        lay.addWidget(label("Pick the raw files (.dat) of one scan session. The SNR map is required (it contains "
                            "the noise measurement); the MPRAGE is optional but recommended.", "muted"))
        lay.addWidget(button("Pick a session folder and fill in automatically…", "primary", self.pick_folder), 0, Qt.AlignLeft)
        form = QFormLayout(); form.setSpacing(10)
        start = lambda: self.settings.get("last_dir", os.path.expanduser("~"))
        self.snr = FileRow("SNR map .dat (required)", start)
        self.mpr = FileRow("MPRAGE .dat (optional)", start)
        self.operator = QLineEdit(self.settings.get("operator", ""))
        self.notes = QLineEdit(); self.notes.setPlaceholderText("e.g. routine QA, after moving the scanner, new phantom…")
        form.addRow("SNR map", self.snr); form.addRow("MPRAGE", self.mpr)
        form.addRow("Your name", self.operator); form.addRow("Notes", self.notes)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Analyse")
        bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        bb.accepted.connect(self.check); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Session folder", self.settings.get("last_dir", ""))
        if not d:
            return
        g = pipeline.guess_pair(d)
        self.settings["last_dir"] = d
        for row, opts, name in ((self.snr, g["snr"], "SNR map"), (self.mpr, g["mprage"], "MPRAGE")):
            if len(opts) == 1:
                row.edit.setText(opts[0])
            elif len(opts) > 1:
                names = [os.path.basename(o) for o in opts]
                choice, ok = QInputDialog.getItem(self, f"Which {name}?", f"Several {name} files found:", names, 0, False)
                if ok:
                    row.edit.setText(opts[names.index(choice)])
        if not g["snr"]:
            QMessageBox.information(self, APP, "No SNR-map file found in that folder (file name containing 'SNR').")

    def check(self):
        if not os.path.isfile(self.snr.path()):
            QMessageBox.warning(self, APP, "Please choose the SNR-map .dat file (required).")
            return
        if self.mpr.path() and not os.path.isfile(self.mpr.path()):
            QMessageBox.warning(self, APP, "The MPRAGE file does not exist. Clear the field to analyse without it.")
            return
        self.settings["operator"] = self.operator.text().strip()
        self.settings["last_dir"] = os.path.dirname(self.snr.path())
        self.accept()


class CoilChoiceDialog(QDialog):
    """Which coil does this scan belong to? Pre-selects the best match."""

    def __init__(self, parent, lib: Library, fp: dict):
        super().__init__(parent)
        self.setWindowTitle("Which coil is this?")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self); lay.setSpacing(12); lay.setContentsMargins(20, 18, 20, 18)
        lay.addWidget(label("Which coil is this?", "h2"))
        matches = lib.match_coil(fp)
        types = sorted({l.split(":")[0] for l in fp["labels"]})
        lay.addWidget(label(f"This scan has {len(fp['labels'])} elements ({', '.join(types)}).", "muted"))
        self.existing = QRadioButton("An existing coil")
        self.combo = QComboBox()
        for c, share, unknown in matches:
            extra = f", {len(unknown)} new element(s)" if unknown else ""
            missing = round((1 - share) * len(c["elements"]))
            self.combo.addItem(f"{c['name']}  ({missing} missing{extra})", c["id"])
        self.new = QRadioButton("A new coil")
        self.name = QLineEdit(); self.name.setPlaceholderText("e.g. Head 128 (lab A), custom 16-ch TMS coil")
        grid = QGridLayout(); grid.setSpacing(10)
        grid.addWidget(self.existing, 0, 0); grid.addWidget(self.combo, 0, 1)
        grid.addWidget(self.new, 1, 0); grid.addWidget(self.name, 1, 1)
        lay.addLayout(grid)
        grp = QButtonGroup(self); grp.addButton(self.existing); grp.addButton(self.new)
        if matches:
            self.existing.setChecked(True)
        else:
            self.existing.setEnabled(False); self.combo.setEnabled(False); self.new.setChecked(True)
            lay.addWidget(label("No coil in the library matches these elements.", "muted"))
        self.name.textEdited.connect(lambda _: self.new.setChecked(True))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        bb.accepted.connect(self.check); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def check(self):
        if self.new.isChecked() and not self.name.text().strip():
            QMessageBox.warning(self, APP, "Please give the new coil a name.")
            return
        self.accept()

    def result_choice(self):
        if self.existing.isChecked():
            return ("existing", self.combo.currentData())
        return ("new", self.name.text().strip())


def ask_checked_note(parent, short):
    note, ok = QInputDialog.getText(parent, "Mark as checked",
                                    f"{short} will stop counting as a fault from a single scan.\n"
                                    "Changes over time still count, and a dead element is always flagged.\n\n"
                                    "Note (e.g. 'bench-tested 21 Nov, fine'):")
    return note, ok


# ── Report page ───────────────────────────────────────────────────────────────

class ReportPage(QWidget):
    saved = Signal(str, str)          # coil_id, scan_id
    back = Signal()

    def __init__(self, main):
        super().__init__()
        self.setObjectName("page")
        self.main = main
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        body = QWidget(); body.setObjectName("page"); scroll.setWidget(body)
        lay = QVBoxLayout(body); lay.setContentsMargins(24, 18, 24, 18); lay.setSpacing(14)

        head = QHBoxLayout()
        tl = QVBoxLayout(); tl.setSpacing(2)
        self.title = label("", "h1"); self.sub = label("", "muted")
        self.sub.setTextFormat(Qt.RichText)
        tl.addWidget(self.title); tl.addWidget(self.sub)
        head.addLayout(tl, 1)
        self.b_back = button("← Back", fn=lambda: self.back.emit())
        head.addWidget(self.b_back, 0, Qt.AlignTop)
        lay.addLayout(head)

        self.banner = QLabel(); self.banner.setWordWrap(True); self.banner.setTextFormat(Qt.RichText)
        lay.addWidget(self.banner)

        c, cl = card()
        cl.addWidget(label("Check these elements first", "h2"))
        cl.addWidget(label("The top 3 are always marked “physically verify”, even on a healthy coil.", "muted"))
        self.top = table(["#", "Element", "Level", "Why", ""], 3)
        self.top.setWordWrap(True)
        cl.addWidget(self.top)
        lay.addWidget(c)

        row = QHBoxLayout(); row.setSpacing(14)
        c, cl = card()
        cl.addWidget(label("Alerts", "h2"))
        self.alerts = label(""); self.alerts.setTextFormat(Qt.RichText)
        cl.addWidget(self.alerts); cl.addStretch(1)
        row.addWidget(c, 3)
        c, cl = card()
        cl.addWidget(label("Known quirks (marked as checked)", "h2"))
        self.known_box = QVBoxLayout(); self.known_box.setSpacing(6)
        cl.addLayout(self.known_box); cl.addStretch(1)
        row.addWidget(c, 2)
        lay.addLayout(row)

        c, cl = card()
        cl.addWidget(label("Where the suspects are", "h2"))
        self.canvas = canvas(340)
        cl.addWidget(self.canvas)
        lay.addWidget(c)

        c, cl = card()
        hr = QHBoxLayout()
        hr.addWidget(label("Full ranking", "h2"), 1)
        self.toggle = button("Show")
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(lambda on: (self.full.setVisible(on), self.toggle.setText("Hide" if on else "Show")))
        hr.addWidget(self.toggle)
        cl.addLayout(hr)
        self.full = table(["#", "Element", "Level", "Total", "This scan", "Vs baseline", "Drift", "Why"], 7)
        self.full.setMinimumHeight(440)
        self.full.setVisible(False)
        cl.addWidget(self.full)
        lay.addWidget(c)

        btns = QHBoxLayout(); btns.setSpacing(8)
        self.b_save = button("Save to history", "primary", lambda: self.save("normal"))
        self.b_base = button("Save as healthy baseline", fn=lambda: self.save("baseline"))
        self.b_reset = button("Save as new baseline (after repair)", fn=lambda: self.save("reset"))
        self.b_pdf = button("Export PDF…", fn=self.pdf)
        for b in (self.b_save, self.b_base, self.b_reset, self.b_pdf):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        lay.addStretch(1)
        self.state = None

    # state: {"fp", "report", "coil_id" or None, "new_coil_name", "saved_scan" or None}
    def show_report(self, state: dict):
        self.state = state
        rep, fp = state["report"], state["fp"]
        meta = fp["meta"]
        coil_name = rep.get("coil", {}).get("name") or state.get("new_coil_name") or "New coil"
        self.title.setText(f"{coil_name}")
        base = rep.get("baseline_dates") or []
        maps = "MPRAGE + SNR map" if meta.get("maps_from_3d") else "SNR map only"
        saved = "saved" if state.get("saved_scan") else "<b>not saved yet</b>"
        self.sub.setText(f"Scan of {meta.get('date', '?')} · {maps} · {saved}<br>"
                         f"Baseline: {', '.join(d[:16] for d in base) if base else 'none yet'}"
                         f" · drift: {str(rep['n_drift_scans']) + ' scans' if rep['n_drift_scans'] else 'needs 3+ scans'}"
                         + (f" · notes: {meta['notes']}" if meta.get("notes") else ""))
        bg, fg, accent = style.STATUS[rep["status"]]
        self.banner.setStyleSheet(f"background:{bg}; color:{fg}; border-left:6px solid {accent};"
                                  f"border-radius:8px; padding:14px 16px; font-size:13pt;")
        self.banner.setText(f"<b>{rep['status']}</b>&nbsp;&nbsp;{rep['summary']}")

        can_mark = bool(state.get("coil_id"))
        self.top.clearContents()
        self.top.setRowCount(0)
        self.top.setRowCount(len(rep["top"]))
        for r, e in enumerate(rep["top"]):
            verify = r < rep["verify_n"]
            self.top.setItem(r, 0, ro_item(r + 1, style.MUTED))
            self.top.setItem(r, 1, ro_item(e["short"], bold=True))
            self.top.setCellWidget(r, 2, chip(("Physically verify · " if verify else "") + e["level"], e["level"]))
            self.top.setItem(r, 3, ro_item(e["reason"]))
            b = button("Unmark" if e["checked"] else "Mark as checked", "small",
                       tip="Bench-checked and fine: stop counting it as a fault from a single scan."
                       if can_mark else "Save the scan first (the coil is new).")
            b.setEnabled(can_mark)
            b.clicked.connect(lambda _=False, lab=e["label"], on=not e["checked"]: self.mark_checked(lab, on))
            w = QWidget(); wl = QHBoxLayout(w); wl.setContentsMargins(4, 2, 8, 2); wl.addWidget(b)
            self.top.setCellWidget(r, 4, w)
        fit_height(self.top)

        self.alerts.setText("<ul style='margin-left:-20px'>" + "".join(f"<li style='margin-bottom:4px'>{a}</li>"
                                                                       for a in rep["alerts"]) + "</ul>"
                            if rep["alerts"] else "<span style='color:#6B6B70'>None</span>")

        clear_layout(self.known_box)
        if not rep.get("checked"):
            self.known_box.addWidget(label("None. Use “Mark as checked” on an element that is always listed "
                                           "but fine on the bench.", "muted"))
        for k in rep.get("checked", []):
            w = QWidget(); wl = QHBoxLayout(w); wl.setContentsMargins(0, 0, 0, 0)
            where = f"now #{k['rank']}, {k['level']}" if k["rank"] else "not in this scan"
            wl.addWidget(label(f"<b>{k['short']}</b> · {k['note']} <span style='color:#6B6B70'>({where})</span>"), 1)
            ub = button("Unmark", "small", lambda _=False, lab=k["label"]: self.mark_checked(lab, False))
            ub.setEnabled(can_mark)
            wl.addWidget(ub)
            self.known_box.addWidget(w)

        plots.layout_figure(fp, rep, self.canvas.figure)
        self.canvas.draw_idle()

        self.full.setRowCount(len(rep["ranking"]))
        for r, e in enumerate(rep["ranking"]):
            vals = [r + 1, e["short"], e["level"], f"{e['score']:.1f}", f"{e['single']:.1f}",
                    f"{e['change']:.1f}", f"{e['drift']:.1f}", e["reason"]]
            for c, v in enumerate(vals):
                self.full.setItem(r, c, ro_item(v, style.LEVEL[e["level"]][0] if c == 2 else None, bold=c == 1))

        unsaved = state.get("saved_scan") is None
        for b in (self.b_save, self.b_base):
            b.setVisible(unsaved)
        self.b_reset.setVisible(unsaved and bool(state.get("coil_id")))

    def mark_checked(self, lab, on):
        cid = self.state["coil_id"]
        short = lab.split(":")[-1]
        note = ""
        if on:
            note, ok = ask_checked_note(self, short)
            if not ok:
                return
        self.main.lib.set_checked(cid, lab, note, on)
        self.state["report"] = self.main.lib.evaluate(cid, self.state["fp"], before=self.state.get("saved_scan"))
        self.show_report(self.state)
        self.main.toast(f"{short} {'marked as checked. It is listed under Known quirks.' if on else 'unmarked.'}")

    def save(self, mode):
        lib, st = self.main.lib, self.state
        cid = st.get("coil_id")
        if mode in ("baseline", "reset"):
            worst = [e["short"] for e in st["report"]["top"] if e["level"] in ("Fault", "Watch")]
            if worst and QMessageBox.question(
                    self, APP, f"This scan lists {', '.join(worst)} as Fault/Watch. Save it as a healthy baseline "
                               "anyway? Only do this if those elements were verified.") != QMessageBox.Yes:
                return
        if cid is None:
            cid = lib.create_coil(st["new_coil_name"], st["fp"])["id"]
        report = lib.evaluate(cid, st["fp"])
        sid = lib.add_scan(cid, st["fp"], report, baseline=mode == "baseline", reset_baseline=mode == "reset")
        self.saved.emit(cid, sid)

    def pdf(self):
        st = self.state
        meta = st["fp"]["meta"]
        name = f"CoilTracker_{(st['report'].get('coil', {}).get('name') or st.get('new_coil_name') or 'coil')}_" \
               f"{meta.get('date', '')[:10]}.pdf".replace(" ", "_").replace(":", "")
        p, _ = QFileDialog.getSaveFileName(self, "Export PDF", os.path.join(os.path.expanduser("~"), "Documents", name),
                                           "PDF (*.pdf)")
        if p:
            export_pdf(st["report"], st["fp"], p)
            self.main.toast(f"PDF saved: {p}")


# ── Coil page ─────────────────────────────────────────────────────────────────

class CoilPage(QWidget):
    open_scan = Signal(str, str)
    changed = Signal()

    def __init__(self, main):
        super().__init__()
        self.setObjectName("page")
        self.main = main
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        body = QWidget(); body.setObjectName("page"); scroll.setWidget(body)
        lay = QVBoxLayout(body); lay.setContentsMargins(24, 18, 24, 18); lay.setSpacing(14)
        self.title = label("", "h1"); self.sub = label("", "muted")
        lay.addWidget(self.title); lay.addWidget(self.sub)

        c, cl = card()
        cl.addWidget(label("Saved scans", "h2"))
        self.table = table(["Date", "Status", "Top suspects", "Baseline", "Maps", "Notes"], 5)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.doubleClicked.connect(lambda _: self.open_selected())
        self.table.setMinimumHeight(290)
        cl.addWidget(self.table)
        row = QHBoxLayout(); row.setSpacing(8)
        row.addWidget(button("Open report", "primary", self.open_selected))
        row.addWidget(button("Set / unset baseline", fn=self.toggle_baseline))
        row.addWidget(button("New baseline from here (after repair)", fn=self.reset_here))
        row.addStretch(1)
        row.addWidget(button("Delete scan", fn=self.delete))
        cl.addLayout(row)
        lay.addWidget(c)

        c, cl = card()
        hr = QHBoxLayout()
        hr.addWidget(label("Element history", "h2"), 1)
        hr.addWidget(QLabel("Element"))
        self.el = QComboBox(); self.el.setMinimumWidth(190); self.el.currentIndexChanged.connect(self.draw_history)
        hr.addWidget(self.el)
        cl.addLayout(hr)
        self.canvas = canvas(280)
        cl.addWidget(self.canvas)
        lay.addWidget(c)

        c, cl = card()
        cl.addWidget(label("Known quirks (marked as checked)", "h2"))
        cl.addWidget(label("These elements no longer count as a fault from a single scan. Changes over time still "
                           "count, and a dead element is always flagged.", "muted"))
        self.checked_list = QListWidget(); self.checked_list.setMaximumHeight(130)
        cl.addWidget(self.checked_list)
        r2 = QHBoxLayout()
        r2.addWidget(button("Mark an element as checked…", fn=self.add_checked))
        r2.addWidget(button("Unmark selected", fn=self.remove_checked))
        r2.addStretch(1)
        cl.addLayout(r2)
        lay.addWidget(c)
        lay.addStretch(1)
        self.coil_id = None

    def show_coil(self, coil_id):
        self.coil_id = coil_id
        lib = self.main.lib
        c = lib.coil(coil_id)
        self.scans = lib.scans(coil_id)
        active = lib.active_baselines(coil_id)
        self.title.setText(c["name"])
        self.sub.setText(f"{len(c['elements'])} elements · {len(self.scans)} saved scans · created {c['created'][:10]}"
                         + (f" · {c['description']}" if c.get("description") else ""))
        self.table.setRowCount(len(self.scans))
        status_col = {"FAULT": style.RED_DARK, "WATCH": "#7A4A00", "OK": "#1E6B34"}
        for r, s in enumerate(reversed(self.scans)):
            base = "active" if s["id"] in active else ("superseded" if s["baseline"] else "")
            vals = [s["date"][:16], s["status"], ", ".join(s.get("top", [])[:3]), base,
                    "MPRAGE + SNR" if s.get("maps_from_3d") else "SNR only", s.get("notes", "")]
            for col, v in enumerate(vals):
                it = ro_item(v, status_col.get(v) if col == 1 else (style.RED_DARK if col == 3 and base == "active" else None),
                             bold=col == 1 or (col == 3 and base == "active"))
                it.setData(Qt.UserRole, s["id"])
                it.setToolTip(", ".join(s.get("files", [])))
                self.table.setItem(r, col, it)
        self.el.blockSignals(True)
        self.el.clear()
        self.el.addItems([l.split(":")[-1] + "   (" + l.split(":")[0] + ")" for l in c["elements"]])
        self.el_labels = list(c["elements"])
        self.el.blockSignals(False)
        self.fps = [lib.load_fp(coil_id, s["id"]) for s in self.scans]
        self.draw_history()
        self.checked_list.clear()
        for lab, note in c.get("checked", {}).items():
            it = QListWidgetItem(f"{lab.split(':')[-1]}  ·  {note}")
            it.setData(Qt.UserRole, lab)
            self.checked_list.addItem(it)
        if not c.get("checked"):
            it = QListWidgetItem("None yet")
            it.setFlags(Qt.NoItemFlags)
            self.checked_list.addItem(it)

    def draw_history(self):
        fig = self.canvas.figure
        if not getattr(self, "fps", None) or self.el.currentIndex() < 0:
            fig.clear(); self.canvas.draw_idle(); return
        lab = self.el_labels[self.el.currentIndex()]
        active = self.main.lib.active_baselines(self.coil_id)
        idx = next((i for i, s in enumerate(self.scans) if s["id"] in active), 0)
        plots.history_figure(self.fps, [s["date"] for s in self.scans], lab, idx, fig)
        self.canvas.draw_idle()

    def selected(self):
        r = self.table.currentRow()
        if r < 0:
            QMessageBox.information(self, APP, "Select a scan in the table first.")
            return None
        return self.table.item(r, 0).data(Qt.UserRole)

    def open_selected(self):
        sid = self.selected()
        if sid:
            self.open_scan.emit(self.coil_id, sid)

    def toggle_baseline(self):
        sid = self.selected()
        if sid:
            s = next(x for x in self.scans if x["id"] == sid)
            self.main.lib.set_baseline(self.coil_id, sid, not s["baseline"])
            self.show_coil(self.coil_id)

    def reset_here(self):
        sid = self.selected()
        if sid and QMessageBox.question(self, APP, "Use this scan as the new baseline? Earlier baselines stop "
                                                   "counting for new scans (use this after a repair or retune).") == QMessageBox.Yes:
            self.main.lib.reset_baseline(self.coil_id, sid)
            self.show_coil(self.coil_id)

    def delete(self):
        sid = self.selected()
        if sid and QMessageBox.question(self, APP, "Delete this saved scan from the history? This cannot be undone.") == QMessageBox.Yes:
            self.main.lib.delete_scan(self.coil_id, sid)
            self.show_coil(self.coil_id)
            self.changed.emit()

    def add_checked(self):
        names = [l.split(":")[-1] + "   (" + l.split(":")[0] + ")" for l in self.el_labels]
        choice, ok = QInputDialog.getItem(self, "Mark as checked", "Element:", names, 0, False)
        if not ok:
            return
        lab = self.el_labels[names.index(choice)]
        note, ok = ask_checked_note(self, lab.split(":")[-1])
        if ok:
            self.main.lib.set_checked(self.coil_id, lab, note, True)
            self.show_coil(self.coil_id)
            self.main.toast(f"{lab.split(':')[-1]} marked as checked.")

    def remove_checked(self):
        it = self.checked_list.currentItem()
        lab = it.data(Qt.UserRole) if it else None
        if not lab:
            QMessageBox.information(self, APP, "Select a checked element in the list first.")
            return
        self.main.lib.set_checked(self.coil_id, lab, "", False)
        self.show_coil(self.coil_id)
        self.main.toast(f"{lab.split(':')[-1]} unmarked.")


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.setWindowTitle(APP)
        self.resize(1320, 880)
        self.lib = Library(self.settings.get("library") or default_library())

        root = QWidget(); rl = QVBoxLayout(root); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(0)
        header = QFrame(); header.setObjectName("appHeader")
        hl = QHBoxLayout(header); hl.setContentsMargins(22, 12, 18, 12); hl.setSpacing(10)
        tl = QVBoxLayout(); tl.setSpacing(0)
        tl.addWidget(label(APP, "appTitle", wrap=False))
        tl.addWidget(label("MRI receive-coil QA · single-scan checks and history", "appSub", wrap=False))
        hl.addLayout(tl); hl.addStretch(1)
        hl.addWidget(button("Help", "headerGhost", self.help))
        hl.addWidget(button("Library folder…", "headerGhost", self.choose_library))
        hl.addWidget(button("＋  Analyse new scan", "headerPrimary", self.analyse))
        rl.addWidget(header)

        split = QSplitter(); split.setHandleWidth(1)
        side = QFrame(); side.setObjectName("sidebar")
        sl = QVBoxLayout(side); sl.setContentsMargins(10, 16, 10, 10); sl.setSpacing(8)
        sl.addWidget(label("COILS", "muted"))
        self.coil_list = QListWidget(); self.coil_list.itemSelectionChanged.connect(self.coil_selected)
        sl.addWidget(self.coil_list, 1)
        br = QHBoxLayout()
        br.addWidget(button("Rename", "small", self.rename_coil)); br.addWidget(button("Delete", "small", self.delete_coil))
        sl.addLayout(br)
        self.lib_label = label("", "muted")
        sl.addWidget(self.lib_label)
        split.addWidget(side)

        self.stack = QStackedWidget()
        self.welcome = QWidget(); self.welcome.setObjectName("page")
        wl = QVBoxLayout(self.welcome); wl.setAlignment(Qt.AlignCenter)
        c, cl = card(); c.setMaximumWidth(560)
        cl.setContentsMargins(28, 24, 28, 24)
        cl.addWidget(label("Welcome to Coil Tracker", "h1"))
        cl.addWidget(label("Analyse a QA scan to rate every element of a coil, or pick a coil on the left to see "
                           "its history.", "muted"))
        cl.addSpacing(6)
        cl.addWidget(label("1.  Scan the <b>same phantom</b> each time (SNR map, plus MPRAGE if possible).<br>"
                           "2.  Click <b>Analyse new scan</b> and pick the session folder.<br>"
                           "3.  Check the listed elements, then save the scan to the coil's history.<br>"
                           "4.  Save the first good scan of a coil as its <b>healthy baseline</b>."))
        cl.addSpacing(6)
        bl = QHBoxLayout()
        bl.addWidget(button("＋  Analyse new scan", "primary", self.analyse)); bl.addWidget(button("How it works", fn=self.help))
        bl.addStretch(1); cl.addLayout(bl)
        wl.addWidget(c)
        self.coil_page = CoilPage(self)
        self.report_page = ReportPage(self)
        for w in (self.welcome, self.coil_page, self.report_page):
            self.stack.addWidget(w)
        split.addWidget(self.stack)
        split.setSizes([250, 1070])
        rl.addWidget(split, 1)
        self.setCentralWidget(root)
        self.statusBar().setStyleSheet(f"color:{style.MUTED};")

        self.coil_page.open_scan.connect(self.open_saved)
        self.coil_page.changed.connect(self.refresh_coils)
        self.report_page.saved.connect(self.after_save)
        self.report_page.back.connect(self.go_back)
        self.refresh_coils()

    def toast(self, msg):
        self.statusBar().showMessage(msg, 6000)

    # coils
    def refresh_coils(self, select=None):
        self.lib_label.setText(f"Library: {self.lib.root}")
        self.coil_list.blockSignals(True)
        self.coil_list.clear()
        for c in self.lib.coils():
            n = len(self.lib.scans(c["id"]))
            it = QListWidgetItem(f"{c['name']}\n{len(c['elements'])} elements · {n} scans")
            it.setData(Qt.UserRole, c["id"])
            self.coil_list.addItem(it)
            if c["id"] == select:
                it.setSelected(True)
        self.coil_list.blockSignals(False)

    def current_coil(self):
        items = self.coil_list.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def coil_selected(self):
        cid = self.current_coil()
        if cid:
            self.coil_page.show_coil(cid)
            self.stack.setCurrentWidget(self.coil_page)

    def rename_coil(self):
        cid = self.current_coil()
        if not cid:
            return
        name, ok = QInputDialog.getText(self, "Rename coil", "New name:", text=self.lib.coil(cid)["name"])
        if ok and name.strip():
            self.lib.rename_coil(cid, name.strip())
            self.refresh_coils(cid); self.coil_selected()

    def delete_coil(self):
        cid = self.current_coil()
        if cid and QMessageBox.question(self, APP, f"Delete coil '{self.lib.coil(cid)['name']}' and ALL its saved "
                                                   "scans? This cannot be undone.") == QMessageBox.Yes:
            self.lib.delete_coil(cid)
            self.refresh_coils(); self.stack.setCurrentWidget(self.welcome)

    def choose_library(self):
        d = QFileDialog.getExistingDirectory(self, "Choose or create the library folder (can be on a shared drive)",
                                             self.lib.root)
        if d:
            self.lib = Library(d)
            self.settings["library"] = d
            save_settings(self.settings)
            self.refresh_coils(); self.stack.setCurrentWidget(self.welcome)
            self.toast(f"Library: {d}")

    def help(self):
        box = QMessageBox(self); box.setWindowTitle(f"{APP} help"); box.setTextFormat(Qt.RichText)
        box.setText(HELP_TEXT); box.exec()

    # analysis
    def analyse(self):
        dlg = AnalyseDialog(self, self.settings)
        if dlg.exec() != QDialog.Accepted:
            return
        save_settings(self.settings)
        self.prog = QProgressDialog("Starting…", None, 0, 0, self)
        self.prog.setWindowTitle("Analysing"); self.prog.setWindowModality(Qt.WindowModal)
        self.prog.setMinimumWidth(500); self.prog.setMinimumDuration(0); self.prog.show()
        self.worker = AnalyseWorker(dlg.snr.path(), dlg.mpr.path() or None, dlg.notes.text().strip(),
                                    dlg.operator.text().strip())
        self.worker.progress.connect(self.prog.setLabelText)
        self.worker.done.connect(self.analysed)
        self.worker.failed.connect(self.analysis_failed)
        self.worker.start()

    def analysis_failed(self, msg):
        self.prog.close()
        box = QMessageBox(self); box.setIcon(QMessageBox.Warning); box.setWindowTitle(APP)
        box.setText("The analysis failed:\n\n" + msg.split("\n\n")[0])
        box.setDetailedText(msg)
        box.exec()

    def analysed(self, fp):
        self.prog.close()
        dlg = CoilChoiceDialog(self, self.lib, fp)
        if dlg.exec() != QDialog.Accepted:
            return
        kind, value = dlg.result_choice()
        if kind == "existing":
            state = {"fp": fp, "coil_id": value, "report": self.lib.evaluate(value, fp), "saved_scan": None}
        else:
            state = {"fp": fp, "coil_id": None, "new_coil_name": value, "report": checks.evaluate(fp), "saved_scan": None}
        self.report_page.show_report(state)
        self.stack.setCurrentWidget(self.report_page)

    def after_save(self, cid, sid):
        self.refresh_coils(cid)
        self.open_saved(cid, sid)
        self.toast("Saved to the coil's history.")

    def open_saved(self, cid, sid):
        rec = next(s for s in self.lib.scans(cid) if s["id"] == sid)
        fp = self.lib.load_fp(cid, sid)
        state = {"fp": fp, "coil_id": cid, "report": self.lib.evaluate(cid, fp, before=rec), "saved_scan": rec}
        self.report_page.show_report(state)
        self.stack.setCurrentWidget(self.report_page)

    def go_back(self):
        if self.current_coil():
            self.coil_selected()
        else:
            self.stack.setCurrentWidget(self.welcome)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setStyle("Fusion")
    app.setStyleSheet(style.QSS)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
