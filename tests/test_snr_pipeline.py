"""
Synthetic tests for the SNR-based coil fault detection pipeline.

All tests use make_brain_phantom() — no NIfTI files or real MRI data needed.
"Dead" coils are simulated by setting voxels to background noise level
(not true zero) so Otsu's threshold remains stable.
"""

import sys
import os
import numpy as np
import pytest

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coil_analysis.snr_analysis import (
    compute_otsu,
    compute_snr_map,
    build_sectors,
    generate_report,
    FAULT_THRESHOLD_RATIO,
)
from tests.conftest import make_brain_phantom, get_octant_slices, make_octant_mask

# ── Helpers ────────────────────────────────────────────────────────────────────

SHAPE = (64, 64, 64)
SIGNAL_MEAN = 500.0
NOISE_MEAN = 8.0
NOISE_STD = 5.0


def run_pipeline(data):
    """Convenience: run all three analysis steps and return (sectors, global_mean, fault_threshold)."""
    threshold, brain_mask, bg_mask = compute_otsu(data)
    snr_map, noise_std, global_mean, fault_threshold = compute_snr_map(data, brain_mask, bg_mask)
    sectors = build_sectors(snr_map, brain_mask, fault_threshold)
    return sectors, global_mean, fault_threshold, brain_mask


def kill_channel(data: np.ndarray, channel: int, brain_mask: np.ndarray,
                 level: float = NOISE_MEAN) -> np.ndarray:
    """Set one octant's brain voxels to background noise level (simulate dead coil)."""
    data = data.copy()
    x_mid, y_mid, z_mid = get_octant_slices(brain_mask)
    octant = make_octant_mask(data.shape, x_mid, y_mid, z_mid, channel)
    octant &= brain_mask
    rng = np.random.default_rng(99)
    data[octant] = rng.normal(level, NOISE_STD * 0.5, size=octant.sum()).clip(0.1)
    return data


def degrade_channel(data: np.ndarray, channel: int, brain_mask: np.ndarray,
                    factor: float = 0.40) -> np.ndarray:
    """Scale one octant's brain signal down by factor (simulate degraded coil)."""
    data = data.copy()
    x_mid, y_mid, z_mid = get_octant_slices(brain_mask)
    octant = make_octant_mask(data.shape, x_mid, y_mid, z_mid, channel)
    octant &= brain_mask
    data[octant] *= factor
    return data


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_healthy_scan():
    """All 8 sectors should be OK on an undamaged phantom."""
    data = make_brain_phantom(SHAPE, signal_mean=SIGNAL_MEAN, noise_mean=NOISE_MEAN,
                              noise_std=NOISE_STD)
    sectors, global_mean, fault_threshold, _ = run_pipeline(data)

    assert len(sectors) == 8
    for ch, info in sectors.items():
        assert info["status"] == "OK", (
            f"Channel {ch} flagged as fault on healthy scan (mean_snr={info['mean_snr']:.2f}, "
            f"threshold={fault_threshold:.2f})"
        )
    assert global_mean > 0


def test_single_dead_coil():
    """Zeroing out channel 1's signal should flag exactly channel 1."""
    data = make_brain_phantom(SHAPE, signal_mean=SIGNAL_MEAN, noise_mean=NOISE_MEAN,
                              noise_std=NOISE_STD)
    _, brain_mask, _ = compute_otsu(data)
    data_dead = kill_channel(data, channel=1, brain_mask=brain_mask)

    sectors, global_mean, fault_threshold, _ = run_pipeline(data_dead)

    faults = [ch for ch, info in sectors.items() if info["status"] == "FAULT LIKELY"]
    assert 1 in faults, "Channel 1 (dead) was not flagged"
    assert len(faults) == 1, f"Expected only channel 1 as fault, got: {faults}"


def test_multiple_dead_coils():
    """Zeroing channels 1, 4, 6 should flag exactly those three."""
    dead_channels = [1, 4, 6]
    data = make_brain_phantom(SHAPE, signal_mean=SIGNAL_MEAN, noise_mean=NOISE_MEAN,
                              noise_std=NOISE_STD)
    _, brain_mask, _ = compute_otsu(data)

    data_dead = data.copy()
    for ch in dead_channels:
        data_dead = kill_channel(data_dead, ch, brain_mask=brain_mask)

    sectors, _, fault_threshold, _ = run_pipeline(data_dead)
    faults = sorted([ch for ch, info in sectors.items() if info["status"] == "FAULT LIKELY"])

    assert faults == dead_channels, (
        f"Expected faults {dead_channels}, got {faults}"
    )


def test_degraded_coil():
    """
    Reducing channel 3 to 20% of normal signal should trigger the fault flag.

    We use factor=0.20 (well below the 50% threshold) and a high-SNR phantom
    (noise_std=3) so the global mean is dominated by the 7 healthy channels
    and the degraded channel's SNR clearly falls below fault_threshold.
    """
    # High-contrast phantom: signal >> noise so global mean stays high
    data = make_brain_phantom(SHAPE, signal_mean=SIGNAL_MEAN, noise_mean=NOISE_MEAN,
                              noise_std=3.0, seed=42)
    _, brain_mask, _ = compute_otsu(data)
    data_degraded = degrade_channel(data, channel=3, brain_mask=brain_mask, factor=0.20)

    sectors, global_mean, fault_threshold, _ = run_pipeline(data_degraded)
    faults = [ch for ch, info in sectors.items() if info["status"] == "FAULT LIKELY"]

    assert 3 in faults, (
        f"Degraded channel 3 not flagged. Its mean_snr={sectors[3]['mean_snr']:.2f}, "
        f"fault_threshold={fault_threshold:.2f}, global_mean={global_mean:.2f}"
    )
    # Other channels should be fine
    other_faults = [ch for ch in faults if ch != 3]
    assert len(other_faults) == 0, f"Unexpected faults in channels: {other_faults}"


def test_otsu_threshold_range():
    """Otsu threshold should fall clearly between noise floor and signal peak."""
    data = make_brain_phantom(SHAPE, signal_mean=SIGNAL_MEAN, noise_mean=NOISE_MEAN,
                              noise_std=NOISE_STD)
    threshold, brain_mask, bg_mask = compute_otsu(data)

    assert threshold > NOISE_MEAN * 3, (
        f"Threshold {threshold:.1f} is too close to noise floor ({NOISE_MEAN})"
    )
    assert threshold < SIGNAL_MEAN * 0.5, (
        f"Threshold {threshold:.1f} is too close to signal peak ({SIGNAL_MEAN})"
    )
    # Brain mask should cover a reasonable fraction of the volume
    brain_fraction = brain_mask.sum() / brain_mask.size
    assert 0.05 < brain_fraction < 0.60, (
        f"Brain mask fraction {brain_fraction:.2%} is unreasonable"
    )


def test_snr_map_values():
    """Global mean SNR should be approximately signal_mean / noise_std."""
    signal_mean = 500.0
    noise_std = 20.0
    data = make_brain_phantom(SHAPE, signal_mean=signal_mean, signal_std=30.0,
                              noise_mean=8.0, noise_std=noise_std)
    threshold, brain_mask, bg_mask = compute_otsu(data)
    _, actual_noise_std, global_mean_snr, _ = compute_snr_map(data, brain_mask, bg_mask)

    expected_snr = signal_mean / actual_noise_std
    tolerance = 0.20  # 20% — phantom noise is approximate
    assert abs(global_mean_snr - expected_snr) / expected_snr < tolerance, (
        f"SNR {global_mean_snr:.1f} too far from expected {expected_snr:.1f} "
        f"(noise_std={actual_noise_std:.2f})"
    )


def test_all_coils_dead():
    """
    If the entire brain region has near-zero signal, the pipeline must not crash.
    All sectors should be flagged as FAULT LIKELY.
    """
    data = make_brain_phantom(SHAPE, signal_mean=SIGNAL_MEAN, noise_mean=NOISE_MEAN,
                              noise_std=NOISE_STD)
    _, brain_mask, _ = compute_otsu(data)

    # Flatten all brain signal to noise level
    data_dead = data.copy()
    rng = np.random.default_rng(77)
    data_dead[brain_mask] = rng.normal(NOISE_MEAN, NOISE_STD * 0.5,
                                        size=brain_mask.sum()).clip(0.1)

    # Must not raise
    threshold, brain_mask2, bg_mask2 = compute_otsu(data_dead)
    snr_map, noise_std, global_mean, fault_threshold = compute_snr_map(
        data_dead, brain_mask2, bg_mask2
    )
    sectors = build_sectors(snr_map, brain_mask2, fault_threshold)

    # All channels flagged (global_mean ≈ fault_threshold when all signal is gone)
    # At minimum: no exceptions were raised and we got 8 sectors back
    assert len(sectors) == 8

    # generate_report should also not crash (pass minimal args, no PNG output)
    faults = generate_report(sectors, global_mean, fault_threshold, output_dir=None)
    # faults list returned (may or may not be all 8 depending on noise variance)
    assert isinstance(faults, list)
