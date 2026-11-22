"""Coil Tracker core: fingerprints, the four checks, the combined report and the library."""
import os
import numpy as np
import pytest

pytest.importorskip("twixtools")
from coil_tracker import fingerprint as fpm, checks
from coil_tracker.library import Library
from tests.test_channel_fault import make_coil_maps, _noise_psd, _tilts

N_RINGS, PER_RING = 4, 12
N = N_RINGS * PER_RING
LABELS = [f"RING{k}:E{j:02d}" for k in range(N_RINGS) for j in range(PER_RING)]
NOISE_SPREAD = np.exp(0.12 * np.random.default_rng(98).standard_normal(N))   # fixed per element


@pytest.fixture(scope="module")
def base_maps():
    """Simulated coil with realistic element-to-element gain spread (~15%, as on HEAD128)."""
    m = make_coil_maps(n_rings=N_RINGS, per_ring=PER_RING, size=32, seed=0)
    spread = np.exp(0.15 * np.random.default_rng(99).standard_normal(N)).astype(np.float32)
    return m * spread[:, None, None, None]


def make_fp(base_maps, seed, gain=None, noise=None, tilt_shift=None, drop=None, date="2026-11-01 10:00:00",
            maps_3d=True):
    """One simulated scan: small random scan-to-scan variation plus any planted changes."""
    rng = np.random.default_rng(seed)
    maps = base_maps * (1 + 0.004 * rng.standard_normal((N, 1, 1, 1))).astype(np.float32)
    x = rng.standard_normal((N, 80000)) + 1j * rng.standard_normal((N, 80000))
    x *= NOISE_SPREAD[:, None] * (1 + 0.003 * rng.standard_normal((N, 1)))
    for i, g in (gain or {}).items():
        maps[i] *= g
    for i, h in (noise or {}).items():
        x[i] *= h
    cov = x @ x.conj().T / x.shape[1]
    adc = np.arange(N)
    t = _tilts(N, adc, seed=3, shift=tilt_shift) + 0.001 * rng.standard_normal(N)
    session = {"labels": list(LABELS), "images": maps, "noise_cov": cov, "noise_psd": _noise_psd(N, t, seed=seed),
               "adc": adc, "paths": ["C:/scans/meas_test_snr.dat"], "coil_keys": {},
               "meta": {"date": date, "protocol": "test"}, "maps_3d": maps_3d}
    fp = fpm.from_session(session)
    if drop:
        fp = fpm.subset(fp, [l for l in fp["labels"] if l not in drop])
    return fp


@pytest.fixture(scope="module")
def baseline(base_maps):
    return make_fp(base_maps, seed=10)


# ── Fingerprint ───────────────────────────────────────────────────────────────

def test_fingerprint_roundtrip_small_no_images(baseline, tmp_path):
    p = tmp_path / "scan.npz"
    fpm.save(baseline, str(p))
    back = fpm.load(str(p))
    assert back["labels"] == baseline["labels"]
    assert np.allclose(back["signal"], baseline["signal"])
    assert np.allclose(back["noise_cov"], baseline["noise_cov"])
    assert "images" not in back
    assert os.path.getsize(p) < 300_000
    assert back["meta"]["files"] == ["meas_test_snr.dat"]        # file names only, no folders


def test_fingerprint_needs_noise(base_maps):
    with pytest.raises(ValueError):
        fpm.from_session({"labels": LABELS, "images": base_maps, "noise_cov": None})


def test_single_scan_from_fingerprint_matches_detector(baseline):
    det = checks.single_scan(baseline)
    assert len(det["score"]) == N and len(det["flagged"]) == 0


# ── Report: first scan, change, placement, missing, drift, checked ───────────

def test_first_scan_healthy_is_ok(baseline):
    r = checks.evaluate(baseline)
    assert r["status"] == "OK"
    assert "No healthy baseline yet" in r["summary"]
    assert len(r["top"]) == checks.TOP_N


def test_first_scan_dead_element_is_fault(base_maps):
    fp = make_fp(base_maps, seed=11, gain={7: 0.03})
    r = checks.evaluate(fp)
    assert r["status"] == "FAULT"
    assert r["ranking"][0]["label"] == LABELS[7] and r["ranking"][0]["level"] == "Fault"


def test_slight_unmatch_found_against_one_baseline(base_maps, baseline):
    """Like M02 on 11-13: signal and noise both down ~25%, invisible in a single scan."""
    fp = make_fp(base_maps, seed=12, gain={20: 0.75}, noise={20: 0.78})
    r = checks.evaluate(fp, expected=LABELS, baselines=[baseline])
    top = r["ranking"][0]
    assert top["label"] == LABELS[20] and top["level"] == "Watch"
    assert r["status"] == "WATCH"
    assert "noise" in top["reason"]


def test_slight_untune_noise_only_found(base_maps, baseline):
    """Like M09 on 11-13: noise -3.6%, signal almost unchanged."""
    fp = make_fp(base_maps, seed=13, noise={33: 0.964})
    r = checks.evaluate(fp, expected=LABELS, baselines=[baseline])
    assert r["ranking"][0]["label"] == LABELS[33]


def test_whole_ring_moving_together_is_placement_not_fault(base_maps, baseline):
    fp = make_fp(base_maps, seed=14, gain={i: 1.12 for i in range(PER_RING)})   # ring 0 +12%
    r = checks.evaluate(fp, expected=LABELS, baselines=[baseline])
    assert r["status"] == "OK"
    assert any("RING0" in a and "position" in a for a in r["alerts"])


def test_missing_element_is_fault(base_maps, baseline):
    fp = make_fp(base_maps, seed=15, drop=[LABELS[5]])
    r = checks.evaluate(fp, expected=LABELS, baselines=[baseline])
    assert r["status"] == "FAULT"
    assert r["missing"] == [LABELS[5]]
    assert any("MISSING" in a and "E05" in a for a in r["alerts"])


def test_slow_drift_found(base_maps, baseline):
    """Noise of one element drifting down 1.2% per scan; no single step is large."""
    series = [baseline] + [make_fp(base_maps, seed=20 + s, noise={40: 0.988 ** (s + 1)},
                                   date=f"2026-11-{s + 2:02d} 10:00:00") for s in range(5)]
    r = checks.evaluate(series[-1], expected=LABELS, baselines=[baseline], series=series)
    assert r["n_drift_scans"] == 6
    e = r["ranking"][0]
    assert e["label"] == LABELS[40] and e["drift"] > checks.FLAG


def test_checked_element_is_not_a_fault(base_maps):
    fp = make_fp(base_maps, seed=10, tilt_shift={9: 0.08})        # strong single-scan anomaly
    before = checks.evaluate(fp)
    assert before["ranking"][0]["label"] == LABELS[9] and before["ranking"][0]["level"] == "Fault"
    r = checks.evaluate(fp, checked={LABELS[9]: "bench-checked, fine"})
    e = next(x for x in r["ranking"] if x["label"] == LABELS[9])
    assert e["level"] != "Fault" and "Known quirk" in e["reason"]
    assert r["status"] == "OK"


# ── Library ───────────────────────────────────────────────────────────────────

def test_library_workflow(base_maps, baseline, tmp_path):
    lib = Library(str(tmp_path / "lib"))
    coil = lib.create_coil("Head 128 lab A", baseline)
    assert lib.match_coil(baseline)[0][0]["id"] == coil["id"]

    r0 = lib.evaluate(coil["id"], baseline)
    assert r0["n_baselines"] == 0
    s0 = lib.add_scan(coil["id"], baseline, r0, baseline=True)

    fault = make_fp(base_maps, seed=30, gain={20: 0.75}, noise={20: 0.78}, date="2026-11-05 10:00:00")
    r1 = lib.evaluate(coil["id"], fault)
    assert r1["n_baselines"] == 1 and r1["ranking"][0]["label"] == LABELS[20]
    s1 = lib.add_scan(coil["id"], fault, r1)

    # after a repair: new baseline replaces the old one
    repaired = make_fp(base_maps, seed=31, gain={20: 0.75}, noise={20: 0.78}, date="2026-11-06 10:00:00")
    s2 = lib.add_scan(coil["id"], repaired, lib.evaluate(coil["id"], repaired), reset_baseline=True)
    later = make_fp(base_maps, seed=32, gain={20: 0.75}, noise={20: 0.78}, date="2026-11-07 10:00:00")
    r3 = lib.evaluate(coil["id"], later)
    assert r3["baseline_dates"] == ["2026-11-06 10:00:00"]
    assert r3["status"] == "OK"                                   # same as the new baseline

    # re-opening the old fault report uses only what existed then
    rec1 = next(s for s in lib.scans(coil["id"]) if s["id"] == s1)
    again = lib.evaluate(coil["id"], lib.load_fp(coil["id"], s1), before=rec1)
    assert again["ranking"][0]["label"] == LABELS[20]

    lib.set_checked(coil["id"], LABELS[3], "fine on bench")
    assert LABELS[3] in lib.coil(coil["id"])["checked"]
    lib.delete_scan(coil["id"], s0)
    assert [s["id"] for s in lib.scans(coil["id"])] == [s1, s2]


def test_unknown_coil_not_matched(base_maps, baseline, tmp_path):
    lib = Library(str(tmp_path / "lib"))
    lib.create_coil("Head 128", baseline)
    other = fpm.subset(baseline, LABELS[:10])
    other["labels"] = [f"CUSTOM:X{i}" for i in range(10)]
    assert lib.match_coil(other) == []


def test_checked_element_that_dies_is_still_a_fault(base_maps):
    fp = make_fp(base_maps, seed=17, gain={9: 0.03})
    r = checks.evaluate(fp, checked={LABELS[9]: "was fine on the bench"})
    e = next(x for x in r["ranking"] if x["label"] == LABELS[9])
    assert e["level"] == "Fault" and "Dead or near-dead" in e["reason"]
    assert r["status"] == "FAULT"
    assert r["checked"][0]["short"] == "E09"
