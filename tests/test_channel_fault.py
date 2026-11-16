import numpy as np
import pytest

pytest.importorskip("twixtools")
from coil_analysis.channel_fault_analysis import (
    channel_features, detect, inject_dead_channel, validate, spectrum_tilt)


def make_coil_maps(n_rings=4, per_ring=12, size=40, seed=0):
    """Per-element sensitivity maps for a symmetric ring coil around a spherical head."""
    rng = np.random.default_rng(seed)
    g = np.indices((size, size, size)).astype(float) - (size - 1) / 2
    r_head = size * 0.35
    head = (g ** 2).sum(0) < r_head ** 2
    maps = []
    for k in range(n_rings):
        z = (k - (n_rings - 1) / 2) * size * 0.15
        for j in range(per_ring):
            a = 2 * np.pi * (j + 0.5) / per_ring
            pos = np.array([np.cos(a) * size * 0.45, np.sin(a) * size * 0.45, z])
            d = np.sqrt(((g - pos[:, None, None, None]) ** 2).sum(0))
            gain = 1 + 0.05 * rng.standard_normal()
            sig = gain * 100.0 / (1 + (d / (size * 0.12)) ** 3) * head
            maps.append(np.abs(sig + 2.0 * (rng.standard_normal(sig.shape) + 1j * rng.standard_normal(sig.shape))))
    return np.array(maps, dtype=np.float32)


@pytest.fixture(scope="module")
def maps():
    return make_coil_maps()


def test_mirror_partners_found(maps):
    assert channel_features(maps)["mirror_mutual"] > 0.9


def test_healthy_scan_flags_nothing(maps):
    assert len(detect(channel_features(maps))["flagged"]) == 0


@pytest.mark.parametrize("idx", [0, 7, 30])
def test_disconnected_element_detected(maps, idx):
    det = detect(channel_features(inject_dead_channel(maps, idx, 0.0)))
    assert idx in det["flagged"]
    assert int(np.argmax(det["score"])) == idx


def test_mostly_dead_element_detected(maps):
    det = detect(channel_features(inject_dead_channel(maps, 12, 0.1)))
    assert list(det["flagged"]) == [12]


def test_two_dead_elements_both_detected(maps):
    m = inject_dead_channel(inject_dead_channel(maps, 3, 0.0), 40, 0.0)
    assert set(detect(channel_features(m))["flagged"]) == {3, 40}


def _noise_cov(n_el, scale=None, seed=1):
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal((n_el, 20000)) + 1j * rng.standard_normal((n_el, 20000))
    noise *= 1 + 0.01 * rng.standard_normal((n_el, 1))
    if scale:
        for i, s in scale.items():
            noise[i] *= s
    return noise @ noise.conj().T / noise.shape[1]


def test_noise_covariance_adds_coupling_measure(maps):
    det = detect(channel_features(maps, _noise_cov(len(maps))))
    assert det["measures"] == ["signal", "noise", "snr", "coupling"]
    assert len(det["flagged"]) == 0


def test_abnormal_noise_ranked_first(maps):
    det = detect(channel_features(maps, _noise_cov(len(maps), {5: 1.6})))
    assert det["ranking"][0] == 5
    assert 5 in det["flagged"]
    assert det["reason"][5] in ("abnormal noise level", "low SNR")


def test_validation_confirms_detection(maps):
    ref = channel_features(maps)
    test = channel_features(inject_dead_channel(maps, 9, 0.0))
    v = validate(test, ref, detect(test))
    assert v["confirmed"] == [9]
    assert v["missed"] == []


# ── Tuning indicator (noise-spectrum tilt), two-sided signal, low-coverage rule ──

HW_TILT = 0.03        # hardware: odd ADC channels tilt +, even -
NATURAL = 0.005       # element-to-element tuning scatter


def _noise_psd(n_el, tilt, n_f=256, seed=2):
    """Noise spectra with a common filter shape, a given per-channel tilt and estimation noise."""
    rng = np.random.default_rng(seed)
    f = np.arange(n_f)
    lo, hi = int(0.2 * n_f), int(0.8 * n_f)
    xb = (f - lo) / (hi - 1 - lo) * 2 - 1                 # -1..1 across the analysed band
    base = -0.5 * (np.abs(f - n_f / 2) / (n_f / 2)) ** 6  # filter roll-off at the edges
    log_psd = base[None] + tilt[:, None] * xb[None] + 0.01 * rng.standard_normal((n_el, n_f))
    return np.exp(log_psd)


def _tilts(n_el, adc, seed=3, shift=None):
    rng = np.random.default_rng(seed)
    t = np.where(adc % 2 == 1, HW_TILT, -HW_TILT) + NATURAL * rng.standard_normal(n_el)
    if shift:
        for i, s in shift.items():
            t[i] += s
    return t


def test_spectrum_tilt_recovers_slope():
    true = np.linspace(-0.05, 0.05, 11)
    est = spectrum_tilt(_noise_psd(11, true))
    assert np.allclose(est - np.median(est), true - np.median(true), atol=0.003)


def test_hardware_tilt_pattern_flags_nothing(maps):
    n = len(maps)
    adc = np.arange(n)
    det = detect(channel_features(maps, _noise_cov(n), _noise_psd(n, _tilts(n, adc)), adc))
    assert "tuning" in det["measures"]
    assert len(det["flagged"]) == 0


def test_receiver_parity_correction_needed(maps):
    """Without the ADC channel numbers, the hardware pattern swamps a mistuned element."""
    n = len(maps)
    adc = np.arange(n)
    psd = _noise_psd(n, _tilts(n, adc, shift={20: -0.04}))
    with_adc = detect(channel_features(maps, _noise_cov(n), psd, adc))
    without = detect(channel_features(maps, _noise_cov(n), psd, None))
    assert with_adc["ranking"][0] == 20
    assert without["ranking"][0] != 20


@pytest.mark.parametrize("shift", [-0.06, +0.06])
def test_untuned_element_ranked_first(maps, shift):
    n = len(maps)
    adc = np.arange(n)
    det = detect(channel_features(maps, _noise_cov(n), _noise_psd(n, _tilts(n, adc, shift={20: shift})), adc))
    assert det["ranking"][0] == 20
    assert det["reason"][20] == "abnormal tuning (noise-spectrum tilt)"
    assert 20 in det["flagged"]


def test_brighter_element_ranked_first(maps):
    """Signal is two-sided: a dematched element got brighter in the 10-30 data."""
    m = maps.copy()
    m[17] *= 1.5
    det = detect(channel_features(m, _noise_cov(len(m))))
    assert det["ranking"][0] == 17
    assert det["reason"][17] in ("abnormal signal", "abnormal SNR")


@pytest.fixture(scope="module")
def partly_covered():
    """Coil whose bottom ring barely reaches the object (20% signal), like J01-J08 on the phantom."""
    m = make_coil_maps(n_rings=4, per_ring=24, seed=4)
    m[:24] *= 0.2
    return m


def test_low_coverage_elements_not_ranked(partly_covered):
    n = len(partly_covered)
    adc = np.arange(n)
    # a low-coverage element with an extreme tilt (as J01) must not take a top-3 spot
    psd = _noise_psd(n, _tilts(n, adc, shift={5: 0.08}))
    det = detect(channel_features(partly_covered, _noise_cov(n), psd, adc))
    assert set(det["not_assessable"]) == set(range(24))
    assert 5 not in det["ranking"][:3]
    assert 5 not in det["flagged"]


def test_dead_element_in_low_coverage_region_still_ranked(partly_covered):
    n = len(partly_covered)
    adc = np.arange(n)
    m = inject_dead_channel(partly_covered, 7, 0.0)
    det = detect(channel_features(m, _noise_cov(n), _noise_psd(n, _tilts(n, adc)), adc))
    assert 7 not in det["not_assessable"]
    assert det["ranking"][0] == 7
