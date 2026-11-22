# MRI receive-coil fault detection

Two ways to use this repository:

1. **Coil Tracker** (the app): a standalone Windows program for post-docs and students.
   - Give it a QA scan of a coil (Siemens raw `.dat` SNR map, plus optionally an MPRAGE).
   - It rates every element, lists the top 5 to check, and keeps a small history per coil to
     catch slow drift as well as sudden faults.
   - **Guide and handover notes:** [docs/COIL_TRACKER.md](docs/COIL_TRACKER.md)
   - **Status (v1.0.0, 2026-11-21):** built and passing its automated tests and packaged-app
     self-test, **but not yet bug-tested by users**. Expect rough edges and report them.
2. **The single-scan detector** (`coil_analysis/channel_fault_analysis.py`): a command-line
   research tool that the app builds on.

More documents:
- **Project summary:** [docs/PROJECT_SUMMARY.md](docs/PROJECT_SUMMARY.md)
- **Full technical log:** [.claude/TECHNICAL_LOG.md](.claude/TECHNICAL_LOG.md)
- **Sources:** [docs/References.docx](docs/References.docx)

## Coil Tracker

Run the built app: `dist\CoilTracker\CoilTracker.exe`. The `.exe` is not in the repository
(about 174 MB), so build it first (see below).
Or run from source:

```bash
pip install -r packaging/requirements-app.txt
python run_coil_tracker.py
```

To try it with real data and no raw files, use **Library folder…** → `examples/demo_library`
(the 10-30 and 11-13 test days).

Build the standalone app (output: `dist\CoilTracker\`, ship the whole folder):

```bash
packaging\build_exe.bat
```

## Single-scan detector (command line)

```bash
python coil_analysis/channel_fault_analysis.py MPRAGE.dat SNRMAP.dat
```

Outputs (ranking plot, suspect maps, JSON summary) go to `coil_analysis/channel_fault_output/`.

## Tests

```bash
pytest tests/ coil_overlay/test_overlay.py
```

Scan data is not included in this repository.
