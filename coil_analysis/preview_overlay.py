"""
Synthetic Coil Overlay Preview
--------------------------------
Loads the real MEMPRAGE brain MRI and places a synthetic 8-element coil ring
on it using ray-cast surface detection — clean cylindrical blobs, one per channel.

Output PNGs are saved to:  coil_analysis/overlay_preview/
"""

import os
import sys
import numpy as np
import nibabel as nib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coil_analysis.snr_analysis import compute_otsu
from coil_overlay.ct_mri_registration import visualize_coil_overlay
from scipy.ndimage import binary_fill_holes, binary_erosion, label as ndlabel

# ── Config ─────────────────────────────────────────────────────────────────────

NIFTI_FILE = os.path.join(
    os.path.dirname(__file__), "..", "nifti_output", "memprage_echo1_cXX23.nii.gz"
)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "overlay_preview")

N_ELEMENTS   = 8    # coil channels
ELEM_RADIUS  = 7    # voxels — radius of each cylindrical coil element
COIL_COVERAGE = 0.65  # fraction of brain Z-extent covered by the coil ring

# ── Load MRI ───────────────────────────────────────────────────────────────────

print("Loading MRI volume...")
img = nib.load(NIFTI_FILE)
data = img.get_fdata()
if data.ndim == 4:
    print(f"  4D volume ({data.shape[3]} vols) — averaging")
    data = data.mean(axis=3)
print(f"  Shape: {data.shape}")

# ── Brain mask and centroid ────────────────────────────────────────────────────

_, brain_raw, _ = compute_otsu(data)
brain_filled = binary_fill_holes(brain_raw)

# Centroid from center-of-mass of filled brain mask
xs, ys, zs = np.where(brain_filled)
cx = float(xs.mean())
cy = float(ys.mean())
cz = float(zs.mean())
print(f"  Brain centroid: ({cx:.1f}, {cy:.1f}, {cz:.1f})")

# Z range for coil coverage
z_min, z_max = int(zs.min()), int(zs.max())
z_span = z_max - z_min
ez_start = int(cz - z_span * COIL_COVERAGE / 2)
ez_end   = int(cz + z_span * COIL_COVERAGE / 2)
ez_start = max(0, ez_start)
ez_end   = min(data.shape[2] - 1, ez_end)
print(f"  Coil Z coverage: {ez_start}–{ez_end}")

# ── Ray-cast to find surface position per element ─────────────────────────────
# For each of the 8 angles, shoot a ray in XY from the centroid outward.
# Walk along the ray until we leave the brain mask → that's the surface point.
# The coil element is centred at the surface point.

print(f"\nLocating coil element positions via ray casting...")
element_centres = []  # list of (ex, ey) in voxel coords

mid_z = int(cz)  # use mid-brain Z slice for ray casting

for i in range(N_ELEMENTS):
    angle = 2 * np.pi * i / N_ELEMENTS
    dx = np.cos(angle)
    dy = np.sin(angle)

    # Walk from centroid outward in steps of 1 voxel
    last_x, last_y = cx, cy
    for step in range(1, 150):
        rx = cx + step * dx
        ry = cy + step * dy
        ix, iy = int(round(rx)), int(round(ry))
        # Bounds check
        if ix < 0 or ix >= data.shape[0] or iy < 0 or iy >= data.shape[1]:
            break
        if not brain_filled[ix, iy, mid_z]:
            # Stepped outside — use the last point inside
            break
        last_x, last_y = rx, ry

    element_centres.append((int(round(last_x)), int(round(last_y))))
    print(f"  Ch{i+1}: angle={np.degrees(angle):.0f}°  centre=({int(round(last_x))}, {int(round(last_y))})")

# ── Build coil mask ────────────────────────────────────────────────────────────

print(f"\nBuilding coil mask (elem radius={ELEM_RADIUS} vx, Z={ez_start}–{ez_end})...")
coil_mask = np.zeros(data.shape, dtype=bool)

X = np.arange(data.shape[0])
Y = np.arange(data.shape[1])
Xg, Yg = np.meshgrid(X, Y, indexing="ij")

for i, (ex, ey) in enumerate(element_centres):
    # Disk mask in XY centred on (ex, ey)
    dist2 = (Xg - ex) ** 2 + (Yg - ey) ** 2
    disk = dist2 <= ELEM_RADIUS ** 2
    # Extrude through Z coverage
    coil_mask[:, :, ez_start:ez_end+1] |= disk[:, :, np.newaxis]

print(f"  Total coil voxels: {coil_mask.sum():,}")

# Verify we get 8 separate components
labeled, n_comp = ndlabel(coil_mask)
print(f"  Connected components: {n_comp}  (expected {N_ELEMENTS})")

# Build channel_rois: dilate each element inward toward the brain
print("\nExtracting per-channel brain ROIs...")
channel_rois = {}
for i in range(1, n_comp + 1):
    from scipy.ndimage import binary_dilation
    comp = labeled == i
    dilated = binary_dilation(comp, iterations=10)
    roi = dilated & brain_filled
    channel_rois[i] = roi

# ── Visualise ─────────────────────────────────────────────────────────────────

os.makedirs(OUTPUT_DIR, exist_ok=True)
print(f"\nSaving overlay images to {OUTPUT_DIR} ...")

visualize_coil_overlay(
    mri_data=data,
    coil_mask_mri=coil_mask,
    output_dir=OUTPUT_DIR,
    axial_fraction=0.5,
    channel_rois=channel_rois,
    brain_mask=brain_filled,
)

print("\nSaved:")
for fname in ["coil_overlay_axial.png", "coil_overlay_multiview.png", "coil_3d_scatter.png"]:
    path = os.path.join(OUTPUT_DIR, fname)
    print(f"  {fname}  ({os.path.getsize(path)//1024} KB)")

print("\nDone.")
