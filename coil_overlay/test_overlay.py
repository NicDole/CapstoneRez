"""
Tests for the CT-to-MRI coil overlay pipeline.
All tests use simulate_ct_phantom() — no real CT or MRI data required.
"""

import sys
import os
import tempfile
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coil_overlay.ct_mri_registration import (
    simulate_ct_phantom,
    segment_coil_from_ct,
    register_fiducials,
    apply_rigid_transform,
    transform_mask_to_mri_space,
    extract_channel_rois,
    visualize_coil_overlay,
    load_fiducials_from_csv,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def identity_affine(voxel_size_mm=1.0):
    """Return a simple isotropic identity-like affine."""
    a = np.eye(4, dtype=np.float64)
    a[0, 0] = a[1, 1] = a[2, 2] = voxel_size_mm
    return a


def rotation_matrix_z(angle_deg: float) -> np.ndarray:
    """Pure rotation around Z axis."""
    theta = np.radians(angle_deg)
    return np.array([
        [np.cos(theta), -np.sin(theta), 0],
        [np.sin(theta),  np.cos(theta), 0],
        [0,              0,             1],
    ])


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_simulate_ct_phantom():
    """Phantom should have the right shape and HU ranges."""
    ct_data, fiducials = simulate_ct_phantom(shape=(64, 64, 64), n_coil_elements=8)
    assert ct_data.shape == (64, 64, 64)
    assert ct_data.max() > 1000, "Coil metal (>1000 HU) should be present"
    assert ct_data.min() < -700, "Air (-800 HU) should be present"
    assert fiducials.shape == (8, 3), "Should have 8 fiducials"
    # All fiducials should be within the volume bounds
    assert (fiducials >= 0).all()
    assert (fiducials[:, 0] < 64).all()
    assert (fiducials[:, 1] < 64).all()
    assert (fiducials[:, 2] < 64).all()


def test_coil_segmentation():
    """Segmented mask should find metal and exclude air."""
    ct_data, _ = simulate_ct_phantom(shape=(64, 64, 64))
    mask = segment_coil_from_ct(ct_data, hu_threshold=400.0, min_voxels=5)

    assert mask.dtype == bool
    assert mask.shape == ct_data.shape
    assert mask.any(), "Should find at least some coil voxels"

    # Air region (top-left corner, far from head) should not be segmented
    air_region = ct_data[:5, :5, :5]
    assert air_region.max() < 400, "Air corner used for test is actually high HU — adjust test"
    assert not mask[:5, :5, :5].any(), "Air region should not be segmented as coil"


def test_kabsch_identity():
    """Identical point sets → R = identity, t = zero, RMSE ≈ 0."""
    rng = np.random.default_rng(0)
    pts = rng.uniform(0, 100, size=(6, 3))
    R, t, rmse = register_fiducials(pts, pts)

    assert np.allclose(R, np.eye(3), atol=1e-8), f"R not identity:\n{R}"
    assert np.allclose(t, np.zeros(3), atol=1e-8), f"t not zero: {t}"
    assert rmse < 1e-8, f"RMSE not zero: {rmse}"


def test_kabsch_pure_translation():
    """Known translation offset should be recovered exactly."""
    rng = np.random.default_rng(1)
    ct_pts = rng.uniform(0, 50, size=(6, 3))
    offset = np.array([10.0, -5.0, 20.0])
    mri_pts = ct_pts + offset

    R, t, rmse = register_fiducials(ct_pts, mri_pts)

    assert np.allclose(R, np.eye(3), atol=1e-6), f"R should be identity for pure translation:\n{R}"
    assert np.allclose(t, offset, atol=1e-6), f"Recovered t={t}, expected {offset}"
    assert rmse < 1e-6


def test_kabsch_pure_rotation():
    """Known 45° rotation around Z should be recovered exactly."""
    rng = np.random.default_rng(2)
    ct_pts = rng.uniform(-30, 30, size=(6, 3))
    # Ensure non-coplanar by adding variety in Z
    ct_pts[:, 2] = rng.uniform(-20, 20, size=6)

    R_true = rotation_matrix_z(45.0)
    mri_pts = (R_true @ ct_pts.T).T

    R, t, rmse = register_fiducials(ct_pts, mri_pts)

    assert np.allclose(R, R_true, atol=1e-6), (
        f"Recovered rotation:\n{R}\nExpected:\n{R_true}"
    )
    assert np.allclose(t, np.zeros(3), atol=1e-6), f"Translation should be zero: {t}"
    assert rmse < 1e-6


def test_kabsch_rotation_and_translation():
    """Combined rotation + translation should both be recovered."""
    rng = np.random.default_rng(3)
    ct_pts = rng.uniform(-20, 20, size=(7, 3))
    ct_pts[:, 2] = rng.uniform(-15, 15, size=7)

    R_true = rotation_matrix_z(30.0)
    t_true = np.array([5.0, -10.0, 3.0])
    mri_pts = (R_true @ ct_pts.T).T + t_true

    R, t, rmse = register_fiducials(ct_pts, mri_pts)

    assert np.allclose(R, R_true, atol=1e-6), f"Rotation mismatch:\n{R}\nvs\n{R_true}"
    assert np.allclose(t, t_true, atol=1e-6), f"Translation mismatch: {t} vs {t_true}"
    assert rmse < 1e-6


def test_kabsch_reflection_guard():
    """Recovered rotation matrix must always have det = +1 (no reflections)."""
    rng = np.random.default_rng(4)
    # Test across many random configurations including degenerate-looking ones
    for seed in range(20):
        rng2 = np.random.default_rng(seed * 7)
        ct_pts = rng2.uniform(-50, 50, size=(5, 3))
        ct_pts[:, 2] = rng2.uniform(-30, 30, size=5)  # ensure non-coplanar
        mri_pts = rng2.uniform(-50, 50, size=(5, 3))
        mri_pts[:, 2] = rng2.uniform(-30, 30, size=5)

        R, t, rmse = register_fiducials(ct_pts, mri_pts)
        det = np.linalg.det(R)
        assert abs(det - 1.0) < 1e-8, (
            f"det(R)={det:.6f} for seed={seed} — reflection not corrected"
        )


def test_full_pipeline_synthetic():
    """
    End-to-end: phantom → segment → register → transform → visualize.
    Checks no exceptions and output PNGs are created.
    """
    ct_data, ct_fiducials = simulate_ct_phantom(shape=(64, 64, 64), n_coil_elements=8)
    ct_affine = identity_affine(voxel_size_mm=1.0)

    # Segment coil
    coil_mask_ct = segment_coil_from_ct(ct_data, hu_threshold=400.0, min_voxels=5)
    assert coil_mask_ct.any(), "Segmentation returned empty mask"

    # For the synthetic test: MRI has the same geometry as CT (identity registration)
    # Apply a small known transform so registration has something to do
    R_true = rotation_matrix_z(10.0)
    t_true = np.array([2.0, -1.0, 0.5])
    mri_fiducials = apply_rigid_transform(ct_fiducials, R_true, t_true)

    R, t, rmse = register_fiducials(ct_fiducials, mri_fiducials)
    assert rmse < 1.0, f"Registration RMSE too high: {rmse:.3f} mm"

    # Transform mask
    mri_shape = (64, 64, 64)
    mri_affine = identity_affine(voxel_size_mm=1.0)
    coil_mask_mri = transform_mask_to_mri_space(
        coil_mask_ct, ct_affine, mri_affine, mri_shape, R, t
    )
    assert coil_mask_mri.shape == mri_shape
    assert coil_mask_mri.any(), "Transformed coil mask is empty"

    # Fake MRI volume (Gaussian signal in ellipsoid)
    rng = np.random.default_rng(0)
    mri_data = np.zeros(mri_shape, dtype=np.float32)
    X, Y, Z = np.meshgrid(
        np.arange(mri_shape[0]), np.arange(mri_shape[1]), np.arange(mri_shape[2]),
        indexing="ij"
    )
    cx, cy, cz = [s // 2 for s in mri_shape]
    head = ((X - cx) / (cx * 0.7))**2 + ((Y - cy) / (cy * 0.7))**2 + ((Z - cz) / (cz * 0.75))**2 <= 1
    mri_data[head] = rng.normal(500, 40, size=head.sum()).clip(1).astype(np.float32)
    mri_data[~head] = rng.normal(8, 3, size=(~head).sum()).clip(0.1).astype(np.float32)

    # Extract ROIs
    brain_mask = mri_data > 100
    rois = extract_channel_rois(coil_mask_mri, brain_mask, n_expected=8)
    assert isinstance(rois, dict)

    # Visualize
    with tempfile.TemporaryDirectory() as tmpdir:
        visualize_coil_overlay(mri_data, coil_mask_mri, tmpdir)
        for fname in ["coil_overlay_axial.png", "coil_overlay_multiview.png", "coil_3d_scatter.png"]:
            path = os.path.join(tmpdir, fname)
            assert os.path.exists(path), f"Missing output: {fname}"
            assert os.path.getsize(path) > 1000, f"Output file suspiciously small: {fname}"


def test_load_fiducials_from_csv():
    """CSV loading should parse coordinates correctly."""
    csv_content = (
        "label,ct_x,ct_y,ct_z,mri_x,mri_y,mri_z\n"
        "F1,10.0,20.0,30.0,11.0,21.0,31.0\n"
        "F2,40.0,50.0,60.0,41.5,51.5,61.5\n"
        "F3,70.0,80.0,90.0,71.0,81.0,91.0\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        csv_path = f.name
    try:
        ct_pts, mri_pts = load_fiducials_from_csv(csv_path)
        assert ct_pts.shape == (3, 3)
        assert mri_pts.shape == (3, 3)
        assert np.allclose(ct_pts[0], [10.0, 20.0, 30.0])
        assert np.allclose(mri_pts[1], [41.5, 51.5, 61.5])
    finally:
        os.unlink(csv_path)
