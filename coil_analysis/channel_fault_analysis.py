"""
Per-element coil fault detection for one headcap session (Siemens raw data, .dat / TWIX).

Input: the .dat files of ONE coil state (e.g. an MPRAGE and an SNR-map scan from the
same session). No reference or earlier scan is used. The tool takes:
  - per-element sensitivity maps from the 3D scan (signal, map position)
  - the noise covariance matrix from noise-only readouts (e.g. the SNR-map scan)

Each element gets four measurements (Tunnicliffe et al., ISMRM 2011; IOP PMB 2024):
  signal, noise level, SNR (= signal / noise), noise coupling to its neighbours.
Each is compared with the element's spatial neighbours AND its mirror partner across
the head; it only counts as evidence if the element deviates from both in the same
direction (too high or too low).

A fifth measurement, the tuning indicator, comes from the shape of each element's
noise spectrum: its tilt across the receiver band. Part of the tilt is set by the
receiver hardware (odd and even ADC channels tilt in opposite directions), so it is
compared with the elements on receiver channels of the same parity. What is left
reflects the element's own tuning (TECHNICAL_LOG Phase 3e).

The five pieces of evidence are combined with equal weight into one score, and the
output is a ranked list of suspect elements (top 3 shown first). Elements with very
low signal (< 1/3 of the coil median, but not dead-like and not clearly below their
own neighbours) are listed separately, not ranked: usually the object barely reaches
them and their noise spectrum reflects that, but from one scan a weak element looks
the same, so the list should be checked for unexpected entries. Dead-like elements
are always ranked.

An optional reference session can be passed with --validate-against. It is never used
for detection, only to check afterwards whether the suspects really changed.

Usage:
  python coil_analysis/channel_fault_analysis.py MPRAGE.dat SNRMAP.dat
         [--validate-against REF_MPRAGE.dat REF_SNRMAP.dat] [--inject-dead ELEMENT]
"""

import os
import sys
import json
import argparse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import twixtools

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "channel_fault_output")

MAP_SIZE = 64            # low-res per-channel maps (centre of k-space only)
N_NEIGHBOURS = 4
TOP_VOXELS = 200         # element signal = mean of its brightest voxels inside the head
Z_FAULT = -5.0           # signal robust z below this vs neighbours or mirror -> not used as a reference
EVIDENCE_FLAG = 5.0      # evidence on any one measurement above this -> high-confidence flag
DEAD_Z = -8.0            # signal this far below the whole coil = dead-like; excluded as a reference
                         # (10-30 data: weakest healthy element -3.7, disconnected element ~-15)
TOP_K = 3                # length of the suspect list printed first
LOW_COVERAGE = 1 / 3     # signal below this fraction of the coil median -> object barely reaches the element
SPECTRUM_BAND = (0.2, 0.8)   # part of the noise spectrum used for the tilt (avoids the filter roll-off)

# measurement name -> label for reports. All are two-sided: a dematched element got
# brighter (10-30 data, L05 x1.32), so "too high" counts as well as "too low".
MEASURES = {"signal": "abnormal signal",
            "noise": "abnormal noise level",
            "snr": "abnormal SNR",
            "coupling": "abnormal noise coupling",
            "tuning": "abnormal tuning (noise-spectrum tilt)"}


# ── Loading ────────────────────────────────────────────────────────────────────

def load_scan(dat_path: str, n: int = MAP_SIZE) -> dict:
    """Low-res per-channel magnitude images, noise samples (if any) and element labels."""
    tw = twixtools.read_twix(dat_path, verbose=False, parse_pmu=False, keep_syncdata=False)
    meas = tw[-1]
    yaps = meas["hdr"]["MeasYaps"]
    coil_list = yaps["sCoilSelectMeas"]["aRxCoilSelectData"][0]["asList"]
    adc_to_label = {c["lADCChannelConnected"] - 1:
                    f'{c["sCoilElementID"]["tCoilID"]}:{c["sCoilElementID"]["tElement"]}'
                    for c in coil_list}
    coil_keys = {f'{c["sCoilElementID"]["tCoilID"]}:{c["sCoilElementID"]["tElement"]}':
                 str(c["sCoilElementID"].get("ulUniqueKey", "")) for c in coil_list}

    image_blocks = [b for b in meas["mdb"] if b.is_image_scan()]
    noise_blocks = [b for b in meas["mdb"] if b.is_flag_set("NOISEADJSCAN")]
    if not image_blocks:
        raise ValueError(f"{dat_path}: no image readouts")

    h = image_blocks[0].mdh
    n_ch, n_col = image_blocks[0].data.shape
    adc = np.array([ch.ChannelId for ch in image_blocks[0].channel_hdr])
    labels = [adc_to_label.get(c, f"ch{c}") for c in adc]
    n_par = n if max(b.cPar for b in image_blocks) > 0 else 1

    ks = np.zeros((n_ch, n, n_par, 2 * n), np.complex64)
    hits = np.zeros((n, n_par))
    c0 = h.CenterCol - n
    for b in image_blocks:
        li = b.cLin - h.CenterLin + n // 2
        pi = b.cPar - h.CenterPar + n_par // 2 if n_par > 1 else 0
        if 0 <= li < n and 0 <= pi < n_par:
            src = b.data[:, max(c0, 0):min(c0 + 2 * n, n_col)]
            start = max(0, -c0)
            ks[:, li, pi, start:start + src.shape[1]] += src
            hits[li, pi] += 1
    ks /= np.maximum(hits, 1)[None, :, :, None]

    axes = (1, 2, 3) if n_par > 1 else (1, 3)
    im = np.fft.fftshift(np.fft.ifftn(np.fft.ifftshift(ks, axes=axes), axes=axes), axes=axes)
    im = np.abs(im[..., n // 2: n // 2 + n]).astype(np.float32)   # crop readout oversampling
    if n_par == 1:
        im = im[:, :, 0, :]

    noise_cov, noise_psd, n_noise = None, None, 0
    if noise_blocks:
        noise_labels = [adc_to_label.get(ch.ChannelId, f"ch{ch.ChannelId}") for ch in noise_blocks[0].channel_hdr]
        order = [noise_labels.index(l) for l in labels]
        noise = np.concatenate([b.data for b in noise_blocks], axis=1).astype(np.complex128)[order]
        noise_cov = noise @ noise.conj().T / noise.shape[1]
        noise_psd = noise_spectrum([b.data[order] for b in noise_blocks])
        n_noise = noise.shape[1]
    return {"path": dat_path, "protocol": yaps.get("tProtocolName", ""), "labels": labels, "adc": adc,
            "images": im, "is_3d": n_par > 1, "noise_cov": noise_cov, "noise_psd": noise_psd,
            "noise_samples": n_noise, "meta": scan_meta(meas["hdr"]), "coil_keys": coil_keys}


def scan_meta(hdr: dict) -> dict:
    """Scan date, scanner and software from the header. Patient fields are never read."""
    dicom, meas = hdr.get("Dicom", {}), hdr.get("Meas", {})
    get = lambda k: str(meas.get(k) or dicom.get(k) or "")
    return {"date": get("PrepareTimestamp"), "scanner_model": get("ManufacturersModelName"),
            "scanner_serial": get("DeviceSerialNumber"), "software": get("SoftwareVersions"),
            "protocol": str(hdr.get("MeasYaps", {}).get("tProtocolName", ""))}


def noise_spectrum(readouts: list) -> np.ndarray:
    """Mean power spectrum of each channel over noise-only readouts (channels x frequency)."""
    psd = None
    for r in readouts:
        p = np.abs(np.fft.fftshift(np.fft.fft(r, axis=1), axes=1)) ** 2
        psd = p if psd is None else psd + p
    return psd / len(readouts)


def load_session(paths: list) -> dict:
    """Combine the scans of one coil state: maps from the 3D scan, noise from noise readouts."""
    return combine_scans([load_scan(p) for p in paths])


def combine_scans(scans: list) -> dict:
    """Session from already-loaded scans (see load_session)."""
    maps = max(scans, key=lambda s: (s["is_3d"], s["images"].size))
    with_noise = [s for s in scans if s["noise_cov"] is not None]
    noise_cov = noise_psd = None
    adc = maps["adc"]
    if with_noise:
        src = max(with_noise, key=lambda s: s["noise_samples"])   # longest noise recording
        if set(src["labels"]) != set(maps["labels"]):
            raise ValueError("noise scan and map scan use different coil elements")
        order = [src["labels"].index(l) for l in maps["labels"]]
        noise_cov = src["noise_cov"][np.ix_(order, order)]
        noise_psd = src["noise_psd"][order]
        adc = src["adc"][order]        # receiver channel the noise spectrum was recorded on
    return {"labels": maps["labels"], "images": maps["images"], "noise_cov": noise_cov,
            "noise_psd": noise_psd, "adc": adc,
            "protocol": maps["protocol"], "paths": [s["path"] for s in scans],
            "meta": (with_noise[0] if with_noise else maps)["meta"], "coil_keys": maps["coil_keys"],
            "maps_3d": maps["is_3d"]}


# ── Single-scan features and detection ────────────────────────────────────────

def _robust_z(x: np.ndarray) -> np.ndarray:
    med = np.median(x)
    mad = 1.4826 * np.median(np.abs(x - med))
    return (x - med) / max(mad, 1e-12)


def spectrum_tilt(noise_psd: np.ndarray) -> np.ndarray:
    """Tilt of each channel's noise spectrum relative to the coil's typical spectrum shape.

    Each spectrum is normalised to its own mean, divided by the median shape over all
    channels, and the log of that ratio is fitted with Legendre polynomials across the
    band; the first-order coefficient is the tilt (about -0.05..+0.05 when healthy).
    """
    n_f = noise_psd.shape[1]
    lo, hi = int(SPECTRUM_BAND[0] * n_f), int(SPECTRUM_BAND[1] * n_f)
    psd = noise_psd[:, lo:hi].astype(np.float64)
    shape = psd / psd.mean(1, keepdims=True)
    rel = np.log(shape / np.median(shape, axis=0))
    x = np.linspace(-1, 1, hi - lo)
    return np.polynomial.legendre.legfit(x, rel.T, 2)[1]


def channel_features(images: np.ndarray, noise_cov: np.ndarray = None,
                     noise_psd: np.ndarray = None, adc: np.ndarray = None) -> dict:
    """Per-element signal, noise, noise correlation, spectrum tilt, map centroid and mirror axis."""
    n = images.shape[0]
    flat = images.reshape(n, -1).astype(np.float64)
    sos = np.sqrt((flat ** 2).sum(0))
    head = sos > np.percentile(sos, 70)
    air = sos < np.percentile(sos, 20)

    signal = np.mean(np.sort(flat[:, head], axis=1)[:, -TOP_VOXELS:], axis=1)
    corr = None
    if noise_cov is not None:
        power = np.real(np.diag(noise_cov))
        noise_std = np.sqrt(power / 2)
        corr = np.abs(noise_cov) / np.sqrt(np.outer(power, power))
        noise_source = "noise-only readouts"
    else:
        noise_std = np.median(flat[:, air], axis=1)
        noise_source = "image background (approximate)"

    grid = np.indices(images.shape[1:]).reshape(images.ndim - 1, -1).astype(float)
    w = flat ** 2
    centroid = (w @ grid.T) / w.sum(1)[:, None]

    # Left-right axis = the axis whose mirror gives the most mutual element pairs.
    best = None
    for ax in range(centroid.shape[1]):
        neighbours, partner = _relations(centroid, ax)
        mutual = np.mean(partner[partner] == np.arange(n))
        if best is None or mutual > best[0]:
            best = (mutual, ax)

    tilt = spectrum_tilt(noise_psd) if noise_psd is not None else None
    # receiver-channel parity: odd and even ADC channels have opposite hardware tilt
    parity = np.asarray(adc) % 2 if adc is not None else np.zeros(n, int)

    return {"signal": signal, "noise_std": noise_std, "noise_source": noise_source, "corr": corr,
            "tilt": tilt, "parity": parity,
            "centroid": centroid, "mirror_axis": best[1], "mirror_mutual": float(best[0])}


def _relations(centroid: np.ndarray, mirror_axis: int, exclude: np.ndarray = None):
    """Nearest neighbours and mirror partner of every element, never using excluded ones."""
    n = len(centroid)
    bad = np.zeros(n, bool) if exclude is None else exclude
    dist = np.linalg.norm(centroid[:, None] - centroid[None], axis=2)
    dist[:, bad] = np.inf
    np.fill_diagonal(dist, np.inf)
    neighbours = np.argsort(dist, axis=1)[:, :N_NEIGHBOURS]

    mirrored = centroid.copy()
    mirrored[:, mirror_axis] = 2 * centroid[:, mirror_axis].mean() - mirrored[:, mirror_axis]
    mdist = np.linalg.norm(mirrored[:, None] - centroid[None], axis=2)
    mdist[:, bad] = np.inf
    np.fill_diagonal(mdist, np.inf)
    return neighbours, mdist.argmin(1)


def _measurements(feat: dict, neighbours: np.ndarray) -> dict:
    """Log of each per-element measurement available for this session."""
    m = {"signal": np.log(feat["signal"]),
         "noise": np.log(feat["noise_std"]),
         "snr": np.log(feat["signal"] / feat["noise_std"])}
    if feat["corr"] is not None:
        # mean noise correlation with the element's spatial neighbours
        m["coupling"] = np.log(np.take_along_axis(feat["corr"], neighbours, axis=1).mean(1))
    return m


def _evidence(z_nb: np.ndarray, z_mirror: np.ndarray) -> np.ndarray:
    """How far an element deviates from BOTH references in the same direction (0 if not)."""
    low = np.minimum(np.maximum(-z_nb, 0), np.maximum(-z_mirror, 0))
    high = np.minimum(np.maximum(z_nb, 0), np.maximum(z_mirror, 0))
    return np.maximum(low, high)


def tuning_z(tilt: np.ndarray, parity: np.ndarray) -> np.ndarray:
    """Spectrum tilt vs the elements on same-parity receiver channels, as a robust z."""
    group_median = np.zeros_like(tilt)
    for p in np.unique(parity):
        group_median[parity == p] = np.median(tilt[parity == p])
    return _robust_z(tilt - group_median)


def _score(feat: dict, neighbours: np.ndarray, partner: np.ndarray) -> dict:
    out = {}
    for name, x in _measurements(feat, neighbours).items():
        z_nb = _robust_z(x - np.median(x[neighbours], axis=1))
        z_mirror = _robust_z(x - x[partner])
        out[f"z_{name}_nb"], out[f"z_{name}_mirror"] = z_nb, z_mirror
        out[f"ev_{name}"] = _evidence(z_nb, z_mirror)
    if feat.get("tilt") is not None:
        # Tuning is compared with the whole coil, not neighbours/mirror: neighbouring
        # elements share no tuning, and the hardware pattern follows the receiver channel.
        out["z_tuning"] = tuning_z(feat["tilt"], feat["parity"])
        out["ev_tuning"] = np.abs(out["z_tuning"])
    return out


def detect(feat: dict, max_iter: int = 5) -> dict:
    """Rank elements by how far they deviate from their neighbours and mirror partner.

    Iterative: a dead element's map is pure noise, so its centroid lands mid-head and
    two dead elements would otherwise serve as each other's reference. Elements with
    very low signal are removed from the reference sets and everything is re-scored.
    """
    global_low = _robust_z(np.log(feat["signal"])) < DEAD_Z
    exclude = global_low.copy()
    for _ in range(max_iter):
        neighbours, partner = _relations(feat["centroid"], feat["mirror_axis"], exclude)
        z = _score(feat, neighbours, partner)
        suspects = (np.minimum(z["z_signal_nb"], z["z_signal_mirror"]) < Z_FAULT) | global_low
        if np.array_equal(suspects, exclude):
            break
        exclude = suspects

    names = [k for k in MEASURES if f"ev_{k}" in z]
    ev = np.stack([z[f"ev_{k}"] for k in names])               # measurements x elements
    score = np.sqrt((ev ** 2).sum(0))                          # equal weights
    main = ev.argmax(0)
    reason = [MEASURES[names[main[i]]] if ev[main[i], i] > 0 else "" for i in range(ev.shape[1])]

    # Coverage: an element the object barely reaches has a noise spectrum set by its
    # loading, not its health, so it is not ranked. Dead-like elements, and elements
    # clearly weaker than their neighbours or mirror, are still ranked. A partly weak
    # element (e.g. 25% signal left) can end up in this list, hence it is reported.
    weak = feat["signal"] / np.median(feat["signal"]) < LOW_COVERAGE
    not_assessable = weak & ~exclude & ~global_low

    ranked = np.where(~not_assessable)[0]
    ranking = np.concatenate([ranked[np.argsort(-score[ranked])],
                              np.where(not_assessable)[0]])     # not assessable go last
    flagged = ranked[ev[:, ranked].max(0) > EVIDENCE_FLAG]
    flagged = flagged[np.argsort(-score[flagged])]
    return {**z, "measures": names, "score": score, "ranking": ranking,
            "not_assessable": np.where(not_assessable)[0],
            "flagged": flagged, "reason": reason, "neighbours": neighbours, "partner": partner}


def inject_dead_channel(images: np.ndarray, idx: int, signal_kept: float = 0.0) -> np.ndarray:
    """Simulate a disconnected/weak element: keep its noise floor, scale its signal."""
    out = images.copy()
    n = images.shape[0]
    flat = out.reshape(n, -1)
    sos = np.sqrt((flat.astype(np.float64) ** 2).sum(0))
    floor = np.median(flat[idx, sos < np.percentile(sos, 20)])
    flat[idx] = np.sqrt(np.maximum(flat[idx] ** 2 - floor ** 2, 0) * signal_kept ** 2 + floor ** 2)
    return out


# ── Validation against a reference (not used for detection) ───────────────────

def validate(test_feat: dict, ref_feat: dict, det: dict) -> dict:
    """Which elements really changed vs a reference session, and where did we rank them?"""
    out = {}
    for key in ("signal", "noise_std"):
        r = test_feat[key] / ref_feat[key]
        r = r / np.median(r)
        out[key] = {"ratio": r, "z": _robust_z(np.log(r))}
    changed = np.where((np.abs(out["signal"]["z"]) > 8) | (np.abs(out["noise_std"]["z"]) > 8))[0]
    rank = np.empty(len(det["score"]), int)
    rank[det["ranking"]] = np.arange(1, len(rank) + 1)
    top = set(det["ranking"][:TOP_K].tolist())
    out["changed"] = changed
    out["rank_of_changed"] = {int(i): int(rank[i]) for i in changed}
    out["confirmed"] = sorted(top & set(changed.tolist()))              # in our top-K and really changed
    out["missed"] = sorted(set(changed.tolist()) - top)
    out["unconfirmed"] = sorted(set(det["flagged"].tolist()) - set(changed.tolist()))
    return out


# ── Reporting ─────────────────────────────────────────────────────────────────

COLORS = {"signal": "tab:blue", "noise": "tab:orange", "snr": "tab:green", "coupling": "tab:purple",
          "tuning": "tab:brown"}


def plot_detection(name, labels, images, feat, det, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    n = len(labels)
    x = np.arange(n)
    short = [l.split(":")[-1] for l in labels]

    # Stacked evidence: each segment is one measurement's share of the score (ev^2 sums to score^2).
    fig, ax = plt.subplots(figsize=(18, 6))
    bottom = np.zeros(n)
    na = np.zeros(n, bool)
    na[det["not_assessable"]] = True
    for k in det["measures"]:
        part = det[f"ev_{k}"] ** 2 / np.maximum(det["score"], 1e-12)
        ax.bar(x[~na], part[~na], bottom=bottom[~na], color=COLORS[k], label=MEASURES[k])
        bottom += part
    if na.any():
        ax.bar(x[na], det["score"][na], color="lightgray", hatch="//", edgecolor="gray", lw=0,
               label=f"low signal, not ranked (< {LOW_COVERAGE:.2g} of median)")
    for r, i in enumerate(det["ranking"][:TOP_K], 1):
        ax.annotate(f"#{r} {short[i]}", (i, det["score"][i]), textcoords="offset points",
                    xytext=(0, 4), ha="center", fontsize=9, fontweight="bold")
    ax.axhline(EVIDENCE_FLAG, color="tab:red", ls=":", label=f"high-confidence flag ({EVIDENCE_FLAG})")
    ax.set_xticks(x)
    ax.set_xticklabels(short, rotation=90, fontsize=6)
    ax.set_xlabel("Coil element")
    ax.set_ylabel("Suspicion score")
    ax.set_ylim(0, max(det["score"].max(), EVIDENCE_FLAG) * 1.15)
    ax.legend(loc="upper left", ncol=4, fontsize=9)
    ax.set_title(f"Coil element suspicion ranking — {name}  [noise: {feat['noise_source']}]")
    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, f"{name}_ranking.png"), dpi=130)
    plt.close(fig)

    show = list(det["ranking"][:TOP_K])
    mid = (lambda a: a[:, :, a.shape[2] // 2]) if images.ndim == 4 else (lambda a: a)
    sos = mid(np.sqrt((images.astype(np.float64) ** 2).sum(0)))
    fig, axes = plt.subplots(1, 1 + len(show), figsize=(4 * (1 + len(show)), 4.4))
    axes[0].imshow(sos.T, cmap="gray", origin="lower")
    axes[0].set_title("Combined image")
    for r, (ax, i) in enumerate(zip(axes[1:], show), 1):
        j = det["partner"][i]
        pair = np.concatenate([mid(images[i]), mid(images[j])], axis=0).T
        ax.imshow(pair, cmap="gray", origin="lower", vmin=0, vmax=np.percentile(mid(images[j]), 99.5))
        ax.set_title(f"#{r} {short[i]} | mirror {short[j]}")
    for ax in axes:
        ax.axis("off")
    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, f"{name}_suspects.png"), dpi=130)
    plt.close(fig)


def analyse(session: dict, name: str, output_dir: str) -> dict:
    labels = session["labels"]
    feat = channel_features(session["images"], session["noise_cov"],
                            session.get("noise_psd"), session.get("adc"))
    det = detect(feat)
    plot_detection(name, labels, session["images"], feat, det, output_dir)

    print(f"\n{name}: {len(labels)} elements, mirror pairing {feat['mirror_mutual']:.0%} mutual, "
          f"noise from {feat['noise_source']}")
    if feat["tilt"] is None:
        print("  (no noise spectrum: tuning indicator not used)")
    head = "".join(f"{k:>10}" for k in det["measures"])
    print(f"  {'rank':<6}{'element':<20}{'score':>7}{head}   main reason")
    for r, i in enumerate(det["ranking"][:6], 1):
        evs = "".join(f"{det[f'ev_{k}'][i]:>10.1f}" for k in det["measures"])
        top = " <-- suspect" if r <= TOP_K else ""
        print(f"  {r:<6}{labels[i]:<20}{det['score'][i]:>7.1f}{evs}   {det['reason'][i]}{top}")
    if len(det["flagged"]):
        print(f"  High-confidence flags (evidence > {EVIDENCE_FLAG}): "
              + ", ".join(f"{labels[i]} ({det['reason'][i]})" for i in det["flagged"]))
    else:
        print(f"  No high-confidence flags; top {TOP_K} are the most likely candidates.")
    if len(det["not_assessable"]):
        rel = feat["signal"] / np.median(feat["signal"])
        print(f"  Low signal, not ranked (poor coverage, or a weak element - check if unexpected; "
              f"signal vs median): "
              + ", ".join(f"{labels[i].split(':')[-1]} {rel[i]:.2f}" for i in det["not_assessable"]))

    # Group-level view: a whole sub-coil being weak is reported separately from element faults.
    subcoil = np.array([l.split(":")[0] for l in labels])
    ls = np.log(feat["signal"])
    groups = {}
    for s in sorted(set(subcoil)):
        m = subcoil == s
        groups[s] = {"elements": int(m.sum()),
                     "signal_vs_coil": round(float(np.exp(np.median(ls[m]) - np.median(ls))), 2)}
    print("  Sub-coil median signal vs whole coil: " +
          ", ".join(f"{s.split('_')[-1]}={g['signal_vs_coil']:.2f}" for s, g in groups.items()))

    def entry(i):
        return {"element": labels[i], "score": round(float(det["score"][i]), 1), "reason": det["reason"][i],
                **{k: round(float(det[f"ev_{k}"][i]), 1) for k in det["measures"]}}
    return {"feat": feat, "det": det,
            "summary": {"suspects": [entry(i) for i in det["ranking"][:TOP_K]],
                        "high_confidence": [entry(i) for i in det["flagged"]],
                        "top10": [entry(i) for i in det["ranking"][:10]],
                        "low_signal_not_ranked": [labels[i] for i in det["not_assessable"]],
                        "subcoils": groups, "files": session.get("paths", [])}}


def run(scan_paths: list, reference_paths: list = None, inject: str = None,
        output_dir: str = OUTPUT_DIR) -> dict:
    session = load_session(scan_paths)
    name = os.path.splitext(os.path.basename(scan_paths[0]))[0]
    if inject:
        session["images"] = inject_dead_channel(session["images"], session["labels"].index(inject))
        name += f"_injected_{inject.split(':')[-1]}"
        print(f"\n[simulation] {inject} made dead in the maps (signal removed, noise kept)")
    res = analyse(session, name, output_dir)
    summary = {name: res["summary"]}

    if reference_paths:
        ref = load_session(reference_paths)
        if ref["labels"] != session["labels"]:
            raise ValueError("reference session uses a different channel layout")
        v = validate(res["feat"], channel_features(ref["images"], ref["noise_cov"]), res["det"])
        na = set(res["det"]["not_assessable"].tolist())
        lab = session["labels"]
        print(f"  Validation vs {os.path.basename(reference_paths[0])} (not used for detection):")
        print("    elements that changed vs reference (our rank): "
              + (", ".join(f"{lab[i]} ({'low signal, not ranked' if i in na else f'#{r}'})"
                           for i, r in v["rank_of_changed"].items()) or "none"))
        print(f"    changed and in our top {TOP_K}: {[lab[i] for i in v['confirmed']] or 'none'}")
        summary[name]["validation"] = {
            "changed_with_rank": {lab[i]: r for i, r in v["rank_of_changed"].items()},
            **{k: [lab[i] for i in v[k]] for k in ("confirmed", "missed", "unconfirmed")}}

    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, f"{name}_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nOutputs written to {output_dir}")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scans", nargs="+", help=".dat files of ONE coil state (e.g. MPRAGE + SNR map)")
    ap.add_argument("--validate-against", nargs="+", help="reference .dat file(s), only used to check results")
    ap.add_argument("--inject-dead", help="simulate a dead element, e.g. HEAD128_5304:L07")
    ap.add_argument("--output-dir", default=OUTPUT_DIR)
    a = ap.parse_args()
    run(a.scans, a.validate_against, a.inject_dead, a.output_dir)
