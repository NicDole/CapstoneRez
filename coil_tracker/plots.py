"""Figures used by the app and the PDF report (matplotlib Figure objects, no pyplot)."""
import numpy as np
from matplotlib.figure import Figure

from coil_tracker import checks, fingerprint as fpm

LEVEL_COLORS = {"Fault": "#ED1B2F", "Watch": "#E39B2B", "Routine check": "#7D8796",
                "Low signal": "#BDBDBD"}


def layout_figure(fp: dict, report: dict, fig: Figure = None) -> Figure:
    """Element positions (from the sensitivity maps), coloured by suspicion; top 5 labelled."""
    fig = fig or Figure(figsize=(8, 4))
    fig.clear()
    cen = np.asarray(fp["centroid"], float)
    lr = fp["mirror_axis"]
    others = [a for a in range(cen.shape[1]) if a != lr]
    views = [(lr, a) for a in others][:2]
    by_label = {e["label"]: e for e in report["ranking"]}
    top = {e["label"]: i + 1 for i, e in enumerate(report["top"])}
    score = np.array([by_label[l]["score"] for l in fp["labels"]])
    vmax = max(np.percentile(score, 98), 1e-6)
    names = {0: "axis 1", 1: "axis 2", 2: "axis 3"}
    # label offsets fanned out so neighbouring suspects don't overlap
    offsets = [(8, 10), (8, -16), (-60, 10), (-60, -16), (8, 24)]
    for k, (ax_x, ax_y) in enumerate(views):
        ax = fig.add_subplot(1, len(views), k + 1)
        for i, l in enumerate(fp["labels"]):
            e = by_label[l]
            if e["low_signal"]:
                ax.scatter(cen[i, ax_x], cen[i, ax_y], s=40, facecolor="none", edgecolor="#bdbdbd", lw=1)
            else:
                ax.scatter(cen[i, ax_x], cen[i, ax_y], s=40 + 160 * min(score[i] / vmax, 1),
                           color=LEVEL_COLORS[e["level"]], alpha=0.35 + 0.65 * min(score[i] / vmax, 1),
                           edgecolor="white", lw=0.8)
            if l in top:
                ax.annotate(f"#{top[l]} {e['short']}", (cen[i, ax_x], cen[i, ax_y]),
                            xytext=offsets[top[l] - 1], textcoords="offset points", fontsize=9, weight="bold",
                            arrowprops=dict(arrowstyle="-", color="#888888", lw=0.6))
        ax.set_xlabel("left–right")
        ax.set_ylabel(names[ax_y])
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values():
            s.set_color("#dddddd")
    fig.suptitle("Bigger, stronger colour = more suspicious · red Fault · amber Watch · hollow = low signal",
                 fontsize=10)
    fig.tight_layout()
    return fig


def element_history(fps: list, label: str, baseline_index: int = 0) -> dict:
    """Per-scan values of one element, relative to the coil median of each scan (so whole-coil
    changes cancel), as % change vs the reference scan."""
    out = {"signal": [], "noise": [], "snr": [], "tilt": []}
    for fp in fps:
        if label not in fp["labels"]:
            for v in out.values():
                v.append(np.nan)
            continue
        i = fp["labels"].index(label)
        f = fpm.features(fp)
        out["signal"].append(np.log(f["signal"][i] / np.median(f["signal"])))
        out["noise"].append(np.log(f["noise_std"][i] / np.median(f["noise_std"])))
        out["snr"].append(out["signal"][-1] - out["noise"][-1])
        out["tilt"].append(checks.corrected_tilt(fp["tilt"], f["parity"])[i] if fp.get("tilt") is not None else np.nan)
    res = {}
    for k, v in out.items():
        v = np.array(v, float)
        ref = v[baseline_index] if np.isfinite(v[baseline_index]) else np.nanmedian(v)
        res[k] = (v - ref) * 100 if k == "tilt" else (np.exp(v - ref) - 1) * 100
    return res


def history_figure(fps: list, dates: list, label: str, baseline_index: int, fig: Figure = None) -> Figure:
    fig = fig or Figure(figsize=(8, 3))
    fig.clear()
    h = element_history(fps, label, baseline_index)
    ax = fig.add_subplot(1, 1, 1)
    x = np.arange(len(fps))
    for k, c, name in (("noise", "#ED1B2F", "noise"), ("snr", "#1F1F1F", "SNR"), ("signal", "#A3A8B0", "signal"),
                       ("tilt", "#E39B2B", "tuning tilt (points)")):
        ax.plot(x, h[k], marker="o", ms=4, lw=1.6 if k != "signal" else 1, color=c, label=name)
    ax.axhline(0, color="#cccccc", lw=1)
    ax.axvline(baseline_index, color="#1E6B34", ls=":", lw=1.2)
    ax.set_xticks(x)
    ax.set_xticklabels([d[2:16] for d in dates], rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("% change vs baseline")
    ax.set_title(f"{label.split(':')[-1]} over time (relative to the rest of the coil; dotted line = baseline)",
                 fontsize=10)
    ax.legend(fontsize=8, ncol=4, loc="best", frameon=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    return fig
