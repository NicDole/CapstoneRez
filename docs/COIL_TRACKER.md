# Coil Tracker — user guide and handover notes

Coil Tracker rates every element of an MRI receive coil from one QA scan session. It keeps a
small history per coil, so it catches both **sudden faults** (dead, disconnected, missing or badly
detuned elements) and **slow drift** (slight detuning, mismatch, wear) over months of scans.

> **Status (v1.0.0, 2026-11-21):** built; the automated tests and the packaged-app self-test pass
> on the real 10-30 and 11-13 data. **It has not yet been bug-tested by users**, so treat it as a
> beta and note any problems.

- [Part 1: Using the app](#part-1-using-the-app)
- [Part 2: How it works](#part-2-how-it-works)
- [Part 3: For the next developer](#part-3-for-the-next-developer)

---

## Part 1: Using the app

### Install
1. Copy the whole `CoilTracker` folder (from `dist\CoilTracker` after a build, or from whoever
   shared it) anywhere on a Windows PC.
2. Double-click `CoilTracker.exe`. No Python or internet needed.
   - Windows may warn that the app is from an unknown publisher (it is not code-signed):
     click *More info → Run anyway*.
3. Reading a session needs about **8 GB of free RAM** and takes **1–2 minutes**.

### Scan protocol (important)
History comparisons only mean something if the setup is the same each time:
- Use the **same phantom**, placed the same way, every time.
- Always acquire the **SNR-map scan** (`SNR_Map_Tra_H0`). It is required: it contains the
  noise-only measurement that most checks rely on.
- Also acquire the **MPRAGE** (`t1_mprage_sag_iso2mm`) when possible. It gives better element
  maps. Without it the app still works but warns that elements at the edge of the phantom
  (e.g. the J sub-coil) are less reliable.
- Export the raw data (`.dat`, TWIX) for both.

### Everyday workflow
1. **Analyse new scan…** → pick the session folder (the app fills in the SNR map and MPRAGE),
   or browse to the files. Add your name and a note if useful ("after cable repair").
2. **Which coil is this?** The app suggests the matching coil from the library, based on the
   element names in the scan. For a new or custom coil, choose *A new coil* and give it a name.
3. **Read the result** (see below).
4. **Save it:**
   - *Save to history*: a normal QA scan.
   - *Save as healthy baseline*: the first good scan of a coil (after you verified the listed
     elements), or another known-good scan to strengthen the baseline.
   - *Save as new baseline (after repair)*: after a repair or retune. Earlier baselines stop
     counting for new scans (they are kept for old reports).
5. Optional: **Export PDF…** for the lab notebook.

### Reading the result
- **Status banner**
  - **FAULT**: an element is clearly abnormal in this scan, or an element is missing.
  - **WATCH**: an element changed or is drifting compared with this coil's baseline.
  - **OK**: nothing clearly wrong.
- **Check these elements first**: the top 5. The top 3 are always marked
  **Physically verify**, even on a healthy coil, so there is always a short list to bench-check.
  The *Why* column explains each one, in plain units where possible, e.g.
  "Changed since baseline (mostly noise): signal −25.2%, noise −22.2%, SNR −3.8%".
- **Levels per element**
  - *Fault*: clearly abnormal from this scan alone.
  - *Watch*: changed or drifting vs the baseline in a noise-based measure.
  - *Routine check*: nothing clearly wrong; simply high on the list.
  - *Low signal*: the phantom barely reaches it (listed separately, not ranked).
- **"Signal changed, noise steady (likely position)"**: signal moves when the coil or phantom
  sits slightly differently. A real fault has so far always changed the noise too, so these are
  ranked but never raise a Watch on their own.
- **Alerts**
  - Missing elements: check connectors and cables.
  - A whole sub-coil or the whole coil changing together: probably position, phantom or loading.
  - Low-signal elements.
  - Scans of different types (with/without MPRAGE) being compared.
- **Element positions plot**: where the suspects sit on the coil (from the element maps).
- **Show full ranking**: every element with its scores from each check.

### Coil page (click a coil on the left)
- All saved scans with status, top suspects and baseline (*active* or *superseded*).
- **Open report** re-opens a saved scan exactly as it was judged at the time.
- **Set / unset baseline**, **New baseline from here (after repair)**, **Delete scan**.
- **Element history**: pick an element to see its noise, SNR, signal and tuning tilt over time,
  relative to the rest of the coil (so whole-coil changes cancel out).

### Known quirks: "Mark as checked"
Some elements are always near the top (on the lab HEAD128: O12's noise coupling, J01's tuning
tilt). If someone bench-tests one and it is fine, click **Mark as checked** and add a note.
- It then no longer counts as a fault from a single scan.
- Changes over time (vs baseline, drift) still count.
- A checked element that becomes **dead or near-dead is always flagged** as a Fault again.
- Checked elements are listed under **Known quirks** in every report and on the coil page, each
  with an **Unmark** button. You can also mark an element from the coil page without a report.
- The list applies to the coil as a whole, so re-opened old reports also use the current list.

### The library
- Each researcher can keep their own library (default: `Documents\CoilTracker Library`), or point
  the app at a shared folder with **Library folder…** so the whole lab sees the same history.
- Each saved scan is a small summary file (~130 KB): per-element measurements, the noise
  covariance and a compressed noise spectrum. **No images and no patient details**, so the raw
  `.dat` files can be archived afterwards.
- Back up the library folder like any other data.

### First scan of a coil
- There is no history yet, so only the single-scan and missing-element checks run.
- The banner says so: verify the listed elements, then save the scan as the **healthy baseline**.
- From the second scan on, *change since baseline* runs. From the third scan on, *drift* runs too.

### Troubleshooting
| Message | What to do |
|---|---|
| "No noise-only readouts found" | You picked the wrong file; the SNR-map scan is required. |
| "Not enough memory" | Close other programs; reading needs ~8 GB of free RAM. |
| Coil not suggested | The element names changed (different coil or plug). Choose the coil manually or create a new one. |
| Many elements flagged at once | Usually position or phantom changes. Check the alerts, and keep the setup consistent. |

---

## Part 2: How it works

Each scan runs four checks, implemented in `coil_tracker/checks.py`.

1. **Single scan** (`coil_analysis/channel_fault_analysis.py`, unchanged from the Phase 3f detector)
   - Every element gets five measurements: signal, noise, SNR, noise coupling to its neighbours,
     and the **tuning indicator**.
   - The tuning indicator is the tilt of the element's noise spectrum. It is corrected for an
     odd/even receiver-channel hardware pattern, so what remains reflects how well the element
     is tuned.
   - Each measurement is compared with the element's neighbours and its mirror element. Only
     deviations from both, in the same direction, count as evidence.
2. **Missing elements**: elements in the coil's profile that are not in the scan.
3. **Change since baseline**
   - For each measurement: (this scan − median of the baseline scans).
   - That change is compared with how **all elements** changed (robust z), and with how its
     **neighbours** changed. It only counts when both agree.
   - So whole-coil and regional changes (phantom position, loading) cancel out, and **one
     baseline is enough**.
4. **Drift**
   - Theil–Sen slope of each measurement over the scans since the baseline (up to 10 scans,
     needs 3).
   - The slopes are judged the same two-way way as the change check.

**Combined ranking and levels**
- Total score = √(single² + change² + drift²), with equal weights.
- *Fault*: single-scan evidence above 5.
- *Watch*: change or drift evidence above 5 in a **noise-based measure** (noise, coupling or
  tuning tilt).

**Thresholds** are constants at the top of `checks.py` and `channel_fault_analysis.py`:
`FLAG = 5`, `LOW_COVERAGE = 1/3`, `GROUP_SHIFT = 3%`, `GLOBAL_SHIFT = 5%`.

### Validation so far (real HEAD128 data)

| Test day | Fault | Single scan only | With baseline (combined) |
|---|---|---|---|
| 10-30 | L05 dematched | #3 | **#1** (score 92, next 16) |
| 10-30 | L07 untuned | #2 | **#1** (34 vs 11) |
| 11-13 | M02 slightly unmatched + coil moved | #15 | **#1** (66 vs 10) |
| 11-13 | M09 slightly untuned + coil moved | #72 | **#1** (14 vs 7) |

- **False alarms:** a healthy scan compared against another day's healthy scan (coil moved) came
  out OK, with no Watch.
- **SNR-map-only mode:** all four faults are still #1 with a baseline, but J-sub-coil elements give
  false alarms.

**Limits**
- Only two fault days have been tested, each with a same-day healthy reference.
- Long-term behaviour is untested: scanner software updates, phantom changes, months of drift.
- Detunes smaller than about half of L07's are missed from a single scan; they need history.

---

## Part 3: For the next developer

### Layout
```
coil_analysis/channel_fault_analysis.py   single-scan detector + raw-data loading (TWIX)
coil_tracker/
    fingerprint.py   scan summary: build, save/load (.npz), subset
    checks.py        the four checks, combined ranking, levels, alerts
    library.py       coil folders, scan files, baselines, coil matching, evaluation context
    pipeline.py      raw files -> fingerprint (with progress messages)
    plots.py         element-position and element-history figures
    pdf_report.py    PDF export
    gui.py           PySide6 app
    style.py         McGill colour palette and the Qt stylesheet
run_coil_tracker.py  entry point (also for PyInstaller)
packaging/           build script and pinned requirements
examples/demo_library/  real 10-30 + 11-13 history (summaries only) to try the app
tests/test_coil_tracker.py  simulated-coil tests for every check and the library
```

### Run from source
```bash
pip install -r packaging/requirements-app.txt
python run_coil_tracker.py
pytest tests/
```
Use **Library folder…** → `examples/demo_library` to explore real data without the raw files.

### Build the .exe
```bash
packaging\build_exe.bat
```
The output is `dist\CoilTracker\`; ship the whole folder.

### Library format (version 1)
- `coils/<id>/coil.json`: name, expected elements, checked elements, events.
- `coils/<id>/scans/<scan-id>.npz`: arrays `signal`, `noise_std`, `noise_cov`, `tilt`, `centroid`,
  `adc`, `psd`, plus a JSON `header`.
- `coils/<id>/scans/<scan-id>.json`: date, files, notes, baseline flags, status.

There is no database, so the library is safe on network drives. If you change the fingerprint,
bump `FORMAT_VERSION` and keep `load()` able to read old files.

### Suggested next steps
0. **User bug testing** of v1.0.0 (not done yet): analyse, save, baselines, mark as checked,
   PDF export, a shared library on a network drive.
1. **Collect regular QA scans** (e.g. weekly, same phantom) to measure the real false-alarm rate
   and tune `FLAG` per measure.
2. **Per-element repeatability:** once a coil has 5+ healthy scans, judge each element against its
   own scan-to-scan spread instead of the cross-element spread.
3. **Faster loading:** read only the noise readouts and the centre of k-space instead of the whole
   file (currently 1–2 min, ~8 GB RAM).
4. **Better SNR-map-only mode:** a coverage rule that works on 2D maps, so that MPRAGE is not
   needed.
5. **Step vs drift:** tell a one-off jump apart from a slow trend. A jump currently also adds to
   the drift score.
6. **Physical thresholds:** agree with the coil engineers on a pass/fail line (e.g. "SNR loss
   > 5%").
7. **Real coil geometry** for the layout plot, instead of positions estimated from the maps.
8. **DICOM-only path** (scanner `NoiseCovariance` / `SNRMap` series) for sites without `.dat` export.
9. **Multi-user editing** of a shared library (currently last-write-wins on `coil.json`).
10. **Code signing / installer**, to avoid the Windows "unknown publisher" warning.
