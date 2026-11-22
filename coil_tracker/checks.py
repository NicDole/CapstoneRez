"""The four checks run on every scan, and the combined ranking.

1. Single-scan: the existing detector (each element vs its neighbours and mirror, plus the
   noise-spectrum tuning indicator). Catches dead / disconnected / badly detuned elements.
2. Missing elements: elements this coil should have that are not in the scan.
3. Change since baseline: each element's change vs the coil's healthy baseline, compared
   with how all other elements changed between the same scans (so whole-coil effects such
   as a slightly different phantom position cancel out). Works from ONE baseline scan.
4. Drift: a steady trend over the scans since the baseline (needs 3 or more scans).

Evidence follows the detector's rule: an element only counts as changed if it stands out
both from the whole coil AND from its own neighbours, in the same direction. A change
shared by a whole region is reported as a placement/loading alert instead.
"""
import numpy as np

from coil_analysis import channel_fault_analysis as cfa
from coil_tracker import fingerprint as fpm

FLAG = cfa.EVIDENCE_FLAG          # evidence above this -> Fault (single scan) / Watch (history)
TOP_N = 5                         # suspects shown first
VERIFY_N = 3                      # of those, how many get "physically verify"
DRIFT_MIN_SCANS = 3
DRIFT_MAX_SCANS = 10
GROUP_SHIFT = 0.03                # sub-coil signal moving together by more than this -> placement alert
GLOBAL_SHIFT = 0.05               # whole-coil signal/noise change above this -> setup alert

HISTORY_MEASURES = {"signal": "signal", "noise": "noise", "snr": "SNR",
                    "coupling": "noise coupling", "tuning": "tuning tilt"}
# Only these can raise a "Watch": signal (and so SNR) also move when the coil or phantom sits
# slightly differently, while every real fault seen so far changed a noise-based measure
# (10-30: L05 noise +11%, L07 tilt; 11-13: M02 noise -22%, M09 noise -3.6%).
WATCH_MEASURES = ("noise", "coupling", "tuning")


# ── Per-measure values ────────────────────────────────────────────────────────

def corrected_tilt(tilt: np.ndarray, parity: np.ndarray) -> np.ndarray:
    """Tilt minus the median of the elements on same-parity receiver channels."""
    out = np.asarray(tilt, float).copy()
    for p in np.unique(parity):
        out[parity == p] -= np.median(out[parity == p])
    return out


def measures(fp: dict, neighbours: np.ndarray) -> dict:
    """Log signal, log noise, log SNR, log neighbour coupling and corrected tilt per element."""
    f = fpm.features(fp)
    m = {"signal": np.log(f["signal"]), "noise": np.log(f["noise_std"])}
    m["snr"] = m["signal"] - m["noise"]
    m["coupling"] = np.log(np.take_along_axis(f["corr"], neighbours, axis=1).mean(1))
    if fp.get("tilt") is not None:
        m["tuning"] = corrected_tilt(fp["tilt"], f["parity"])
    return m


def _two_way_evidence(delta: np.ndarray, neighbours: np.ndarray):
    """Robust z vs the whole coil and vs the element's neighbours; evidence needs both."""
    z_all = cfa._robust_z(delta)
    z_local = cfa._robust_z(delta - np.median(delta[neighbours], axis=1))
    return cfa._evidence(z_local, z_all), z_all, z_local


def _neighbours(fp: dict) -> np.ndarray:
    return cfa._relations(np.asarray(fp["centroid"]), fp["mirror_axis"])[0]


# ── 1. Single scan ────────────────────────────────────────────────────────────

def single_scan(fp: dict) -> dict:
    return cfa.detect(fpm.features(fp))


# ── 3. Change since baseline ──────────────────────────────────────────────────

def change_since_baseline(fp: dict, baselines: list) -> dict:
    """Per-element change vs the median of the baseline scans (labels as in fp)."""
    labels = [l for l in fp["labels"] if all(l in b["labels"] for b in baselines)]
    new = fpm.subset(fp, labels)
    nb = _neighbours(new)
    m_new = measures(new, nb)
    m_base = [measures(fpm.subset(b, labels), nb) for b in baselines]
    out = {"labels": labels, "n_baselines": len(baselines), "delta": {}, "ev": {}}
    for k in m_new:
        if not all(k in mb for mb in m_base):
            continue
        ref = np.median([mb[k] for mb in m_base], axis=0)
        d = m_new[k] - ref
        out["delta"][k] = d
        out["ev"][k], _, _ = _two_way_evidence(d, nb)
    ev = np.stack(list(out["ev"].values()))
    out["score"] = np.sqrt((ev ** 2).sum(0))
    out["alerts"] = _placement_alerts(labels, out["delta"])
    return out


def _placement_alerts(labels: list, delta: dict) -> list:
    alerts = []
    for k, name in (("signal", "signal"), ("noise", "noise")):
        g = float(np.median(delta[k]))
        if abs(g) > np.log1p(GLOBAL_SHIFT):
            alerts.append(f"Whole-coil {name} {_pct(g)} vs baseline. Likely a different phantom, "
                          f"position or loading, or a scanner change, not an element fault.")
    rel = delta["signal"] - np.median(delta["signal"])
    groups = np.array([l.split(":")[0] for l in labels])
    for g in sorted(set(groups)):
        sel = groups == g
        if sel.sum() >= 4 and abs(np.median(rel[sel])) > np.log1p(GROUP_SHIFT):
            alerts.append(f"Sub-coil {g} signal {_pct(np.median(rel[sel]))} as a group vs baseline. "
                          f"Probably coil or phantom position, not an element fault.")
    return alerts


# ── 4. Drift ──────────────────────────────────────────────────────────────────

def _theil_sen(y: np.ndarray) -> np.ndarray:
    """Median pairwise slope per element (y: scans x elements), per scan step."""
    n = len(y)
    slopes = [(y[j] - y[i]) / (j - i) for i in range(n) for j in range(i + 1, n)]
    return np.median(slopes, axis=0)


def drift(series: list) -> dict:
    """Trend per element over a chronological list of fingerprints (last one = current scan)."""
    series = series[-DRIFT_MAX_SCANS:]
    if len(series) < DRIFT_MIN_SCANS:
        return None
    cur = series[-1]
    labels = [l for l in cur["labels"] if all(l in s["labels"] for s in series)]
    new = fpm.subset(cur, labels)
    nb = _neighbours(new)
    ms = [measures(fpm.subset(s, labels), nb) for s in series]
    out = {"labels": labels, "n_scans": len(series), "total": {}, "ev": {}}
    for k in ms[-1]:
        if not all(k in m for m in ms):
            continue
        y = np.stack([m[k] for m in ms])
        slope = _theil_sen(y)
        out["total"][k] = slope * (len(series) - 1)
        out["ev"][k], _, _ = _two_way_evidence(slope, nb)
    ev = np.stack(list(out["ev"].values()))
    out["score"] = np.sqrt((ev ** 2).sum(0))
    return out


# ── Combined report ───────────────────────────────────────────────────────────

def _pct(log_change: float) -> str:
    return f"{(np.exp(log_change) - 1) * 100:+.1f}%"


def _align(values: np.ndarray, labels: list, target: list) -> np.ndarray:
    pos = {l: i for i, l in enumerate(labels)}
    return np.array([values[pos[l]] if l in pos else 0.0 for l in target])


def _change_text(ch: dict, i: int) -> str:
    d = {k: ch["delta"][k][i] for k in ch["delta"]}
    parts = [f"signal {_pct(d['signal'])}", f"noise {_pct(d['noise'])}", f"SNR {_pct(d['snr'])}"]
    if "tuning" in d:
        parts.append(f"tilt {d['tuning'] * 100:+.1f}%")
    main = max(ch["ev"], key=lambda k: ch["ev"][k][i])
    if main in ("signal", "snr") and max(ch["ev"][k][i] for k in WATCH_MEASURES if k in ch["ev"]) <= FLAG:
        return "Signal changed since baseline, noise steady (likely position): " + ", ".join(parts)
    return f"Changed since baseline (mostly {HISTORY_MEASURES[main]}): " + ", ".join(parts)


def _drift_text(dr: dict, i: int) -> str:
    main = max(dr["ev"], key=lambda k: dr["ev"][k][i])
    t = dr["total"][main][i]
    amount = f"{t * 100:+.1f} tilt" if main == "tuning" else _pct(t)
    return f"Drifting: {HISTORY_MEASURES[main]} {amount} over the last {dr['n_scans']} scans"


def evaluate(fp: dict, expected: list = None, baselines: list = None, series: list = None,
             checked: dict = None) -> dict:
    """Run all checks on one scan.

    expected:  element labels this coil should have (None for a brand-new coil)
    baselines: healthy-baseline fingerprints (empty -> no history check yet)
    series:    chronological fingerprints since the baseline, ending with this scan
    checked:   {label: note} elements someone bench-checked and found fine (known quirks)
    """
    labels = fp["labels"]
    n = len(labels)
    checked = checked or {}
    det = single_scan(fp)
    # A dead or near-dead element is never hidden, even if it was marked as checked before.
    dead = cfa._robust_z(np.log(np.maximum(fpm.features(fp)["signal"], 1e-12))) < cfa.DEAD_Z
    is_checked = np.array([l in checked for l in labels]) & ~dead

    single = np.where(is_checked, 0.0, det["score"])
    single_ev = np.stack([det[f"ev_{k}"] for k in det["measures"]]).max(0)

    ch = change_since_baseline(fp, baselines) if baselines else None
    dr = drift(series) if series and len(series) >= DRIFT_MIN_SCANS else None
    change = _align(ch["score"], ch["labels"], labels) if ch else np.zeros(n)
    drift_s = _align(dr["score"], dr["labels"], labels) if dr else np.zeros(n)
    def noise_ev(res):
        if not res:
            return np.zeros(n)
        ev = np.stack([res["ev"][k] for k in WATCH_MEASURES if k in res["ev"]]).max(0)
        return _align(ev, res["labels"], labels)
    change_max, drift_max = noise_ev(ch), noise_ev(dr)

    total = np.sqrt(single ** 2 + change ** 2 + drift_s ** 2)
    low = np.zeros(n, bool)
    low[det["not_assessable"]] = True
    ranked = np.where(~low)[0]
    ranking = list(ranked[np.argsort(-total[ranked])]) + list(np.where(low)[0])

    elements = []
    for i in range(n):
        fault = single_ev[i] > FLAG and not is_checked[i] and not low[i]
        watch = (change_max[i] > FLAG or drift_max[i] > FLAG) and not low[i]
        level = ("Fault" if fault else "Watch" if watch else
                 "Low signal" if low[i] else "Routine check")
        parts = {"single": single[i], "change": change[i], "drift": drift_s[i]}
        src = max(parts, key=parts.get)
        if fault:
            src = "single"
        if src == "change" and ch:
            reason = _change_text(ch, ch["labels"].index(labels[i]))
        elif src == "drift" and dr:
            reason = _drift_text(dr, dr["labels"].index(labels[i]))
        elif is_checked[i]:
            reason = f"Known quirk, marked as checked ({checked[labels[i]] or 'no note'})"
        else:
            reason = f"Unusual in this scan: {det['reason'][i] or 'no clear deviation'}"
        if is_checked[i] and src != "single":
            reason += f"  · marked as checked ({checked[labels[i]] or 'no note'})"
        if dead[i] and labels[i] in checked:
            reason = "Dead or near-dead now (was marked as checked before): " + reason
        elements.append({"label": labels[i], "short": labels[i].split(":")[-1], "level": level,
                         "score": float(total[i]), "single": float(single[i]),
                         "change": float(change[i]), "drift": float(drift_s[i]),
                         "reason": reason, "low_signal": bool(low[i]), "checked": bool(is_checked[i]),
                         "signal_rel": float(fp["signal"][i] / np.median(fp["signal"]))})

    missing = [l for l in (expected or []) if l not in labels]
    extra = [l for l in labels if expected and l not in expected]
    alerts = []
    if missing:
        alerts.append(f"MISSING elements (expected for this coil, not in the scan): "
                      f"{', '.join(l.split(':')[-1] for l in missing)}. Check connectors and cables.")
    if extra:
        alerts.append(f"Elements not seen before for this coil: {', '.join(l.split(':')[-1] for l in extra)}")
    if ch:
        alerts += ch["alerts"]
        if any(b["meta"].get("maps_from_3d") != fp["meta"].get("maps_from_3d") for b in baselines):
            alerts.append("The baseline and this scan used different map scans (with/without MPRAGE): "
                          "signal comparisons are less reliable.")
    low_list = [elements[i] for i in ranking if low[i]]
    if low_list:
        alerts.append("Low signal, not ranked (phantom barely reaches them, or a weak element; check if "
                      "unexpected): " + ", ".join(f"{e['short']} {e['signal_rel']:.2f}" for e in low_list))

    n_fault = sum(e["level"] == "Fault" for e in elements)
    n_watch = sum(e["level"] == "Watch" for e in elements)
    if missing or n_fault:
        status = "FAULT"
        summary = ", ".join(s for s in (f"{len(missing)} missing element(s)" if missing else "",
                                       f"{n_fault} element(s) clearly abnormal" if n_fault else "") if s)
    elif n_watch:
        status, summary = "WATCH", f"{n_watch} element(s) changed or drifting since baseline"
    else:
        status, summary = "OK", "No clear problem found"
    if not baselines:
        summary += ". No healthy baseline yet: verify the elements below, then set this scan as the baseline"

    order = [elements[i] for i in ranking]
    known = [{"label": l, "short": l.split(":")[-1], "note": n,
              "rank": next((k + 1 for k, e in enumerate(order) if e["label"] == l), None),
              "level": next((e["level"] for e in order if e["label"] == l), "not in scan")}
             for l, n in checked.items()]
    return {"status": status, "summary": summary, "ranking": order, "checked": known,
            "top": [e for e in order if not e["low_signal"]][:TOP_N], "verify_n": VERIFY_N,
            "alerts": alerts, "missing": missing, "n_baselines": len(baselines or []),
            "n_drift_scans": dr["n_scans"] if dr else 0, "meta": fp.get("meta", {})}
