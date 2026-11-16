"""
Shared test fixtures and utilities for the MRI coil fault detection test suite.
"""

import numpy as np


def make_brain_phantom(
    shape=(64, 64, 64),
    signal_mean=500.0,
    signal_std=40.0,
    noise_mean=8.0,
    noise_std=5.0,
    seed=42,
) -> np.ndarray:
    """
    Generate a synthetic 3D MRI phantom for testing.

    Structure:
    - 1-voxel border of true zeros (simulates NIfTI air boundary)
    - Background ring: low-signal Gaussian noise (simulates air/background)
    - Ellipsoid brain region: high-signal Gaussian noise

    The bimodal distribution allows Otsu's method to find a clean threshold.

    Parameters
    ----------
    shape : tuple of int
        Volume dimensions (x, y, z).
    signal_mean : float
        Mean intensity of brain voxels.
    signal_std : float
        Std of brain voxel intensities.
    noise_mean : float
        Mean intensity of background voxels (must be << signal_mean).
    noise_std : float
        Std of background voxel intensities.
    seed : int
        Random seed for reproducibility.
    """
    rng = np.random.default_rng(seed)
    data = np.zeros(shape, dtype=np.float64)

    cx, cy, cz = shape[0] // 2, shape[1] // 2, shape[2] // 2
    rx, ry, rz = shape[0] * 0.38, shape[1] * 0.43, shape[2] * 0.46

    # Build coordinate grids
    x = np.arange(shape[0])
    y = np.arange(shape[1])
    z = np.arange(shape[2])
    X, Y, Z = np.meshgrid(x, y, z, indexing="ij")

    ellipsoid_mask = (
        ((X - cx) / rx) ** 2 + ((Y - cy) / ry) ** 2 + ((Z - cz) / rz) ** 2
    ) <= 1.0

    # Background (non-ellipsoid, non-border): positive low signal
    border = np.zeros(shape, dtype=bool)
    border[0, :, :] = border[-1, :, :] = True
    border[:, 0, :] = border[:, -1, :] = True
    border[:, :, 0] = border[:, :, -1] = True
    background_mask = ~ellipsoid_mask & ~border

    brain_signal = rng.normal(signal_mean, signal_std, size=shape)
    bg_signal = rng.normal(noise_mean, noise_std, size=shape)

    data[ellipsoid_mask] = np.clip(brain_signal[ellipsoid_mask], 1.0, None)
    data[background_mask] = np.clip(bg_signal[background_mask], 0.1, None)
    # border stays at 0.0

    return data


def get_octant_slices(brain_mask: np.ndarray):
    """
    Return (x_mid, y_mid, z_mid) midpoints of the brain bounding box.
    Matches the logic in snr_analysis.build_sectors().
    """
    import numpy as np
    xs, ys, zs = np.where(brain_mask)
    x_mid = (xs.min() + xs.max()) // 2
    y_mid = (ys.min() + ys.max()) // 2
    z_mid = (zs.min() + zs.max()) // 2
    return x_mid, y_mid, z_mid


def make_octant_mask(shape, x_mid, y_mid, z_mid, channel: int) -> np.ndarray:
    """
    Return a boolean mask for one of the 8 octants, matching build_sectors() ordering.
    Channels 1–8 correspond to the nested loop order:
      z: [Superior(z_mid:), Inferior(:z_mid)]
      y: [Anterior(y_mid:), Posterior(:y_mid)]
      x: [Right(x_mid:),   Left(:x_mid)]
    """
    order = []
    for z_sl in [slice(z_mid, None), slice(None, z_mid)]:
        for y_sl in [slice(y_mid, None), slice(None, y_mid)]:
            for x_sl in [slice(x_mid, None), slice(None, x_mid)]:
                order.append((x_sl, y_sl, z_sl))
    x_sl, y_sl, z_sl = order[channel - 1]
    mask = np.zeros(shape, dtype=bool)
    mask[x_sl, y_sl, z_sl] = True
    return mask
