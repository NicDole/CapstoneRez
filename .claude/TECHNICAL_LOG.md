---
name: Technical Log — MRI Coil Fault Detection Pipeline
description: Full narrative technical log of all development phases, techniques, issues, and solutions. Intended for project continuity and reporting.
type: project
---

# MRI Phased Array Coil Fault Detection — Technical Log

## Project Summary

The goal is to build a software pipeline that automatically detects when one or more channels in a phased array MRI receive coil are disconnected or performing poorly. The clinical context: modern MRI systems routinely use coil arrays with 8 to hundreds of individual receive elements. When one or two elements fail (loose connector, broken cable, hardware fault), the corresponding region of the brain scan shows reduced signal-to-noise ratio (SNR). Currently this goes undetected until a radiologist notices image quality issues.

**The approach:** Analyze the SNR map of an MRI brain scan, divide the brain volume into spatial regions corresponding to coil coverage zones, and flag zones with anomalously low SNR relative to the global average. When CT-scan data of the physical coil is available, replace the crude spatial division with the actual coil geometry registered into MRI space.

**Scale context:** The production target is hundreds of coils (e.g., 128-channel or 256-channel arrays), where 1–2 might fail at any time. The current 8-channel implementation is a development testbed. Any algorithm that hardcodes 8 must be treated as a placeholder.

**Data:** A Siemens MEMPRAGE 4-echo T1 anatomical scan from a TMS-fMRI study. The NIfTI volume is 192×192×176×27 (27 echoes/volumes from the combined magnitude reconstruction). No per-channel raw data is available — the scanner output is already a sum-of-squares combined image. Per-channel data would require a scanner reconfiguration for future scans.

---

## Repository Structure

```
MRI_Viz_Project/
  nifti_output/
    memprage_echo1_cXX23.nii.gz   # Real MRI data (192x192x176x27, 1mm iso)
    memprage_echo1_cXX23.json     # dcm2niix sidecar
    (echo2, echo3, echo4 variants)

  coil_analysis/
    snr_analysis.py               # Phase 1 — SNR fault detection (callable functions)
    preview_overlay.py            # Phase 2 — synthetic coil overlay on real MRI

  coil_overlay/
    ct_mri_registration.py        # Phase 2 — CT segmentation + Kabsch registration
    test_overlay.py               # Tests for CT registration pipeline

  tests/
    __init__.py
    conftest.py                   # Shared phantom fixture
    test_snr_pipeline.py          # Tests for Phase 1 SNR pipeline

  coil_analysis/overlay_preview/
    coil_overlay_axial.png        # Output: axial view with 8 coil circles
    coil_overlay_multiview.png    # Output: three-plane view
    coil_3d_scatter.png           # Output: 3D scatter of coils + brain surface

  coil_analysis/snr_output/
    snr_map.png                   # Output: voxelwise SNR heatmap
    sector_report.png             # Output: per-sector bar chart
    fault_report.txt              # Output: text summary
```

---

## Phase 1 — SNR Map and Sector Fault Detection

### What

`coil_analysis/snr_analysis.py` loads the real MEMPRAGE NIfTI, computes a voxelwise SNR map, divides the brain into 8 spatial octant sectors, compares each sector's mean SNR to the global brain SNR, and flags sectors below 50% of the global mean as fault candidates.

### Why

The combined magnitude image (sum-of-squares of all coil channels) does not directly show which coil is dead, but it does show reduced signal in the spatial region that coil covers. By dividing the brain into 8 octants (anterior/posterior × left/right × superior/inferior), we approximate the spatial coverage of an 8-element ring array. A dead element leaves its octant with reduced SNR.

### Key Functions

- `load_volume(path)` — loads NIfTI, averages 4D→3D (handles 27-echo MEMPRAGE)
- `compute_otsu(data, bins=512)` — Otsu thresholding to separate brain from background; returns `(threshold, brain_mask, bg_mask)`
- `compute_snr_map(data, brain_mask, bg_mask)` — `snr_map = data / noise_std`, where `noise_std = std(background_voxels)`; returns global mean SNR and `fault_threshold = global_mean * FAULT_THRESHOLD_RATIO`
- `build_sectors(snr_map, brain_mask, fault_threshold, n_channels=8)` — splits brain mask at median X, Y, Z to form 8 octants; returns dict keyed 1–8 with mean SNR and status
- `generate_report(sectors, ...)` — saves SNR heatmap PNG, sector bar chart PNG, text report; returns list of faulty channel indices
- `run_analysis(nifti_path, output_dir)` — orchestrator; callable from other scripts or `__main__`

### Issues and Solutions

**Issue 1 — UnicodeEncodeError on `→` arrow**
- *Context:* Windows cmd/Git Bash with cp1252 encoding cannot print the `→` character.
- *Fix:* Replaced all `→` with `->` in print statements throughout the script.

**Issue 2 — Flat script not testable**
- *Context:* The original script was a single flat file with all logic at module level. No unit tests possible.
- *Fix:* Refactored into functions with a `if __name__ == "__main__"` guard. All module-level state moved into `run_analysis()`. The script still runs identically when called directly.

### Scalability Caveat

`build_sectors` divides the brain by median X/Y/Z midpoints, producing exactly 8 octants. This is structurally limited to 8 channels. **It will not generalize to 16, 32, or 256 channels without replacing the octant logic with geometry-based ROIs from the CT registration pipeline.** The `n_channels` parameter is currently unused for the subdivision logic itself — it only controls the output dict size.

---

## Phase 2 — Synthetic Testing Pipeline

### What

Since no "broken" MRI scans exist to test fault detection against, a synthetic phantom generator was built. `tests/conftest.py` creates a realistic bimodal MRI volume entirely in memory, with controllable signal/noise levels and per-octant signal killing.

### Why

Without ground-truth faulty scans, correctness of the detection algorithm cannot be verified. The phantom provides deterministic, reproducible test cases.

### Phantom Design (`make_brain_phantom`)

- Ellipsoid of signal voxels (brain): drawn at `signal_mean ± signal_std`
- Background ring: low-level noise at `noise_mean ± noise_std`
- 1-voxel zero border around the volume (to anchor Otsu threshold)
- "Dead" channel simulation: set a spatial octant's brain voxels to `noise_mean` level (NOT true zero — true zero would shift Otsu and break the threshold, making other tests flaky)

### Tests (all in `tests/test_snr_pipeline.py`)

| Test | What it verifies |
|---|---|
| `test_healthy_scan` | All 8 sectors OK on undamaged phantom |
| `test_single_dead_coil` | Kill ch1 → exactly ch1 flagged |
| `test_multiple_dead_coils` | Kill chs 1,4,6 → exactly those flagged |
| `test_degraded_coil` | Reduce ch3 to 20% signal → ch3 flagged |
| `test_otsu_threshold_range` | Threshold falls between noise×3 and signal×0.5 |
| `test_snr_map_values` | Global SNR within 20% of signal/noise |
| `test_all_coils_dead` | No crash; returns list |

### Issues and Solutions

**Issue 1 — `test_degraded_coil` flaky at factor=0.40**
- *Context:* Reducing ch3 to 40% of signal didn't reliably push it below the fault threshold because the degraded channel also pulled down the global mean, shrinking the margin.
- *Fix:* Changed to `factor=0.20` (80% reduction) and increased the phantom's SNR contrast by using `noise_std=3.0` instead of 5.0. This gives a clear global_mean dominated by 7 healthy channels, with ch3 well below threshold.

**Issue 2 — Dead channel set to true zero breaks Otsu**
- *Context:* Setting brain voxels to 0 collapses the bimodal histogram (Otsu needs both peaks present to threshold correctly).
- *Fix:* Set dead voxels to `noise_mean` (background level) rather than 0. Brain mask still correctly excludes them since they're indistinguishable from background.

---

## Phase 2 — CT-to-MRI Coil Registration Module

### What

`coil_overlay/ct_mri_registration.py` provides the full pipeline to overlay the physical coil geometry (from a CT scan) onto an MRI brain volume. It handles CT loading, coil metal segmentation, rigid-body registration via fiducial markers, mask transformation, brain ROI extraction, and visualization.

### Why

When a CT scan of the physical coil is available, we replace the crude octant approximation with the actual spatial geometry of each coil element — giving a precise mapping of which brain region each element covers.

### Key Functions

**`segment_coil_from_ct(ct_data, hu_threshold=400, min_voxels=100)`**
Thresholds CT HU values (metal is typically >400 HU), then applies 1× erosion to remove noise, 2× dilation to reconnect, and removes connected components below `min_voxels`. Returns a boolean mask.

**`register_fiducials(ct_pts, mri_pts) -> (R, t, rmse)`**
Implements the Kabsch algorithm for closed-form rigid registration:
1. Subtract centroids from both point sets
2. Compute cross-covariance: `H = P_centered.T @ Q_centered`
3. SVD: `U, S, Vt = np.linalg.svd(H)`
4. Reflection guard: if `det(Vt.T @ U.T) < 0`, negate the last column of `Vt`
5. `R = Vt.T @ diag([1,1,d]) @ U.T`
6. `t = mri_centroid - R @ ct_centroid`
7. RMSE = mean distance after applying (R, t) to ct_pts

**`transform_mask_to_mri_space(...)`**
Converts coil voxels to world coordinates via CT affine, applies (R, t), converts to MRI voxel indices via inverse MRI affine. One binary dilation pass fills nearest-neighbor gaps from the coordinate transform.

**`extract_channel_rois(coil_mask_mri, brain_mask, n_expected, dilation_iters=4)`**
Labels connected components in the transformed coil mask (one component per physical coil element). Dilates each component toward the brain to get the brain tissue region closest to that element. Works for any number of components — `n_expected` is only used for a warning printout.

**`simulate_ct_phantom(shape, n_coil_elements, seed)`**
For testing only. Generates: air background (HU=-800), head ellipsoid (HU=40), and `n_coil_elements` thin metal strips arranged in a ring around the ellipsoid (HU=1500). Fiducials are staggered in Z (alternating +/-30% of radius height) to avoid coplanarity in the Kabsch solver.

### Issues and Solutions

**Issue 1 — Kabsch SVD fails on coplanar fiducials**
- *Context:* `simulate_ct_phantom` originally placed all 8 fiducials at the same Z height (ring around equator). The cross-covariance matrix H became rank-deficient, and the SVD produced numerically unstable rotation matrices.
- *Fix:* Added Z staggering: `z_offset = int(rz * 0.3 * (1 if i % 2 == 0 else -1))` — alternating fiducials up/down by 30% of the ellipsoid's Z radius.

**Issue 2 — `segment_coil_from_ct` producing 5186 components**
- *Context:* Early version of `preview_overlay.py` called `segment_coil_from_ct` on the MRI data (not a CT). MRI brain signal doesn't have the discrete metal HU contrast that CT does, so thresholding produced thousands of fragmented components.
- *Fix:* `preview_overlay.py` switched to a completely different approach (ray-casting, see below). `ct_mri_registration.py` itself is only meant to be used with actual CT data.

### Scalability

`extract_channel_rois` uses connected component labeling — it scales to any number of coil elements automatically. The only hardcoded reference is the default value `n_expected=8` which is purely for warning messages.

---

## Phase 2 — Synthetic Coil Overlay Preview (`preview_overlay.py`)

### What

A standalone script that loads the real MEMPRAGE MRI and synthetically places an N-element coil ring on the brain surface, then renders three output images: axial slice with coil contours, three-plane multiview, and 3D scatter plot.

### Why

The CT scan of the physical coil doesn't exist yet. This script provides a visual preview of what the final CT-registered overlay will look like, validates that the visualization functions work on real MRI data, and serves as a demo.

### Algorithm

1. **Load and average:** Load `memprage_echo1_cXX23.nii.gz`, average 27 echoes → 3D volume (192×192×176)
2. **Brain segmentation:** Otsu threshold → `binary_fill_holes` → filled brain mask
3. **Centroid:** Center-of-mass of filled brain mask
4. **Z coverage:** `cz ± 0.65 * z_span / 2` defines the axial extent of the coil ring
5. **Ray-casting:** For each of N_ELEMENTS angles (evenly spaced 0–360°), shoot a ray from centroid outward in XY. Walk voxel by voxel until the ray exits `brain_filled`. The last voxel inside the brain is the element center.
6. **Coil mask:** For each element center, create a disk of radius `ELEM_RADIUS` voxels in XY, extruded through the Z coverage range. Union of all disks = coil mask.
7. **Channel ROIs:** Each labeled component dilated 10 iterations into the brain = per-channel brain ROI.
8. **Visualize:** Call `visualize_coil_overlay(data, coil_mask, output_dir, brain_mask=brain_filled)`

### Configuration Constants (top of script)

```python
N_ELEMENTS   = 8    # change to match actual coil count
ELEM_RADIUS  = 7    # voxels — scale with voxel size and coil diameter
COIL_COVERAGE = 0.65  # fraction of brain Z-extent the ring covers
```

### Issues and Solutions

**Issue 1 — Brain fills entire FOV; coil ring outside volume bounds**
- *Context:* MEMPRAGE brain spans X:1–191, Y:0–168 (nearly the full 192×192 volume). Naive "place ring outside brain bounding box" produced `rx=137.8 > 192` — index out of bounds.
- *Fix:* Replaced bounding-box approach with ray-casting. The ray stops AT the brain surface, so the element is always within valid array bounds.

**Issue 2 — 5186 components from brain surface carving**
- *Context:* An intermediate approach tried carving the brain surface with morphological operations and segmenting the coil from that. The result was 5186 noisy fragments instead of 8 clean elements.
- *Fix:* Discarded the surface-carving approach entirely. Used clean cylindrical blobs (one per ray-cast endpoint) — guaranteed exactly N_ELEMENTS components.

**Issue 3 — `nib.as_closest_canonical` OrientationError**
- *Context:* After averaging the 4D volume to 3D, we tried to create a new `Nifti1Image(data_raw, img.affine, new_header)` then reorient it to RAS. But the affine from the original 4D NIfTI has a degenerate k-column (the third spatial column is all zeros). nibabel's `set_qform` calls SVD on this matrix and raises `HeaderDataError: Could not decompose affine`.
- *Root cause:* dcm2niix stores this MEMPRAGE with a broken spatial affine. The file's sform/qform encoding has the slice direction (k) missing from the affine. `nib.aff2axcodes(img.affine)` returns `('P', 'S', None)` — the None indicates the degenerate axis.
- *Attempted fix:* Build a clean 3D NIfTI with fresh header → same error. Reorient the original 4D img object → `OrientationError: Cannot drop coordinates`.
- *Final fix:* Skip RAS reorientation entirely. The data in `img.get_fdata()` is already usable as-is. Slicing `data[:, :, z]` gives the correct-looking axial planes (sagittal anatomical view). The coronal/sagittal views in the multiview PNG show intensity striping due to the missing affine axis — this is an inherent limitation of this NIfTI file.

**Issue 4 — Brain surface invisible in 3D scatter**
- *Context:* `visualize_coil_overlay` estimated the brain surface using `mri_data > (vmax * 0.1)`. Since the brain fills the entire FOV, this mask covers nearly the entire volume, and `binary_erosion` gives a surface that is just the outer wall of the 192×192×176 cube — not a brain shape. Scatter points with `s=0.3, alpha=0.05` were invisible.
- *Fix 1:* Added optional `brain_mask` parameter to `visualize_coil_overlay`. When called from `preview_overlay.py`, the Otsu-segmented `brain_filled` mask is passed in. This correctly bounds the surface to anatomical brain tissue.
- *Fix 2:* Changed erosion from 1 iteration to 3 (thicker shell = more visible), increased `surf_pts` subsample to 15000, increased marker size to `s=4, alpha=0.35`.

---

## Phase 3 — Real Fault Data: Per-Channel Detection (2026-11-01)

> **Design rule (user, 2026-11-01): detection must use a single scan with no reference.** The benchmark scan is only for validating the detector's answer afterwards. Phase 3a below was the first attempt, which used benchmark as an input (wrong design, kept for the record). Phase 3b is the current method.

### What
Received `10-30 Scan - Broken Coil/`, a controlled fault experiment scanned 2026-10-30. It has three conditions (**benchmark**, **dematch**, **untune**; the user believes every scan has at least one broken element, so benchmark is not assumed healthy), each with a 2 mm MPRAGE and an SNR-map scan, in two forms:
- DICOM (`CoilFailureTest/`, Siemens XA enhanced multi-frame), including scanner-computed `SNRMap`, `GFactor`, `NoiseCovariance`, `Combined_Image`
- Raw TWIX `.dat` k-space, which **contains every channel separately** (first time we have per-channel data)

Coil (read from `.dat` header): **HEAD128 head coil, 108 elements selected**, split across 6 sub-coils (HEAD128_5301–5306, labels J/K/L/M/N/O/P/Q + number). The SNR-map scan (`NoiseSensitivityMap` sequence) is one transverse slice, 448×448, 4 averages, plus 448 noise-only readouts (≈400k noise samples per channel).

### Phase 3a (superseded): benchmark-vs-test comparison
Per-channel ratios of SNR, noise and noise-correlation change between each test scan and the benchmark. This is now only the **validation** step (`validate()`, `--validate-against`).

Validation results (what changed relative to benchmark):
| Condition | Changed element | Evidence |
|---|---|---|
| dematch | **HEAD128_5304:L05** | noise +11.4% (all others <1%), every top correlation change involves L05; SNR unchanged. Score 97 vs next 6.1 |
| untune | **HEAD128_5304:L07** | SNR −14%, noise +4%, every top correlation change involves L07. Score 33.8 vs next 16.8; neighbours (L02, K01, K03, J01, J08) also flagged as coupling side-effects |

The **original octant pipeline (`snr_analysis.py`) detects nothing** on the MPRAGEs: all octants are within ±1% of benchmark. One faulty element out of 108 is diluted in the combined image.

Insights from 3a: a dematched preamp scales signal and noise together (SNR flat, noise up); an untuned element loses SNR and changes its noise coupling with neighbours.

### Phase 3b (superseded by 3c): single-scan detection
```
python coil_analysis/channel_fault_analysis.py SCAN.dat [SCAN2.dat ...] [--validate-against REF.dat] [--inject-dead HEAD128_5304:L07]
```
Each scan is analysed on its own:
1. **Per-element maps:** central 64×64(×64) of k-space → low-res magnitude image per channel. 3D MPRAGE raw data is fully sampled (128×128 lines/partitions, 108 ch, 200 readout samples, centre col 72), so this gives whole-head maps. Signal = mean of the 200 brightest head voxels.
2. **Reference-free predictions from the same scan:**
   - *neighbours*: the 4 elements whose map centroids are nearest
   - *mirror partner*: mirror the centroids across the left-right axis (auto-chosen as the axis giving the most mutual pairs, 91–99% mutual on this coil; pairs follow names, e.g. P*k*↔Q*k*, J03↔J06)
3. **Score:** robust z (median/MAD) of log signal vs neighbours and vs mirror. **Flag when below −5 on both.** Noise vs mirror is a secondary indicator (|z| > 5). Exact noise comes from noise-adjust readouts when present; MPRAGE has none, so noise is estimated from air.
4. **Iterative reference cleaning:** a dead element's map is pure noise, so its centroid sits mid-head and it can become another dead element's neighbour or mirror. Elements low on either test (< −5), or dead-like against the whole coil (< −8), are removed as references and everything is re-scored.
5. Per-sub-coil median signal vs the whole coil is reported separately (group-level weakness is not an element fault).

**Real-data results (HEAD128, 108 elements, 3D MPRAGE):**
- **Unmodified scans: 0 flags** in benchmark, dematch and untune.
- **Simulated faults** (each of 108 elements in turn, signal removed with its noise floor kept):

| Signal left | Detected |
|---|---|
| 0% (disconnected) | 108/108 |
| 10% | 108/108 |
| 25% | ~40/108 |
| 50% | ~3/108 |
| Two dead at once (60 random pairs) | 120/120 |

  A handful of extra flags occur (J07/J08) only when the dead element is in or next to the J sub-coil.
- **The induced faults (dematch L05 +11% noise, untune L07 −14% SNR) are below single-scan sensitivity.** Natural element-to-element variation is larger: noise differs ~9% between mirror pairs and ~17% across the coil. The single-scan detector does not flag them, and the validation step lists them as "changed but below single-scan sensitivity". This is a physical limit of single-scan detection, not a code bug.
- **Consistent anomaly in every scan:** sub-coil HEAD128_5305 (J01–J08) at only **20–23% of the coil's median signal**, with low SNR vs neighbours (z −4 to −7), J02/J08 noise asymmetry 1.37, and abnormal noise correlations. Also Q19 is weaker than its mirror (z ≈ −4). Possibly coil design (sub-coil position), possibly the persistent broken element. Needs coil documentation or the experimenters.

### Single-scan approaches tried that did NOT separate the induced faults
Raw per-channel ranking (noise/signal/SNR vs all channels); noise vs same-sub-coil peers; noise correlation vs image-overlap model (R² 0.19); noise-correlation profile vs neighbours; correlation row vs mirror; 3D map shape vs mirrored partner. All gave L05/L07 |z| ≤ 2.

### Key insights
- **Single-scan detection works by predicting each element from its own scan** (neighbours + left-right mirror) and needs whole-head per-element maps. Use the 3D MPRAGE raw data; the 2D single-slice SNR map is too limited because many elements barely see the slice.
- **Two dead elements can mask each other** unless suspicious elements are excluded as references.
- **Sensitivity floor ≈ 75% signal loss** on this coil. Good for the project's real target (disconnected or badly connected elements), not for subtle tuning/matching drift.

### Issues and Solutions
- **Windows Python 3.13 had disappeared** (`C:\Python313` gone, `python` resolved to a 0-byte stub). Reinstalled with `winget install Python.Python.3.13 --scope user`, giving `%LOCALAPPDATA%\Programs\Python\Python313\python.exe`. Existing user packages in `%APPDATA%\Python\Python313\site-packages` were picked up automatically. Added `twixtools`. `python` on PATH may still hit the Store stub; use the full path or the `py` launcher.
- **twixtools crashes on PMU (physio) blocks of this XA file** (`struct.error`). Fix: `read_twix(..., parse_pmu=False, keep_syncdata=False)`. It prints harmless "Software version: VD/VE (!?)" and "unknown magic key" warnings.
- **Channel → element mapping:** `channel_hdr[i].ChannelId == lADCChannelConnected − 1` from `MeasYaps.sCoilSelectMeas.aRxCoilSelectData[0].asList`.
- **First version used SNR only and missed dematch completely.** Added the noise and correlation indicators.
- **`snr_analysis.run_analysis` crashed on a new output dir.** Added `os.makedirs` in `generate_report`.
- MPRAGE DICOMs converted with WSL dcm2niix into `nifti_output/scan_1030/` (XA warnings, but valid 128³ volumes).

- **Self-pairing bug:** a dead element's mirror landed on itself, zeroing its mirror score. Fix: exclude self (diagonal = inf).
- **Over-aggressive reference exclusion** (threshold −2.5) flagged the naturally weak J sub-coil in healthy scans. Fixed by excluding only flag-level suspects plus dead-like elements (global z < −8).
- `Remove-Item` on `coil_analysis/channel_fault_output/*` is blocked by the sandbox, so stale Phase 3a PNGs may remain there. Current outputs are `*_detection.png`, `*_elements.png`, `summary.json`.

### Tests
`tests/test_channel_fault.py`: 9 tests on a synthetic symmetric ring coil (48 elements). Covers mirror pairing, healthy → no flags, disconnected element at 3 positions, 10%-signal element, two dead elements, abnormal noise, and validation confirming a detection. Full suite: **25 passed**.

---

## Phase 3c — Noise covariance + ranked suspect list (2026-11-02)

### Why
Phase 3b only answered yes/no and flagged nothing on the three 10-30 scans. The user's goal: feed in one headcap session and get back which elements (if any) are broken. Because elements need manual repair anyway, **a ranked top-3 suspect list is useful** even when nothing is certain. Literature review before coding (sources in `docs/References.docx`):
- Within-scan per-element vs combined-image checks catch dead/near-dead elements (IOP PMB 2024, 892 exams, 100% recall).
- The **noise covariance matrix (NCM)** is the most recommended QA tool; coupling faults can cost 18% SNR while invisible in images (Tunnicliffe et al., ISMRM 2011). Its single-scan rules (>35% diagonal spread, off-diagonal >25% of max diagonal) only catch large faults and assume equal loading.
- Partial faults are caught in practice by **tracking each element over time** (ISMRM 2018 #4221, Siemens patent US11073588). See "Option D" below.

### What changed
- **Input is a session**: all `.dat` files of one coil state (`load_session`). Maps come from the 3D scan (MPRAGE); the NCM comes from the noise-only readouts (SNR-map scan, ~400k samples/element). Channels are matched by element label.
- **Four measurements per element**, all log-scale: signal, noise level (NCM diagonal), SNR (signal/noise), noise coupling (mean |noise correlation| with its 4 spatial neighbours).
- Each measurement is robust-z scored vs neighbours and vs mirror partner. **Evidence** = how far the element deviates from *both* in the same direction (0 otherwise). Signal and SNR count only when low; noise and coupling count both ways.
- **Score** = sqrt(sum of evidence²), equal weights, fixed before looking at the real-data results (no tuning to L05/L07).
- Output: ranked list, top 3 marked as suspects; "high-confidence" flag when any single evidence > 5. Plots: `*_ranking.png` (stacked evidence per element), `*_suspects.png` (top 3 maps vs mirror). JSON: `*_summary.json`.
- CLI: `python coil_analysis/channel_fault_analysis.py MPRAGE.dat SNRMAP.dat [--validate-against REF_MPRAGE.dat REF_SNRMAP.dat] [--inject-dead ELEMENT]`.

### Results (10-30 HEAD128, 108 elements)
| Session | Top 3 suspects | Induced fault rank (validation) |
|---|---|---|
| benchmark | O12, Q19, J01 | — |
| dematch | O12, Q19, O14 | L05 **#70** |
| untune | O12, Q19, J01 | L07 **#15** |

- **The induced faults did not reach the top 3.** L07 improved (maps only #9–#54 depending on version; now #15). L05 got *higher* signal and slightly higher noise (z +1.6 vs neighbours, +0.4 vs mirror): a dematch scales both, which a single scan can't distinguish from a naturally strong element.
- **The same elements top the list in all three sessions** (O12, Q19, J01, O14, Q20). They are stable, element-specific quirks or persistent faults. O12: unusually *low* noise coupling (max corr 0.14 vs coil 99th percentile 0.25) plus low signal/SNR. Q19: weak vs mirror P19. J01: low SNR (J sub-coil, 20–23% of median signal).
- **Tunnicliffe's NCM rules fail for the whole coil in every session** (diagonal spread 4.7×, max off-diagonal 0.56 of max diagonal), including benchmark, as expected for a large head coil with uneven loading.
- **Simulated faults rank much better as a list** (each of 108 elements, real NCM kept):

| Signal left | Rank 1 | In top 3 | High-confidence flag |
|---|---|---|---|
| 0% | 108/108 | 108/108 | 108/108 |
| 10% | 107/108 | 108/108 | 108/108 |
| 25% | 89/108 | 107/108 | 60/108 |
| 50% | 5/108 | 61/108 | 3/108 |

### Key insight
Single-scan sensitivity is limited by **stable element-to-element differences**, not random noise: the same elements top the list in every session. That is exactly what a per-coil history would remove.

### Option D — coil history (NOT built; awaiting prof's approval)
Save each analysed session's per-element measurements; score future sessions against that coil's own history as well. First scan = single-scan only; sensitivity improves with every scan. Industry standard for catching partial faults. Reintroduces a kind of reference, so it needs sign-off.

### Tests
`tests/test_channel_fault.py` now 10 tests (added: NCM adds the coupling measure with no false flags; 1.6× noise element ranked first). Full suite: **26 passed**.

### Notes
- Old outputs (`*_detection.png`, `*_elements.png`, `summary.json`, Phase 3a PNGs) are still in `channel_fault_output/` because deletion is sandbox-blocked; current outputs are `*_ranking.png`, `*_suspects.png`, `*_summary.json`.
- Validation "changed" lists are now longer (NCM noise is very precise, so small differences pass |z| > 8). O12/J01 appearing as "changed and in top 3" is not a real hit; they top the benchmark too.

---

## Phase 3d — Research: can L05/L07 be detected at all? (2026-11-02)

Goal: find any way to detect the two induced faults reliably. Scripts in the session scratchpad (`lab.py`, `signature.py`, `oracle.py`, `law.py`, `history*.py`, `fig_history.py`); not yet in the project code.

### 1. What the faults physically do (learned by comparing scans; research only)
| | L05 dematch | L07 untune | typical healthy scan-to-scan change | healthy spread within one scan |
|---|---|---|---|---|
| Noise level | +11.4% | +4.2% (+12% vs initial calibration) | 0.1% | 17% |
| Noise coupling to neighbours | +5% (with all L elements) | +19%; shifted away from J07/J08 (−40%) towards L06/L08 | 0.5% | 30% |
| Noise-spectrum tilt | −1.7 | −5.9 (slope flips sign: resonance moved) | 0.27 | 1.9 |
| Signal | **+31%** (dematch made it stronger) | −11% | 0.6–4% | 55% |
| SNR | +18% | −14% | 0.6–4% | 53% |

Every element has a precise fingerprint (features correlate 0.998 between scans). Relative to their own previous values, both faults are 30–140× normal variation. Relative to other elements in one scan, they are smaller than natural differences.

### 2. Single-scan approaches tested (all failed to reach top 3)
- Noise power spectrum shape (tilt, curvature, roughness): L07 tilt flips but lands at z −1.5 (#10)
- Coherence vs frequency between neighbours, phase consistency of noise vs image overlap
- Rank-1 "noise efficiency" model; image-overlap prediction of noise correlation (R² only 0.24–0.32)
- Receiver calibration factors in header (`aFFT_SCALE`): identical across scans, explain little of the noise spread
- Geometry "laws": SNR predicted from map size/depth/position (R² 0.93) — residual spread still ~14%
- Robust Mahalanobis over 14 features; adding spectral tilt to the detector (L07 #13 → #12)
- **Upper bound (Fisher d′, best possible linear combination of all 14 features):** L05 ≤ 2–4 σ, L07 ≤ 5–9 σ — and that optimum needs the fault's own signature to define it (overfitting). Practical result stays L07 ≈ #5–15, L05 ≈ #10–70.
**Conclusion:** on this coil, these mild faults are below the single-scan limit. Single scan is fine for gross faults (dead, ≥75% loss), not for detuning/mismatch of this size.

### 3. Breakthrough: noise fingerprint vs the coil's own history (Option D, data-backed)
- Every `.dat` file contains measurement 0 `AdjCoilSens` with 256 noise-only readouts (also the localizer). **Caveat:** the scanner only re-runs it when adjustments are redone — in MID40–45 it is one identical copy (taken healthy, after the localizer). Fresh noise per state came from the SNR-map scans.
- Score per element = robust z of noise-level change (normalised to neighbours, cancels smooth loading changes) combined with how much its noise-correlation row changed.
- Results (`coil_analysis/channel_fault_output/noise_fingerprint_history.png`):

| history → new | top 3 | L05 | L07 |
|---|---|---|---|
| healthy calib #1 → healthy calib #2 | J07 3.3, Q06, N01 | #71 | #75 |
| healthy calib → dematch session | **L05 17.8**, L07 9.4, L08 | **#1** | #2 |
| healthy calib → untune session | **L07 12.8**, J07, Q19 | #7 | **#1** |
| benchmark SNR-map → dematch SNR-map | **L05 87.5**, L01 11, L04 9 | **#1** | #4 |
| benchmark SNR-map → untune SNR-map | **L07 24.4**, J01 16, J08 9 | #56 | **#1** |

- **The "benchmark" SNR-map scan (MID43) is not fully healthy:** vs the healthy calibration, L07 noise +7.6% and L05 +3.3% (every other element ≤0.6%, except J07 3.7%, which also varies between the two healthy calibrations). The coil was cycled bench→dematch→untune twice (MID40–42 MPRAGE, MID43–45 SNR map) and the first round was apparently not fully undone. Explains part of the user's "every scan has a broken element".
- Moderate scores of J07, Q17–Q19, Q06, Q09 in calibration→SNR-map comparisons are likely a measurement-type effect (calibration noise has 512 samples vs 896); same-type comparisons are cleaner.
- MPRAGE k-space corners are not usable as noise (power correlates only 0.10 with real noise).

### Recommendation
Keep single-scan ranking for gross faults; add a per-coil **noise fingerprint** (a known-good "coil passport" + routine updates) for partial faults. Needs: prof approval; a fresh noise-only measurement each session (SNR-map scan, forced re-adjust, or a short noise-only scan); validation across different subjects/loading.

---

## Phase 3e — New single-scan ideas: tuning indicator (2026-11-02)

Goal: find single-scan signals other than "compare with neighbours/mirror". No new scanner time is available, so only the 10-30 data. Scripts are in the session scratchpad (`footprint.py`, `shape_change.py`, `mirror_pattern.py`, `siblings.py`, `hw.py`, `parity.py`, `tilt_robust.py`, `delay.py`, `rawnoise.py`, `timestats.py`, `combo*.py`, `coverage*.py`, `simulate*.py`, `fig_tuning.py`); not yet in the project code.

### Why single-scan image methods can't see these faults
`shape_change.py` fits each element's dematch/untune map to its own benchmark map (research only). **L05's map is unchanged in shape and ×1.32 brighter; L07's is ×0.86 with no clear shape change.** A pure gain change looks exactly like a healthy element with a slightly hotter amplifier, so no image-only feature can expose it. The usable gain-free signals are SNR (+18%/−17%), noise coupling, and the noise *spectrum shape*.

### Breakthrough: the noise-spectrum tilt is set by receiver hardware + the element's tuning
- The tilt of each channel's noise spectrum **alternates with receiver (ADC) channel parity**: odd channels tilt up, even channels tilt down. Parity alone explains ~50% of the healthy spread. The same pattern appears in the independent `AdjCoilSens` calibration noise, so it is hardware.
- After subtracting the odd/even median, the residual is each element's own tuning (natural spread is 4.2× the scan-to-scan measurement noise, so it is real tuning scatter, not noise).
- **Tuning indicator** = |robust z| of the parity-corrected tilt (vs the whole coil, not neighbours, because tilt doesn't depend on position).
  - Untune scan: **L07 #2** (z −3.5). Stable over different frequency bands (#1–#2 for all wide bands).
  - Dematch scan: L05 #4–9.
  - J01 is #1 in *every* scan, including healthy calibration → possibly a genuinely mistuned element.
- Spatial neighbours explain none of the tilt; design siblings (same element number in the same sub-coil type) explain ~30%, mostly through the same parity effect.

### Combined single-scan detector (proposed here; built in Phase 3f)
1. Phase 3c evidence, but **signal and SNR count in both directions** (a dematched element is too *strong*, not weak).
2. Add the **tuning indicator** as a 5th equal-weight measure.
3. **Coverage rule:** elements with < 1/3 of the median signal are reported separately as "not assessable in this scan" (phantom barely reaches them; their neighbour comparisons are unreliable). Here: J01–03, J06–08, Q19, Q20. Dead elements are still caught by the separate global DEAD_Z check. The improvement holds for any cut from the weakest 3 to the weakest 8 elements.

| Scan | Current (3c) | New |
|---|---|---|
| Untune: L07 | #13 | **#2** |
| Dematch: L05 | #70 | **#3** |
| "Benchmark": L05 / L07 | #90 / #49 | #57 / #26 (not flagged) |

**Simulation (planted into each of 108 elements of the benchmark session, `simulate*.py`):**

| Planted fault | 3c top-3 | New top-3 | New top-10 |
|---|---|---|---|
| Untune-like (sig ×0.86, noise ×1.04, L07's tilt shift) | 3% | **99%** | 100% |
| Same, tilt shifted the opposite way | 3% | **100%** | 100% |
| Half-strength untune | 3% | 44% | 77% |
| Quarter-strength untune | 3% | 13% | 26% |
| Dematch-like (sig ×1.32, noise ×1.11, L05's tilt shift) | 2% | 22% | 48% |
| Nothing planted (false alarm) | 3% | 3% (chance) | |

Figure: `coil_analysis/channel_fault_output/tuning_indicator.png`.

**Limits / honesty:** the tilt idea was found by looking at how L07 changed (research), and the simulation uses L05/L07's observed effect sizes. Planting into all 108 elements and both tilt directions guards against overfitting to L07's position, but it still needs confirmation on a new fault. Detuning smaller than ~half of L07's is not reliably caught in one scan. Dematch remains the weaker case.

### Ideas tested that did NOT help (record so they aren't repeated)
- Mirror-symmetry of whole coupling *patterns* (rows of the correlation / partial-correlation matrix): nothing.
- Partial correlation / "unique noise fraction" (precision matrix): no fault signal.
- Per-element group delay from cross-spectral phase slopes (`delay.py`): faulty elements' delay doesn't even change vs benchmark (preamp decoupling flattens the phase response).
- Time-domain noise stats from raw samples (kurtosis, spikes, power drift, burstiness, I/Q balance, DC, lag-1 autocorrelation): faulty noise is as clean and stationary as healthy; oracle change ≈ 0.
- Parity correction of noise *level*: explains more spread (R² 0.44 → 0.67) but no detection gain.
- Brightness not explained by map shape (peakiness/spread regression): shape explains 64% of brightness spread but not L05's gain.
- `CoilFailureTest/` DICOMs are the scanner's own SNR map / g-factor / noise covariance of the same raw data — no new information.
- Literature: Tunnicliffe et al. 2011 (NCM QA) confirm single-scan NCM thresholds only catch gross failures and recommend longitudinal comparison.

---

## Phase 3f — Tuning indicator built into the detector (2026-11-03)

The user approved the Phase 3e proposal. The changes in `coil_analysis/channel_fault_analysis.py` are below.

### Loading
- `load_scan` keeps each channel's ADC number and computes the noise power spectrum (`noise_spectrum`).
- The spectrum is the fftshifted mean of |FFT|² over the noise-only readouts.
- `load_session` takes the noise from the scan with the longest noise recording, reordered to the map labels.

### Features and evidence
- **Tilt:** `spectrum_tilt` uses the central 20–80% of the band, normalises each spectrum to its mean, divides by the median shape across channels, and takes the 1st Legendre coefficient of the log ratio.
- **Tuning indicator:** `tuning_z` takes each element's robust z of tilt minus the median of its ADC-parity group. The evidence is |z|, compared with the whole coil, not with neighbours or the mirror.
- **Two-sided measures:** all measures are now two-sided, and `MEASURES` is now just name → label.

### Coverage rule
In `detect`, an element is listed separately as "low signal, not ranked" when:
- its signal is below 1/3 of the coil median (`LOW_COVERAGE`), and
- it is not dead-like (`DEAD_Z`), and
- it is not clearly below its neighbours or mirror (it is not in the `Z_FAULT` exclusion set).

Such elements go to the end of `ranking` and are never flagged. The output lists them with their signal ratio, so a new entry stands out. The JSON key is `low_signal_not_ranked`; the code key is `det["not_assessable"]`.

### Results with the real code (64³ maps)

| Scan | Rank of the broken element | Top 3 | Flags |
|---|---|---|---|
| Untune | **L07 #2** | O12, L07, O09 | none |
| Dematch | **L05 #3** | O12, O09, L05 | none |
| "Benchmark" | L05 #52, L07 #28 | — | none |

In the untune scan the main reason for L07 is tuning; in the dematch scan it is tuning for L05.

Low-signal elements not ranked: Q19, Q20 and J01–J04, J06–J08. The untune scan also adds J05 and O13.

### Planted-fault check with the real code (`sim_final.py`, benchmark session, each of the 108 elements)

| Planted fault | Top 3 | Top 10 |
|---|---|---|
| Untune-like | 98% | 100% |
| Half-strength untune | 35% | 74% |
| Dematch-like | 18% | 55% |
| Nothing planted | 3% (chance) | |

Weak or dead elements, made in the images:

| Signal left | Top 3 | Flagged | Wrongly listed as low signal, not ranked |
|---|---|---|---|
| 0% | 100% | 108/108 | 0 |
| 10% | 100% | 108/108 | 0 |
| 25% | 90% (3c: 99%) | 60/108 | 10/108 |
| 50% | 59% (3c: 56%) | 3/108 | 23/108 |

### The coverage-rule trade-off
A partly weak element in a moderately covered area looks the same as a poorly covered element from one scan: signal ratio, ratio to neighbours and ratio to mirror all overlap (`cov_diag.py`). Extra thresholds to separate them would be fitted to this one dataset. The rule is therefore kept simple and the separate list is always printed.

### Tests
`tests/test_channel_fault.py` now has 18 tests, all passing. The 8 new ones check:
- tilt recovery
- the hardware parity pattern produces no flags
- parity correction is needed to find a mistuned element
- an untuned element ranks #1 in both tilt directions
- a brighter element ranks #1 (two-sided signal)
- a low-coverage ring is not ranked, even with an extreme tilt
- a dead element inside a low-coverage ring is still ranked #1

---

## Data Notes

**NIfTI affine:** The `memprage_echo1_cXX23.nii.gz` file has a degenerate affine from dcm2niix:
```
[[  0.   0.   0.  87.5  ]
 [ -1.   0.   0. 106.55 ]
 [  0.   1.   0. -63.77 ]
 [  0.   0.   0.   1.   ]]
```
The k-axis column is all zeros, meaning nibabel cannot determine the slice direction. As a result:
- `nib.aff2axcodes()` returns `('P', 'S', None)`
- `nib.as_closest_canonical()` fails with OrientationError
- Coronal/sagittal slices of the array show intensity banding (likely inter-slice variation in the MEMPRAGE preparation pulses)
- `data[:, :, z]` still produces clinically meaningful anatomical planes and is used as the "axial" view

**No per-channel data:** The current NIfTI is a combined sum-of-squares magnitude image. Individual coil channel images are not available from these DICOM files. The octant-based fault detection in Phase 1 is the current workaround. Future scans could be acquired with per-channel raw data output enabled.

---

## Running the Pipeline

```bash
# Phase 1: SNR analysis on real MRI
cd MRI_Viz_Project
python coil_analysis/snr_analysis.py
# Outputs: coil_analysis/snr_output/*.png + fault_report.txt

# Phase 2: Coil overlay preview
python coil_analysis/preview_overlay.py
# Outputs: coil_analysis/overlay_preview/*.png

# Tests (all should pass)
pytest tests/ coil_overlay/test_overlay.py -v
```

**Dependencies:** nibabel, numpy, scipy, matplotlib, scikit-image, pytest

---

## Current Status

All code is working and tested. Verified outputs as of 2026-04-12:

| File | Status | Notes |
|---|---|---|
| `snr_analysis.py` | Working | 8-channel octant detection on real MRI |
| `tests/test_snr_pipeline.py` | 7/7 passing | Synthetic phantom tests |
| `ct_mri_registration.py` | Working | Kabsch + overlay; tested on synthetic CT |
| `coil_overlay/test_overlay.py` | 9/9 passing | |
| `preview_overlay.py` | Working | 8 coil elements placed on real brain surface |
| `overlay_preview/coil_overlay_axial.png` | Good | 8 clean circles on brain |
| `overlay_preview/coil_overlay_multiview.png` | Partial | Axial clean; coronal/sagittal striped (affine issue) |
| `overlay_preview/coil_3d_scatter.png` | Good | Brain surface cloud visible with coil cylinders |

---

## Next Steps

**As of 2026-11-03 (after Phase 3f — tuning indicator built in)**
- Confirm the tuning indicator on a new, unseen fault when scanner time is available (ideally a detuned element other than L07)
- Ask the experimenters to check **J01's tuning on the bench**: it has the most extreme noise-spectrum tilt in every scan, including healthy calibration
- **Discuss Option D (per-coil noise fingerprint) with the prof**: Phase 3d shows it finds both L05 and L07 at #1 by a wide margin; single scan (3e) now reaches top 3 but with less margin
- Ask the experimenters whether L05/L07 were fully restored before the second benchmark (MID43 shows L07 +7.6%, L05 +3.3% noise vs initial calibration)
- If approved: build `noise_fingerprint.py` (store per-coil fingerprint, compare new sessions), and find out how to get fresh noise every session (re-adjust or short noise-only scan)
- Ask the experimenters about O12, Q19, J01: they top the suspect list in every session (persistent fault or normal?)
- Ask the experimenters: which elements were dematched/untuned (validation shows L05 and L07 changed), and is the J sub-coil (HEAD128_5305) weak by design?
- Get a scan with a genuinely disconnected element (unplugged) to validate the single-scan detector on a real gross fault, not just simulation
- Check whether more sensitivity is possible: higher-res maps, or using the noise-adjust readouts if they can be enabled for MPRAGE
- DICOM-only path using the scanner's `NoiseCovariance`/`SNRMap` series (9–20) for sites that don't export `.dat`
- Use the per-element centroids as coil-element positions for the 3D overlay

**Immediate (when CT data is available)**
1. CT-scan the physical coil array with fiducial markers visible in both CT and MRI
2. Run `load_ct` → `segment_coil_from_ct` → `register_fiducials` → `transform_mask_to_mri_space` → `extract_channel_rois` to replace the synthetic coil ring with actual geometry
3. Validate `n_expected` matches the actual coil count (change default from 8)

**Short-term improvements**
4. Fix the degenerate NIfTI affine — either re-run dcm2niix with different flags, or manually construct a correct affine from DICOM metadata (`PixelSpacing`, `ImageOrientationPatient`, `ImagePositionPatient`) so coronal/sagittal views render correctly
5. Add per-channel SNR time-series tracking — flag channels that degrade over multiple sessions (trending toward failure before complete disconnect)

**Architecture scale-up (for hundreds of coils)**
6. Replace `build_sectors` (Phase 1 octant approach) with `extract_channel_rois` (Phase 2 CT geometry) as the primary sector definition method — this already scales to any number of coils via connected-component labeling
7. Add a database/CSV store for per-session per-channel SNR values to enable longitudinal fault tracking
8. Consider a simple ML classifier (logistic regression or SVM on per-channel SNR features) trained on phantom data + known-fault cases to replace the hard threshold

**Data acquisition**
9. Request per-channel raw data from future scans (requires scanner reconstruction setting change: "coil images" or "channel-by-channel" mode depending on scanner software version)
