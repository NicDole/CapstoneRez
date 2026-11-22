"""Scan fingerprint: a small summary of one scan session (~200 KB instead of GBs of raw data).

It keeps everything the checks need, so old scans can be compared after the raw files are
archived: per-element signal, noise, noise-spectrum tilt, receiver channel and map position,
the full noise covariance and a compressed noise spectrum. No images and no patient details.
"""
import io
import json
import numpy as np

from coil_analysis import channel_fault_analysis as cfa
from coil_tracker import __version__

FORMAT_VERSION = 1
PSD_BINS = 128           # noise spectrum is stored at this resolution


def from_session(session: dict, notes: str = "", operator: str = "") -> dict:
    """Build a fingerprint from a loaded session (coil_analysis.channel_fault_analysis.load_session)."""
    if session.get("noise_cov") is None:
        raise ValueError("No noise-only readouts found. Please include the SNR-map scan.")
    feat = cfa.channel_features(session["images"], session["noise_cov"],
                                session.get("noise_psd"), session.get("adc"))
    meta = dict(session.get("meta") or {})
    meta.update({"files": [p.replace("\\", "/").split("/")[-1] for p in session.get("paths", [])],
                 "maps_from_3d": bool(session.get("maps_3d", session["images"].ndim == 4)),
                 "notes": notes, "operator": operator, "app_version": __version__,
                 "format_version": FORMAT_VERSION})
    psd = session.get("noise_psd")
    return {"labels": list(session["labels"]),
            "coil_keys": dict(session.get("coil_keys") or {}),
            "adc": np.asarray(session.get("adc", np.arange(len(session["labels"])))),
            "signal": feat["signal"], "noise_std": feat["noise_std"],
            "noise_cov": np.asarray(session["noise_cov"], np.complex64),
            "tilt": feat["tilt"], "centroid": feat["centroid"],
            "mirror_axis": int(feat["mirror_axis"]), "mirror_mutual": float(feat["mirror_mutual"]),
            "psd": _compress_psd(psd) if psd is not None else None,
            "meta": meta}


def features(fp: dict) -> dict:
    """The feature dict the single-scan detector expects, rebuilt from a fingerprint."""
    cov = fp["noise_cov"].astype(np.complex128)
    power = np.real(np.diag(cov))
    return {"signal": fp["signal"], "noise_std": fp["noise_std"], "noise_source": "noise-only readouts",
            "corr": np.abs(cov) / np.sqrt(np.outer(power, power)),
            "tilt": fp["tilt"], "parity": np.asarray(fp["adc"]) % 2,
            "centroid": fp["centroid"], "mirror_axis": fp["mirror_axis"],
            "mirror_mutual": fp["mirror_mutual"]}


def _compress_psd(psd: np.ndarray) -> np.ndarray:
    n = psd.shape[1] // PSD_BINS * PSD_BINS
    return psd[:, :n].reshape(psd.shape[0], PSD_BINS, -1).mean(2).astype(np.float32)


# ── Saving and loading ───────────────────────────────────────────────────────

ARRAYS = ("adc", "signal", "noise_std", "noise_cov", "tilt", "centroid", "psd")


def save(fp: dict, path: str):
    arrays = {k: fp[k] for k in ARRAYS if fp.get(k) is not None}
    header = {k: fp[k] for k in ("labels", "coil_keys", "mirror_axis", "mirror_mutual", "meta")}
    buf = io.BytesIO()
    np.savez_compressed(buf, header=np.frombuffer(json.dumps(header).encode(), np.uint8), **arrays)
    with open(path, "wb") as f:
        f.write(buf.getvalue())


def load(path: str) -> dict:
    with np.load(path, allow_pickle=False) as z:
        fp = json.loads(bytes(z["header"]).decode())
        for k in ARRAYS:
            fp[k] = z[k] if k in z.files else None
    return fp


def subset(fp: dict, labels: list) -> dict:
    """The fingerprint restricted to (and ordered by) the given element labels."""
    idx = [fp["labels"].index(l) for l in labels]
    out = dict(fp)
    out["labels"] = list(labels)
    for k in ("adc", "signal", "noise_std", "tilt", "centroid", "psd"):
        if fp.get(k) is not None:
            out[k] = np.asarray(fp[k])[idx]
    out["noise_cov"] = fp["noise_cov"][np.ix_(idx, idx)]
    return out
