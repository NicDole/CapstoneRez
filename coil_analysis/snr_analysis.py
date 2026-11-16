"""
Phased Array MRI Coil Fault Detection — Phase 1
------------------------------------------------
Loads a NIfTI volume (converted from Siemens DICOM), computes a voxel-wise SNR map,
divides the brain into 8 spatial sectors (one per coil channel), and flags sectors
with anomalously low signal as potential coil faults.

Usage:
    python snr_analysis.py
"""

import os
import numpy as np
import nibabel as nib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

# ── Configuration ─────────────────────────────────────────────────────────────

NIFTI_FILE = os.path.join(
    os.path.dirname(__file__), "..", "nifti_output", "memprage_echo1_cXX23.nii.gz"
)
OUTPUT_DIR = os.path.dirname(__file__)
FAULT_THRESHOLD_RATIO = 0.5
N_CHANNELS = 8
AXIAL_SLICE_FRACTION = 0.5

# ── Core functions ─────────────────────────────────────────────────────────────

def load_volume(nifti_path: str) -> np.ndarray:
    """Load NIfTI file and return a 3D float64 array. Averages 4D volumes along axis 3."""
    img = nib.load(nifti_path)
    data = img.get_fdata()
    print(f"  Volume shape: {data.shape}")
    print(f"  Voxel sizes:  {img.header.get_zooms()}")
    if data.ndim == 4:
        print(f"  4D volume detected ({data.shape[3]} volumes) — averaging across 4th dim")
        data = data.mean(axis=3)
    print(f"  3D volume shape after reduction: {data.shape}")
    return data


def compute_otsu(data: np.ndarray, bins: int = 512):
    """
    Compute Otsu threshold on data (ignoring exact zeros).
    Returns (threshold, brain_mask, background_mask).
    """
    flat = data[data > 0].ravel()
    if flat.size == 0:
        return 0.0, np.zeros(data.shape, dtype=bool), np.zeros(data.shape, dtype=bool)

    counts, bin_edges = np.histogram(flat, bins=bins)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    total = counts.sum()
    weight_bg, sum_bg = 0.0, 0.0
    best_thresh, best_var = 0.0, 0.0
    total_mean = (counts * bin_centers).sum() / total

    for cnt, center in zip(counts, bin_centers):
        weight_bg += cnt / total
        if weight_bg == 0 or weight_bg == 1:
            continue
        weight_fg = 1 - weight_bg
        sum_bg += cnt * center / total
        mean_bg = sum_bg / weight_bg
        mean_fg = (total_mean - sum_bg) / weight_fg
        var = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if var > best_var:
            best_var, best_thresh = var, center

    brain_mask = data > best_thresh
    background_mask = (data > 0) & (~brain_mask)
    return best_thresh, brain_mask, background_mask


def compute_snr_map(data: np.ndarray, brain_mask: np.ndarray, background_mask: np.ndarray,
                    fault_threshold_ratio: float = FAULT_THRESHOLD_RATIO):
    """
    Compute voxel-wise SNR map and summary statistics.
    Returns (snr_map, noise_std, global_mean_snr, fault_threshold).
    """
    background_voxels = data[background_mask]
    noise_std = background_voxels.std() if background_voxels.size > 0 else 0.0
    if noise_std == 0:
        noise_std = 1.0

    snr_map = np.zeros_like(data)
    snr_map[brain_mask] = data[brain_mask] / noise_std

    brain_snr = snr_map[brain_mask]
    global_mean_snr = brain_snr.mean() if brain_snr.size > 0 else 0.0
    fault_threshold = global_mean_snr * fault_threshold_ratio

    return snr_map, noise_std, global_mean_snr, fault_threshold


def build_sectors(snr_map: np.ndarray, brain_mask: np.ndarray,
                  fault_threshold: float, n_channels: int = N_CHANNELS) -> dict:
    """
    Divide brain into 8 octant sectors and compute per-sector SNR stats.
    Returns dict keyed 1–8, each value: {name, voxels, mean_snr, status}.
    """
    xs, ys, zs = np.where(brain_mask)
    if xs.size == 0:
        return {i: {"name": f"Ch{i}", "voxels": np.array([]),
                    "mean_snr": 0.0, "status": "FAULT LIKELY"} for i in range(1, n_channels + 1)}

    x_mid = (xs.min() + xs.max()) // 2
    y_mid = (ys.min() + ys.max()) // 2
    z_mid = (zs.min() + zs.max()) // 2

    sectors = {}
    idx = 1
    for z_label, z_sl in [("Superior", slice(z_mid, None)), ("Inferior", slice(None, z_mid))]:
        for y_label, y_sl in [("Anterior", slice(y_mid, None)), ("Posterior", slice(None, y_mid))]:
            for x_label, x_sl in [("Right", slice(x_mid, None)), ("Left", slice(None, x_mid))]:
                octant = np.zeros(snr_map.shape, dtype=bool)
                octant[x_sl, y_sl, z_sl] = True
                octant &= brain_mask
                voxels = snr_map[octant]
                mean_snr = voxels.mean() if voxels.size > 0 else 0.0
                status = "FAULT LIKELY" if mean_snr < fault_threshold else "OK"
                sectors[idx] = {
                    "name": f"Ch{idx} ({x_label[:1]}{y_label[:2]}{z_label[:2]})",
                    "voxels": voxels,
                    "mean_snr": mean_snr,
                    "status": status,
                    "slices": (x_sl, y_sl, z_sl),
                }
                idx += 1
    return sectors


def generate_report(sectors: dict, global_mean_snr: float, fault_threshold: float,
                    output_dir: str, data: np.ndarray = None, snr_map: np.ndarray = None,
                    brain_mask: np.ndarray = None, otsu_threshold: float = None) -> list:
    """
    Print per-channel table and save histogram.png, snr_map_axial.png, sector_report.png.
    Returns list of fault channel indices.
    """
    print("\n" + "=" * 65)
    print(f"{'Channel':<22} {'Mean SNR':>9} {'vs Global':>10} {'Status':>12}")
    print("=" * 65)
    faults = []
    for ch, info in sectors.items():
        ratio = info["mean_snr"] / global_mean_snr if global_mean_snr > 0 else 0
        print(f"  {info['name']:<20} {info['mean_snr']:>9.1f} {ratio:>9.1%}  {info['status']:>12}")
        if info["status"] != "OK":
            faults.append(ch)
    print("=" * 65)
    if faults:
        print(f"\n  !! Potential faults detected in channels: {faults}")
    else:
        print("\n  All channels within normal range.")

    if data is None or snr_map is None or brain_mask is None:
        return faults

    os.makedirs(output_dir, exist_ok=True)
    background_mask = (data > 0) & (~brain_mask)
    brain_voxels = data[brain_mask]
    background_voxels = data[background_mask]

    # Figure 1: Histogram
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(background_voxels, bins=200, color="steelblue", alpha=0.6, label="Background (noise)")
    ax.hist(brain_voxels, bins=200, color="tomato", alpha=0.6, label="Brain tissue")
    if otsu_threshold is not None:
        ax.axvline(otsu_threshold, color="black", lw=1.5, linestyle="--",
                   label=f"Otsu threshold ({otsu_threshold:.0f})")
    if brain_voxels.size > 0:
        ax.axvline(brain_voxels.mean(), color="red", lw=1.5,
                   label=f"Brain mean ({brain_voxels.mean():.0f})")
    ax.set_xlabel("Voxel Intensity")
    ax.set_ylabel("Count")
    ax.set_title("Voxel Intensity Histogram — MEMPRAGE Echo 1")
    ax.legend()
    ax.set_yscale("log")
    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, "histogram.png"), dpi=150)
    plt.close(fig)

    # Figure 2: SNR map axial slice
    xs, ys, zs = np.where(brain_mask)
    x_mid = (xs.min() + xs.max()) // 2 if xs.size > 0 else data.shape[0] // 2
    y_mid = (ys.min() + ys.max()) // 2 if ys.size > 0 else data.shape[1] // 2
    slice_idx = int(data.shape[2] * AXIAL_SLICE_FRACTION)
    snr_slice = snr_map[:, :, slice_idx].T

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    im = axes[0].imshow(snr_slice, cmap="hot", origin="lower", aspect="equal")
    axes[0].axvline(x_mid, color="cyan", lw=1, linestyle="--", alpha=0.7)
    axes[0].axhline(y_mid, color="cyan", lw=1, linestyle="--", alpha=0.7)
    plt.colorbar(im, ax=axes[0], label="SNR")
    axes[0].set_title(f"SNR Map — Axial slice {slice_idx}")
    axes[0].set_xlabel("X")
    axes[0].set_ylabel("Y")

    z_mid = (zs.min() + zs.max()) // 2 if zs.size > 0 else data.shape[2] // 2
    sector_map = np.zeros(data.shape[:2])
    for ch, info in sectors.items():
        x_sl, y_sl, z_sl = info["slices"]
        octant_2d = np.zeros(data.shape, dtype=bool)
        octant_2d[x_sl, y_sl, z_sl] = True
        octant_2d &= brain_mask
        sector_map[octant_2d[:, :, slice_idx]] = (
            0 if info["mean_snr"] < fault_threshold else info["mean_snr"]
        )
    im2 = axes[1].imshow(sector_map.T, cmap="RdYlGn", origin="lower", aspect="equal",
                          vmin=0, vmax=max(global_mean_snr * 1.5, 1))
    plt.colorbar(im2, ax=axes[1], label="Mean SNR (red=fault)")
    axes[1].set_title("Sector SNR Map")
    axes[1].set_xlabel("X")
    axes[1].set_ylabel("Y")
    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, "snr_map_axial.png"), dpi=150)
    plt.close(fig)

    # Figure 3: Bar chart
    ch_labels = [sectors[i]["name"] for i in range(1, len(sectors) + 1)]
    ch_means = [sectors[i]["mean_snr"] for i in range(1, len(sectors) + 1)]
    colors = ["tomato" if m < fault_threshold else "steelblue" for m in ch_means]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(ch_labels, ch_means, color=colors)
    ax.axhline(global_mean_snr, color="black", lw=1.5, linestyle="--")
    ax.axhline(fault_threshold, color="red", lw=1.5, linestyle=":")
    ax.set_xlabel("Channel (Spatial Sector)")
    ax.set_ylabel("Mean SNR")
    ax.set_title("Per-Channel SNR — Phased Array Coil Fault Detection")
    ax.tick_params(axis="x", rotation=20)
    ax.legend(handles=[
        mpatches.Patch(color="steelblue", label="Normal"),
        mpatches.Patch(color="tomato", label="Fault Likely"),
        plt.Line2D([0], [0], color="black", lw=1.5, linestyle="--",
                   label=f"Global mean ({global_mean_snr:.1f})"),
        plt.Line2D([0], [0], color="red", lw=1.5, linestyle=":",
                   label=f"Fault threshold ({fault_threshold:.1f})"),
    ])
    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, "sector_report.png"), dpi=150)
    plt.close(fig)

    return faults


def run_analysis(nifti_path: str, output_dir: str) -> dict:
    """Run the full SNR analysis pipeline. Returns result dict."""
    print("Loading NIfTI file...")
    data = load_volume(nifti_path)

    otsu_threshold, brain_mask, background_mask = compute_otsu(data)
    print(f"\nOtsu threshold: {otsu_threshold:.1f}")

    snr_map, noise_std, global_mean_snr, fault_threshold = compute_snr_map(
        data, brain_mask, background_mask
    )
    print(f"Noise std (background): {noise_std:.2f}")
    print(f"Global brain SNR: mean={global_mean_snr:.1f}")
    print(f"Fault threshold:  {fault_threshold:.1f} (< {FAULT_THRESHOLD_RATIO*100:.0f}% of mean)")

    sectors = build_sectors(snr_map, brain_mask, fault_threshold)
    faults = generate_report(sectors, global_mean_snr, fault_threshold, output_dir,
                             data=data, snr_map=snr_map, brain_mask=brain_mask,
                             otsu_threshold=otsu_threshold)

    print(f"\nSaved outputs to: {output_dir}")
    print("Done.")
    return {"sectors": sectors, "global_mean_snr": global_mean_snr,
            "fault_threshold": fault_threshold, "faults": faults}


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_analysis(NIFTI_FILE, OUTPUT_DIR)
