"""PDF export of one report."""
import textwrap
from matplotlib.figure import Figure
from matplotlib.backends.backend_pdf import PdfPages

from coil_tracker import plots, __version__

STATUS_COLORS = {"OK": "#2e7d32", "WATCH": "#ef8f00", "FAULT": "#c62828"}


def export_pdf(report: dict, fp: dict, path: str):
    meta = report.get("meta", {})
    with PdfPages(path) as pdf:
        fig = Figure(figsize=(8.27, 11.69))                      # A4
        ax = fig.add_axes([0.07, 0.04, 0.86, 0.92]); ax.axis("off")
        y = 1.0

        def line(text, size=10, color="black", weight="normal", gap=0.028, wrap=95):
            nonlocal y
            for t in textwrap.wrap(text, wrap) or [""]:
                ax.text(0, y, t, fontsize=size, color=color, weight=weight, va="top", transform=ax.transAxes)
                y -= gap

        line(f"Coil Tracker report — {report.get('coil', {}).get('name', 'unsaved coil')}", 15, weight="bold", gap=0.04)
        line(f"Scan {meta.get('date', '?')} · {meta.get('protocol', '')} · {meta.get('scanner_model', '')} "
             f"· files: {', '.join(meta.get('files', []))}", 9, "#555555")
        base = report.get("baseline_dates") or []
        line(f"Compared with baseline: {', '.join(base) if base else 'none yet'}"
             f" · drift over {report.get('n_drift_scans', 0)} scans", 9, "#555555", gap=0.04)
        line(f"STATUS: {report['status']}", 14, STATUS_COLORS[report["status"]], "bold", 0.035)
        line(report["summary"], 10, gap=0.04)
        line("Check these elements first", 12, weight="bold", gap=0.032)
        for i, e in enumerate(report["top"], 1):
            tag = "PHYSICALLY VERIFY · " if i <= report["verify_n"] else ""
            line(f"{i}. {e['short']}  [{tag}{e['level']}]  score {e['score']:.1f}", 10, weight="bold", gap=0.024)
            line(f"    {e['reason']}", 9, "#333333", gap=0.026, wrap=100)
        y -= 0.01
        if report["alerts"]:
            line("Alerts", 12, weight="bold", gap=0.032)
            for a in report["alerts"]:
                line(f"• {a}", 9, gap=0.024, wrap=105)
        y -= 0.01
        line("Levels: Fault = clearly abnormal in this scan or missing · Watch = changed or drifting vs this "
             "coil's baseline · Routine check = nothing clearly wrong, top of the list. "
             f"Coil Tracker {__version__}.", 8, "#777777", wrap=120)
        pdf.savefig(fig)

        fig = Figure(figsize=(11.69, 8.27))
        ax = fig.add_axes([0.03, 0.03, 0.94, 0.92]); ax.axis("off")
        ax.set_title("Full ranking", loc="left", fontsize=12, weight="bold")
        rows = [[str(i), e["short"], e["level"], f"{e['score']:.1f}", f"{e['single']:.1f}", f"{e['change']:.1f}",
                 f"{e['drift']:.1f}", e["reason"][:95]] for i, e in enumerate(report["ranking"], 1)]
        per_page = 45
        for start in range(0, len(rows), per_page):
            if start:
                fig = Figure(figsize=(11.69, 8.27))
                ax = fig.add_axes([0.03, 0.03, 0.94, 0.92]); ax.axis("off")
            t = ax.table(cellText=rows[start:start + per_page],
                         colLabels=["#", "Element", "Level", "Total", "This scan", "Vs baseline", "Drift", "Why"],
                         colWidths=[0.03, 0.06, 0.08, 0.05, 0.06, 0.07, 0.05, 0.6], loc="upper left", cellLoc="left")
            t.auto_set_font_size(False); t.set_fontsize(6.5); t.scale(1, 1.05)
            pdf.savefig(fig)

        pdf.savefig(plots.layout_figure(fp, report, Figure(figsize=(11.69, 6))))
