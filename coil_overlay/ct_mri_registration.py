"""
CT-to-MRI Coil Overlay Pipeline
---------------------------------
Loads a CT scan of the phased array coil, segments the coil structure,
registers CT space to MRI space using fiducial markers (Kabsch algorithm),
and overlays the coil geometry on the MRI brain volume.

Workflow:
    1. CT scan of physical coil → segment_coil_from_ct() → coil binary mask in CT space
    2. Fiducial markers (visible in both CT and MRI) → register_fiducials() → rigid transform (R, t)
    3. transform_mask_to_mri_space() → coil mask in MRI voxel space
    4. extract_channel_rois() → per-channel brain ROI masks
    5. visualize_coil_overlay() → output PNGs

For testing without real CT data, use simulate_ct_phantom().

Fiducial CSV format (load_fiducials_from_csv):
    label,ct_x,ct_y,ct_z,mri_x,mri_y,mri_z
    F1,10.2,44.1,30.5,12.1,48.3,29.8
"""

import os
import numpy as np
from scipy.ndimage import (
    binary_erosion, binary_dilation, label as ndlabel
)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ── CT Loading ────────────────────────────────────────────────────────────────

def load_ct(nifti_path: str):
    """
    Load a CT NIfTI file (values in Hounsfield Units).
    Returns (ct_data: float32 ndarray, affine: 4x4 ndarray).
    """
    import nibabel as nib
    img = nib.load(nifti_path)
    ct_data = img.get_fdata(dtype=np.float32)
    return ct_data, img.affine


# ── Coil Segmentation from CT ─────────────────────────────────────────────────

def segment_coil_from_ct(ct_data: np.ndarray, hu_threshold: float = 400.0,
                          min_voxels: int = 100) -> np.ndarray:
    """
    Segment the coil structure from CT by thresholding and morphological cleanup.

    Metal coil housing: HU > 400 (often 1000–3000).
    Steps:
      1. Threshold at hu_threshold
      2. Erode once (removes CT streak/ringing artifacts)
      3. Dilate twice (restores coil body, fills small gaps)
      4. Remove connected components smaller than min_voxels

    Parameters
    ----------
    ct_data : ndarray
        CT volume in Hounsfield Units.
    hu_threshold : float
        Minimum HU to classify as coil metal. Default 400.
    min_voxels : int
        Minimum size of a valid coil component. Smaller blobs are discarded.

    Returns
    -------
    coil_mask : bool ndarray, same shape as ct_data
    """
    raw_mask = ct_data > hu_threshold

    struct = np.ones((3, 3, 3), dtype=bool)
    cleaned = binary_erosion(raw_mask, structure=struct, iterations=1)
    cleaned = binary_dilation(cleaned, structure=struct, iterations=2)

    labeled, n_components = ndlabel(cleaned)
    coil_mask = np.zeros(ct_data.shape, dtype=bool)
    for comp_idx in range(1, n_components + 1):
        comp = labeled == comp_idx
        if comp.sum() >= min_voxels:
            coil_mask |= comp

    return coil_mask


# ── Fiducial Registration (Kabsch Algorithm) ──────────────────────────────────

def register_fiducials(ct_fiducials: np.ndarray, mri_fiducials: np.ndarray):
    """
    Compute optimal rigid transform (R, t) mapping ct_fiducials → mri_fiducials.

    Uses the Kabsch algorithm (SVD-based closed-form solution).
    Requires N >= 3 non-coplanar fiducial pairs.

    Parameters
    ----------
    ct_fiducials  : ndarray, shape (N, 3) — fiducial world coords in CT space (mm)
    mri_fiducials : ndarray, shape (N, 3) — corresponding coords in MRI space (mm)

    Returns
    -------
    R    : ndarray (3, 3) — rotation matrix (det = +1)
    t    : ndarray (3,)   — translation vector
    rmse : float          — registration error in mm
    """
    ct_pts = np.asarray(ct_fiducials, dtype=np.float64)
    mri_pts = np.asarray(mri_fiducials, dtype=np.float64)

    if ct_pts.shape != mri_pts.shape or ct_pts.ndim != 2 or ct_pts.shape[1] != 3:
        raise ValueError("Fiducials must both be arrays of shape (N, 3)")
    if ct_pts.shape[0] < 3:
        raise ValueError("At least 3 fiducial pairs are required")

    # Check for coplanarity
    centered = ct_pts - ct_pts.mean(axis=0)
    if np.linalg.matrix_rank(centered) < 3:
        raise ValueError(
            "CT fiducials are coplanar — add a non-planar 4th point for a unique 3D solution"
        )

    # Step 1: centre both clouds
    ct_centroid = ct_pts.mean(axis=0)
    mri_centroid = mri_pts.mean(axis=0)
    P = ct_pts - ct_centroid   # shape (N, 3)
    Q = mri_pts - mri_centroid

    # Step 2: cross-covariance matrix
    H = P.T @ Q   # (3, 3)

    # Step 3: SVD
    U, S, Vt = np.linalg.svd(H)

    # Step 4: reflection guard — ensure det(R) = +1
    d = np.linalg.det(Vt.T @ U.T)
    D = np.diag([1.0, 1.0, d])

    # Step 5: optimal rotation
    R = Vt.T @ D @ U.T

    # Step 6: translation
    t = mri_centroid - R @ ct_centroid

    # Step 7: RMSE
    transformed = apply_rigid_transform(ct_pts, R, t)
    rmse = float(np.sqrt(((transformed - mri_pts) ** 2).sum(axis=1).mean()))

    return R, t, rmse


def apply_rigid_transform(points: np.ndarray, R: np.ndarray, t: np.ndarray) -> np.ndarray:
    """
    Apply rigid transform to an (N, 3) array of points.
    Also accepts a single point of shape (3,).
    """
    pts = np.asarray(points, dtype=np.float64)
    single = pts.ndim == 1
    if single:
        pts = pts[np.newaxis, :]
    result = (R @ pts.T).T + t
    return result[0] if single else result


# ── Mask Transformation into MRI Space ───────────────────────────────────────

def transform_mask_to_mri_space(coil_mask_ct: np.ndarray, ct_affine: np.ndarray,
                                 mri_affine: np.ndarray, mri_shape: tuple,
                                 R: np.ndarray, t: np.ndarray) -> np.ndarray:
    """
    Map a binary coil mask from CT voxel space into MRI voxel space.

    Steps:
      1. Get CT voxel coords of all coil voxels
      2. Convert to CT world coords (mm) via ct_affine
      3. Apply rigid transform R, t → MRI world coords
      4. Convert to MRI voxel indices via inverse of mri_affine
      5. Round, clip, and build the output boolean mask
      6. One binary_dilation pass fills nearest-neighbor gaps from voxel rounding

    Parameters
    ----------
    coil_mask_ct : bool ndarray — coil segmentation in CT voxel space
    ct_affine    : (4, 4) ndarray — CT voxel-to-world affine
    mri_affine   : (4, 4) ndarray — MRI voxel-to-world affine
    mri_shape    : tuple — shape of the target MRI volume
    R, t         : rotation and translation from register_fiducials()

    Returns
    -------
    coil_mask_mri : bool ndarray of shape mri_shape
    """
    # CT voxel coords of coil
    ct_vox = np.argwhere(coil_mask_ct).astype(np.float64)  # (N, 3)
    if ct_vox.size == 0:
        return np.zeros(mri_shape, dtype=bool)

    # CT voxel → CT world (mm)
    ct_vox_h = np.hstack([ct_vox, np.ones((len(ct_vox), 1))])   # homogeneous
    ct_world = (ct_affine @ ct_vox_h.T).T[:, :3]

    # CT world → MRI world (rigid transform)
    mri_world = apply_rigid_transform(ct_world, R, t)

    # MRI world → MRI voxel
    mri_affine_inv = np.linalg.inv(mri_affine)
    mri_world_h = np.hstack([mri_world, np.ones((len(mri_world), 1))])
    mri_vox = (mri_affine_inv @ mri_world_h.T).T[:, :3]

    # Round and clip to valid voxel range
    mri_vox_int = np.round(mri_vox).astype(int)
    for dim, size in enumerate(mri_shape):
        mri_vox_int[:, dim] = np.clip(mri_vox_int[:, dim], 0, size - 1)

    # Build mask
    coil_mask_mri = np.zeros(mri_shape, dtype=bool)
    coil_mask_mri[mri_vox_int[:, 0], mri_vox_int[:, 1], mri_vox_int[:, 2]] = True

    # Fill rounding gaps with one dilation pass
    coil_mask_mri = binary_dilation(coil_mask_mri, iterations=1)

    return coil_mask_mri


# ── Per-Channel ROI Extraction ────────────────────────────────────────────────

def extract_channel_rois(coil_mask_mri: np.ndarray, brain_mask: np.ndarray,
                          n_expected: int = 8, dilation_iters: int = 4) -> dict:
    """
    Segment individual coil loops from the registered coil mask and compute
    the brain ROI each loop "sees" (its sensitivity region).

    Steps:
      1. Label connected components in coil_mask_mri
      2. Dilate each component toward the brain
      3. Intersect with brain_mask → per-channel brain ROI

    Parameters
    ----------
    coil_mask_mri : bool ndarray — coil in MRI voxel space
    brain_mask    : bool ndarray — brain tissue mask (same shape)
    n_expected    : int — expected number of coil elements (for warnings)
    dilation_iters: int — how far to dilate each coil element into the brain

    Returns
    -------
    rois : dict mapping component_label (int) → brain ROI (bool ndarray)
    """
    labeled, n_components = ndlabel(coil_mask_mri)
    if n_components != n_expected:
        print(
            f"  Warning: found {n_components} coil components, expected {n_expected}. "
            "Adjust hu_threshold or min_voxels in segment_coil_from_ct()."
        )

    rois = {}
    for comp_idx in range(1, n_components + 1):
        comp_mask = labeled == comp_idx
        dilated = binary_dilation(comp_mask, iterations=dilation_iters)
        roi = dilated & brain_mask
        rois[comp_idx] = roi

    return rois


# ── Overlay Visualization ─────────────────────────────────────────────────────

def visualize_coil_overlay(mri_data: np.ndarray, coil_mask_mri: np.ndarray,
                            output_dir: str, axial_fraction: float = 0.5,
                            channel_rois: dict = None,
                            brain_mask: np.ndarray = None) -> None:
    """
    Save three overlay PNGs to output_dir:
      - coil_overlay_axial.png    : single axial slice, MRI + coil contour
      - coil_overlay_multiview.png: axial + coronal + sagittal views
      - coil_3d_scatter.png       : 3D scatter of coil voxels + brain surface

    Parameters
    ----------
    mri_data      : 3D float ndarray — MRI volume
    coil_mask_mri : 3D bool ndarray — coil mask in MRI space (same shape)
    output_dir    : str — directory to write PNGs
    axial_fraction: float — which axial slice (0=bottom, 1=top)
    channel_rois  : optional dict from extract_channel_rois(), for coloured channels
    brain_mask    : optional 3D bool ndarray — precomputed brain mask for 3D scatter;
                    if None, a simple intensity threshold is used
    """
    os.makedirs(output_dir, exist_ok=True)
    shape = mri_data.shape
    ax_idx = int(shape[2] * axial_fraction)
    co_idx = int(shape[1] * 0.5)
    sa_idx = int(shape[0] * 0.5)

    vmax = np.percentile(mri_data[mri_data > 0], 99) if (mri_data > 0).any() else 1.0

    # ── Figure 1: Single axial slice ──────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.imshow(mri_data[:, :, ax_idx].T, cmap="gray", origin="lower",
               aspect="equal", vmin=0, vmax=vmax)
    coil_slice = coil_mask_mri[:, :, ax_idx].astype(float).T
    if coil_slice.any():
        ax.contour(coil_slice, levels=[0.5], colors="red", linewidths=1.5)
    ax.set_title(f"Coil Overlay — Axial slice {ax_idx}/{shape[2]}")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, "coil_overlay_axial.png"), dpi=150)
    plt.close(fig)

    # ── Figure 2: Three-view ──────────────────────────────────────────────────
    views = [
        ("Axial",    mri_data[:, :, ax_idx].T,  coil_mask_mri[:, :, ax_idx].T),
        ("Coronal",  mri_data[:, co_idx, :].T,  coil_mask_mri[:, co_idx, :].T),
        ("Sagittal", mri_data[sa_idx, :, :].T,  coil_mask_mri[sa_idx, :, :].T),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax_obj, (title, mri_sl, coil_sl) in zip(axes, views):
        ax_obj.imshow(mri_sl, cmap="gray", origin="lower", aspect="equal",
                       vmin=0, vmax=vmax)
        coil_float = coil_sl.astype(float)
        if coil_float.any():
            ax_obj.contour(coil_float, levels=[0.5], colors="red", linewidths=1.5)
        ax_obj.set_title(title)
        ax_obj.axis("off")
    plt.suptitle("Coil Overlay — Three Views", fontsize=13)
    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, "coil_overlay_multiview.png"), dpi=150)
    plt.close(fig)

    # ── Figure 3: 3D scatter ──────────────────────────────────────────────────
    fig = plt.figure(figsize=(10, 8))
    ax3d = fig.add_subplot(111, projection="3d")

    # Brain surface approximation (3-voxel-thick outer shell for visibility)
    if brain_mask is None:
        brain_mask = mri_data > (vmax * 0.1)
    surface = brain_mask & ~binary_erosion(brain_mask, iterations=3)
    surf_pts = np.argwhere(surface)
    if surf_pts.size > 0:
        step = max(1, len(surf_pts) // 15000)
        surf_pts = surf_pts[::step]
        ax3d.scatter(surf_pts[:, 0], surf_pts[:, 1], surf_pts[:, 2],
                     c="silver", s=4, alpha=0.35, zorder=1)

    # Coil voxels
    coil_pts = np.argwhere(coil_mask_mri)
    if coil_pts.size > 0:
        if channel_rois is not None:
            # Colour by channel
            cmap = plt.get_cmap("tab10")
            labeled_coil, _ = ndlabel(coil_mask_mri)
            colors_arr = np.array([cmap(labeled_coil[tuple(pt)] % 10) for pt in coil_pts])
            ax3d.scatter(coil_pts[:, 0], coil_pts[:, 1], coil_pts[:, 2],
                         c=colors_arr, s=5, alpha=0.9, label="Coil channels", zorder=2)
        else:
            ax3d.scatter(coil_pts[:, 0], coil_pts[:, 1], coil_pts[:, 2],
                         c="red", s=5, alpha=0.9, label="Coil", zorder=2)

    ax3d.set_xlabel("X")
    ax3d.set_ylabel("Y")
    ax3d.set_zlabel("Z")
    ax3d.set_title("3D Coil Position relative to Brain")
    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, "coil_3d_scatter.png"), dpi=150)
    plt.close(fig)


# ── Fiducial CSV I/O ──────────────────────────────────────────────────────────

def load_fiducials_from_csv(csv_path: str):
    """
    Load fiducial pairs from a CSV file.

    Expected columns: label, ct_x, ct_y, ct_z, mri_x, mri_y, mri_z
    (label column is ignored; any extra columns are ignored)

    Returns (ct_fiducials, mri_fiducials) each of shape (N, 3).
    """
    ct_pts, mri_pts = [], []
    with open(csv_path, "r") as f:
        lines = f.readlines()
    for line in lines[1:]:  # skip header
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) < 7:
            raise ValueError(f"Expected 7 columns per row, got: {line!r}")
        ct_pts.append([float(parts[1]), float(parts[2]), float(parts[3])])
        mri_pts.append([float(parts[4]), float(parts[5]), float(parts[6])])
    return np.array(ct_pts), np.array(mri_pts)


# ── Synthetic CT Phantom ──────────────────────────────────────────────────────

def simulate_ct_phantom(shape=(80, 80, 80), n_coil_elements=8, seed=42):
    """
    Generate a synthetic CT volume for testing the registration pipeline.

    Structure:
    - Background: HU = -800 (air)
    - Central ellipsoid: HU ~ 40 (soft tissue / head)
    - 8 thin metal strips arranged in a ring around the ellipsoid: HU ~ 1500 (coil)
    - Gaussian noise (std=20 HU) throughout
    - Fiducial markers: small spheres (HU = 1200) placed between coil elements

    Returns
    -------
    ct_data   : float32 ndarray of shape `shape`
    fiducials : (N, 3) float64 array of fiducial world coords (identity affine,
                so world coords == voxel coords in mm)
    """
    rng = np.random.default_rng(seed)
    ct_data = np.full(shape, -800.0, dtype=np.float32)

    cx, cy, cz = shape[0] // 2, shape[1] // 2, shape[2] // 2
    rx, ry, rz = shape[0] * 0.35, shape[1] * 0.35, shape[2] * 0.40

    x = np.arange(shape[0])
    y = np.arange(shape[1])
    z = np.arange(shape[2])
    X, Y, Z = np.meshgrid(x, y, z, indexing="ij")

    head_mask = (
        ((X - cx) / rx) ** 2 + ((Y - cy) / ry) ** 2 + ((Z - cz) / rz) ** 2
    ) <= 1.0
    ct_data[head_mask] = 40.0

    # Place coil elements: thin rectangles around the ellipsoid rim
    fiducials = []
    for i in range(n_coil_elements):
        angle = 2 * np.pi * i / n_coil_elements
        # Centre of this coil element (slightly outside head ellipsoid)
        ex = int(cx + (rx + 4) * np.cos(angle))
        ey = int(cy + (ry + 4) * np.sin(angle))
        ez_start = cz - int(rz * 0.6)
        ez_end = cz + int(rz * 0.6)

        ex = np.clip(ex, 2, shape[0] - 3)
        ey = np.clip(ey, 2, shape[1] - 3)
        ez_start = max(2, ez_start)
        ez_end = min(shape[2] - 3, ez_end)

        ct_data[ex-1:ex+2, ey-1:ey+2, ez_start:ez_end] = 1500.0

        # Fiducial marker: stagger Z position so fiducials are NOT coplanar.
        # Alternating elements are placed at different heights along the coil.
        z_offset = int(rz * 0.3 * (1 if i % 2 == 0 else -1))
        fid_z = np.clip(cz + z_offset, ez_start + 1, ez_end - 1)
        ct_data[ex, ey, fid_z-1:fid_z+2] = 1200.0
        fiducials.append([float(ex), float(ey), float(fid_z)])

    # Add noise
    ct_data += rng.normal(0, 20, size=shape).astype(np.float32)

    return ct_data, np.array(fiducials, dtype=np.float64)
