"""Entry point for the Coil Tracker app (also used by PyInstaller to build the .exe).

Hidden self-test, for checking a build without clicking through the app:
    CoilTracker.exe --selftest OUT.txt LIBRARY_DIR [SNRMAP.dat [MPRAGE.dat]]
It reads the library, re-evaluates its latest scan, exports a PDF next to OUT.txt and, if raw
files are given, runs the full analysis on them. Results are written to OUT.txt.
"""
import multiprocessing
import os
import sys
import traceback


def selftest(args):
    out = args[0]
    lines = []
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from coil_tracker import gui, pipeline, checks, __version__
        from coil_tracker.library import Library
        from coil_tracker.pdf_report import export_pdf
        lines.append(f"Coil Tracker {__version__}")
        app = QApplication(sys.argv)
        lib = Library(args[1])
        w = gui.MainWindow()
        w.lib = lib
        w.refresh_coils()
        c = lib.coils()[0]
        scans = lib.scans(c["id"])
        w.open_saved(c["id"], scans[-1]["id"])
        app.processEvents()
        rep = w.report_page.state["report"]
        lines.append(f"library ok: {c['name']}, {len(scans)} scans, latest {rep['status']}, "
                     f"top {[e['short'] for e in rep['top']]}")
        pdf = os.path.join(os.path.dirname(os.path.abspath(out)), "selftest_report.pdf")
        export_pdf(rep, w.report_page.state["fp"], pdf)
        lines.append(f"pdf ok: {os.path.getsize(pdf)} bytes")
        if len(args) > 2:
            fp = pipeline.analyse_files(args[2], args[3] if len(args) > 3 else None,
                                        progress=lambda m: lines.append("  " + m))
            match = lib.match_coil(fp)
            r = lib.evaluate(match[0][0]["id"], fp) if match else checks.evaluate(fp)
            lines.append(f"analysis ok: {len(fp['labels'])} elements, {fp['meta'].get('date')}, "
                         f"status {r['status']}, top {[e['short'] + '(' + e['level'] + ')' for e in r['top']]}")
        lines.append("SELFTEST PASSED")
    except Exception:
        lines.append(traceback.format_exc())
        lines.append("SELFTEST FAILED")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        selftest(sys.argv[2:])
    else:
        from coil_tracker.gui import main
        main()
