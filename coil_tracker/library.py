"""Coil library: one folder per coil, one small fingerprint file per saved scan.

Layout (no database, so it is safe on a shared network drive and easy to back up):

    <library>/
        coils/<coil-id>/coil.json           name, expected elements, checked elements
        coils/<coil-id>/scans/<scan-id>.npz fingerprint (see fingerprint.py)
        coils/<coil-id>/scans/<scan-id>.json date, files, notes, baseline flag, status

Baselines: scans flagged "baseline". "Reset baseline" (after a repair or retune) unflags all
earlier baselines so only the new one counts. The reference is the median of the flagged scans
of the same map type (with or without MPRAGE) when there are any, otherwise all of them.
"""
import json
import os
import re
import shutil
import uuid
from datetime import datetime

from coil_tracker import fingerprint as fpm, checks

MATCH_MIN = 0.5            # share of a coil's elements a scan must contain to match that coil


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:40] or "coil"
    return f"{s}-{uuid.uuid4().hex[:6]}"


def _write_json(path: str, data: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def scan_sort_key(s: dict):
    return (s.get("date") or "", s.get("saved") or "")


class Library:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(os.path.join(root, "coils"), exist_ok=True)
        info = os.path.join(root, "library.json")
        if not os.path.exists(info):
            _write_json(info, {"created": _now(), "format_version": fpm.FORMAT_VERSION})

    # ── Coils ────────────────────────────────────────────────────────────────
    def _coil_dir(self, coil_id: str) -> str:
        return os.path.join(self.root, "coils", coil_id)

    def coils(self) -> list:
        out = []
        base = os.path.join(self.root, "coils")
        for d in sorted(os.listdir(base)):
            p = os.path.join(base, d, "coil.json")
            if os.path.exists(p):
                c = _read_json(p)
                c["id"] = d
                out.append(c)
        return sorted(out, key=lambda c: c["name"].lower())

    def coil(self, coil_id: str) -> dict:
        c = _read_json(os.path.join(self._coil_dir(coil_id), "coil.json"))
        c["id"] = coil_id
        return c

    def save_coil(self, coil: dict):
        data = {k: v for k, v in coil.items() if k != "id"}
        _write_json(os.path.join(self._coil_dir(coil["id"]), "coil.json"), data)

    def create_coil(self, name: str, fp: dict, description: str = "") -> dict:
        coil_id = _slug(name)
        os.makedirs(os.path.join(self._coil_dir(coil_id), "scans"))
        coil = {"id": coil_id, "name": name, "description": description, "created": _now(),
                "elements": list(fp["labels"]), "coil_keys": fp.get("coil_keys", {}),
                "checked": {}, "events": [{"date": _now(), "event": "coil created"}]}
        self.save_coil(coil)
        return coil

    def rename_coil(self, coil_id: str, name: str):
        c = self.coil(coil_id)
        c["name"] = name
        self.save_coil(c)

    def delete_coil(self, coil_id: str):
        shutil.rmtree(self._coil_dir(coil_id))

    def match_coil(self, fp: dict) -> list:
        """Coils this scan could belong to, best first: [(coil, share_of_coil_present, unknown_elements)]."""
        labels = set(fp["labels"])
        out = []
        for c in self.coils():
            exp = set(c["elements"])
            present = len(exp & labels) / max(len(exp), 1)
            unknown = sorted(labels - exp)
            if present >= MATCH_MIN:
                out.append((c, present, unknown))
        return sorted(out, key=lambda t: (len(t[2]), -t[1]))

    def set_checked(self, coil_id: str, label: str, note: str = "", checked: bool = True):
        c = self.coil(coil_id)
        if checked:
            c["checked"][label] = note or f"bench-checked {_now()[:10]}"
        else:
            c["checked"].pop(label, None)
        c["events"].append({"date": _now(), "event": f"{label} {'marked' if checked else 'unmarked'} as checked"})
        self.save_coil(c)

    # ── Scans ────────────────────────────────────────────────────────────────
    def _scan_dir(self, coil_id: str) -> str:
        return os.path.join(self._coil_dir(coil_id), "scans")

    def scans(self, coil_id: str) -> list:
        """Scan records (json), oldest first."""
        d = self._scan_dir(coil_id)
        out = []
        for f in os.listdir(d):
            if f.endswith(".json"):
                s = _read_json(os.path.join(d, f))
                s["id"] = f[:-5]
                out.append(s)
        return sorted(out, key=scan_sort_key)

    def load_fp(self, coil_id: str, scan_id: str) -> dict:
        return fpm.load(os.path.join(self._scan_dir(coil_id), scan_id + ".npz"))

    def add_scan(self, coil_id: str, fp: dict, report: dict, baseline: bool = False,
                 reset_baseline: bool = False) -> str:
        date = fp["meta"].get("date") or _now()
        scan_id = re.sub(r"[^0-9]", "", date)[:14] + "-" + uuid.uuid4().hex[:6]
        d = self._scan_dir(coil_id)
        fpm.save(fp, os.path.join(d, scan_id + ".npz"))
        rec = {"date": date, "saved": _now(), "baseline": bool(baseline or reset_baseline),
               "status": report["status"], "summary": report["summary"],
               "top": [e["short"] for e in report["top"]],
               "protocol": fp["meta"].get("protocol", ""), "files": fp["meta"].get("files", []),
               "maps_from_3d": fp["meta"].get("maps_from_3d"), "notes": fp["meta"].get("notes", ""),
               "operator": fp["meta"].get("operator", "")}
        _write_json(os.path.join(d, scan_id + ".json"), rec)
        if reset_baseline:
            self.reset_baseline(coil_id, scan_id)
        return scan_id

    def update_scan(self, coil_id: str, scan_id: str, **fields):
        p = os.path.join(self._scan_dir(coil_id), scan_id + ".json")
        rec = _read_json(p)
        rec.update(fields)
        _write_json(p, rec)

    def set_baseline(self, coil_id: str, scan_id: str, on: bool = True):
        self.update_scan(coil_id, scan_id, baseline=on)

    def reset_baseline(self, coil_id: str, scan_id: str):
        """Only this scan (and later baselines) count from now on, e.g. after a repair.

        Earlier baselines keep their flag, so re-opening an old report still compares it with
        the baseline it had at the time; they are shown as superseded.
        """
        self.update_scan(coil_id, scan_id, baseline=True, baseline_reset=True)
        c = self.coil(coil_id)
        c["events"].append({"date": _now(), "event": f"baseline reset to scan {scan_id}"})
        self.save_coil(c)

    def active_baselines(self, coil_id: str) -> set:
        """IDs of the baselines that count for a new scan (flagged, since the last reset)."""
        recs = self.scans(coil_id)
        last = max((i for i, s in enumerate(recs) if s.get("baseline_reset") and s["baseline"]), default=0)
        return {s["id"] for s in recs[last:] if s["baseline"]}

    def delete_scan(self, coil_id: str, scan_id: str):
        for ext in (".npz", ".json"):
            p = os.path.join(self._scan_dir(coil_id), scan_id + ext)
            if os.path.exists(p):
                os.remove(p)

    # ── Evaluation against the library ───────────────────────────────────────
    def context(self, coil_id: str, fp: dict, before: dict = None) -> dict:
        """Baselines and drift series for evaluating fp.

        before: a saved scan record; only scans saved up to that one are used (to re-open an
        old report as it was). None = a new scan, use everything in the library.
        """
        recs = self.scans(coil_id)
        if before is not None:
            recs = [s for s in recs if scan_sort_key(s) < scan_sort_key(before)]
        recs = [s for s in recs if os.path.exists(os.path.join(self._scan_dir(coil_id), s["id"] + ".npz"))]
        # baselines: flagged scans since the last reset
        last_reset = max((i for i, s in enumerate(recs) if s.get("baseline_reset") and s["baseline"]), default=0)
        since = recs[last_reset:]
        base_recs = [s for s in since if s["baseline"]]
        same_type = [s for s in base_recs if s.get("maps_from_3d") == fp["meta"].get("maps_from_3d")]
        base_recs = same_type or base_recs
        baselines = [self.load_fp(coil_id, s["id"]) for s in base_recs]
        # drift series: scans of the same map type from the first baseline onward, then this scan
        series = []
        if base_recs:
            first = scan_sort_key(base_recs[0])
            series = [self.load_fp(coil_id, s["id"]) for s in since
                      if scan_sort_key(s) >= first and s.get("maps_from_3d") == fp["meta"].get("maps_from_3d")]
        return {"baselines": baselines, "series": series + [fp], "baseline_records": base_recs}

    def evaluate(self, coil_id: str, fp: dict, before: dict = None) -> dict:
        c = self.coil(coil_id)
        ctx = self.context(coil_id, fp, before)
        rep = checks.evaluate(fp, expected=c["elements"], baselines=ctx["baselines"],
                              series=ctx["series"], checked=c["checked"])
        rep["coil"] = {"id": coil_id, "name": c["name"]}
        rep["baseline_dates"] = [s["date"] for s in ctx["baseline_records"]]
        if not fp["meta"].get("maps_from_3d", True):
            rep["alerts"].insert(0, "No MPRAGE in this scan: maps come from the SNR map only, which is "
                                    "less reliable at the edge of the phantom (e.g. the J sub-coil).")
        return rep
