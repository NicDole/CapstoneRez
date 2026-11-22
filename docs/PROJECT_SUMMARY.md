# Coil Fault Detection — Project Summary

Updated Nov 21, 2026 · Live version (last synced Nov 4, before the Coil Tracker app): https://claude.ai/artifact/FiCy7YnzD6ChhXtjYe8As7

**Goal:** feed in one headcap session (Siemens raw `.dat`) and get back which receive-coil elements, if any, are broken. The target is coils with hundreds of elements. The output is a ranked top-3 suspect list to guide manual repair.

## TL;DR

- **From a single scan, the tool now puts both broken elements in its top 3:** L07 (untuned) is **#2** and L05 (dematched) is **#3**. Before, they were #15 and #70.
- **The key was the noise spectrum, not the images.** Each element's background noise is slightly tilted across the receiver band. After removing a hardware pattern (odd and even receiver channels tilt opposite ways), the tilt shows how well the element is tuned.
- **Dead or nearly dead elements** are still always found (100% in simulation).
- **Comparing against the coil's own history (Option D)** was approved and is now built into the **Coil Tracker** app (below). With one healthy baseline it puts all four real test faults (10-30 L05/L07, 11-13 M02/M09) at **#1**.

## What we tried

1. **Compare each element with its neighbours and its mirror element in the same scan**, using signal, noise, SNR and noise coupling. This finds gross faults reliably, but L05 and L07 ranked #70 and #15.
2. **Work out what the faults physically do** (comparing scans, for research only). Both turned out to be pure brightness changes: L05 is ×1.32 and L07 ×0.86, and the shape of their sensitivity maps is unchanged. In one image, that looks exactly like a healthy element, so image-only methods cannot see these faults.
3. **Coil history, i.e. Option D (research prototype):** compare each element's noise fingerprint with that coil's earlier healthy one. Both faults come out #1 by a wide margin (87 vs 11 for the next element).
4. **Noise-spectrum shape, i.e. the tuning indicator:** this became the final single-scan solution (below).

## What didn't work

- **Image-based ideas:** map shape, spread and peakiness, and brightness that the map shape doesn't explain. The faults are pure gain changes, so none of these change.
- **Left/right symmetry of whole noise-coupling patterns, and partial correlations:** no signal.
- **Signal delay through each element** (phase slope): unchanged in the faulty elements.
- **Time-domain noise statistics** (spikes, drift, bursts, I/Q balance): the faulty noise is just as clean.
- **Published noise-covariance pass/fail rules:** they fail the whole coil, even the healthy benchmark.
- **The scanner's own DICOM outputs** (SNR map, g-factor, noise covariance): the same raw data, so no new information.
- **More noise averaging:** the limit is the natural spread in tuning between elements, not measurement noise.

## Final solution (built and tested)

- **Five measurements per element:** signal, noise, SNR, noise coupling, and **tuning** (noise-spectrum tilt, compared with elements on the same odd or even receiver channels). They are combined with equal weights into one ranked list.
- **All measures are two-sided.** "Too strong" counts as well as "too weak", because the dematched L05 got brighter.
- **Low-signal list:** elements the phantom barely reaches (under 1/3 of typical signal) are listed separately with their signal level instead of being ranked. Here these are J01–J08 and Q19–Q20.

| Scan | Before | Now |
| --- | --- | --- |
| Untune: L07 | #15 | **#2** |
| Dematch: L05 | #70 | **#3** |
| False high-confidence flags | none | none |

| Fault planted at each of the 108 positions | Ranked in top 3 |
| --- | --- |
| Untune like L07 | 98% (was 3%) |
| Untune at half strength | 35% |
| Dematch like L05 | 18% (55% in top 10) |
| Dead or 10% signal left | 100% |
| 25% signal left | 90% (was 99%; some end up on the low-signal list) |
| Nothing planted | 3% (chance level) |

**Limits:**

- The tilt idea came from studying L07, so it needs confirming on a new fault.
- Dematch is still the weaker case.
- Detuning smaller than about half of L07's is not reliably caught from one scan.

## Coil Tracker app (Nov 2026)

A standalone Windows program (`CoilTracker.exe`) for post-docs and students. Guide and handover notes: `docs/COIL_TRACKER.md`.

- **Every scan runs both kinds of check:** the single-scan detector above (dead, missing, badly detuned elements) and the history checks (change since the healthy baseline, and drift over scans).
- **Output:** a top-5 list (top 3 always marked "physically verify"), alerts, an element-position plot, the full ranking and a PDF export.
- **Saves a ~130 KB summary per scan, not the raw data**, and no patient details, so the `.dat` files can be archived.
- **One healthy baseline is enough.** The first scan of a coil is checked on its own; once verified it is saved as the baseline.

| Real test fault | Single scan only | With baseline |
| --- | --- | --- |
| 10-30 L05 dematched | #3 | **#1** (92 vs 16) |
| 10-30 L07 untuned | #2 | **#1** (34 vs 11) |
| 11-13 M02 slightly unmatched + coil moved | #15 | **#1** (66 vs 10) |
| 11-13 M09 slightly untuned + coil moved | #72 | **#1** (14 vs 7) |

A healthy scan compared with another day's healthy scan (coil moved) came out OK, with no false alarm.

**Status:** built, automated tests and a packaged-app self-test pass, **but it has not yet been bug-tested by users.**

## Questions to ask

**Prof**

1. ~~Go or no-go on **Option D (coil history)**?~~ Approved; built as the Coil Tracker.
2. Is a top-3 suspect list enough, or does the tool need to say confidently whether anything is wrong at all?

**Experimenters**

3. **How strongly** were L05 and L07 dematched and untuned? This tells us how sensitive the tool is.
4. Were L05 and L07 **fully restored** before the second benchmark? It still shows L07 at +7.6% noise and L05 at +3.3%.
5. **O12** tops the list in every scan (noise coupling). Is it a known issue?
6. **J01** has the most extreme tuning tilt in every scan, including healthy calibration. Could someone check its tuning on the bench?
7. Is the **J sub-coil (about 20% signal)** weak by design, or is it the phantom position? Can we use a phantom that fills the coil?
8. Next scanner time:
   - a **new fault** on a different element, to confirm the method
   - a **healthy repeat** session, to measure false alarms
   - a **fresh noise-only scan** in every session
9. Do we have access to the scanner's **service coil check**, to cross-check dematch?

**Status:**

- The single-scan tool runs end to end with `python coil_analysis/channel_fault_analysis.py MPRAGE.dat SNRMAP.dat`.
- The Coil Tracker app builds to `dist\CoilTracker\` with `packaging\build_exe.bat`. Not yet bug-tested by users.
- All 48 tests pass.
- Details are in `.claude/TECHNICAL_LOG.md` (Phases 3c–4), and sources are in `docs/References.docx`.
