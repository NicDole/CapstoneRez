"""From raw scan files to a fingerprint."""
import os

from coil_analysis import channel_fault_analysis as cfa
from coil_tracker import fingerprint as fpm


def analyse_files(snr_map: str, mprage: str = None, notes: str = "", operator: str = "",
                  progress=lambda msg: None) -> dict:
    """Load the SNR map (required: it has the noise-only readouts) and optionally an MPRAGE.

    The raw files are only read; nothing is written next to them.
    """
    paths = [p for p in (mprage, snr_map) if p]
    scans = []
    for i, p in enumerate(paths, 1):
        progress(f"Reading {os.path.basename(p)} ({i}/{len(paths)}), this can take a minute…")
        scans.append(cfa.load_scan(p))
    if scans[-1]["noise_cov"] is None and not any(s["noise_cov"] is not None for s in scans):
        raise ValueError("The SNR-map file has no noise-only readouts. Please pick the SNR-map scan.")
    progress("Measuring every element…")
    session = cfa.combine_scans(scans)
    return fpm.from_session(session, notes=notes, operator=operator)


def guess_pair(folder_or_file: str) -> dict:
    """Suggest SNR-map and MPRAGE files from a folder (or a file inside it) by file name."""
    folder = folder_or_file if os.path.isdir(folder_or_file) else os.path.dirname(folder_or_file)
    dats = sorted(f for f in os.listdir(folder) if f.lower().endswith(".dat"))
    snr = [f for f in dats if "snr" in f.lower()]
    mpr = [f for f in dats if "mprage" in f.lower() or "t1" in f.lower()]
    return {"snr": [os.path.join(folder, f) for f in snr], "mprage": [os.path.join(folder, f) for f in mpr]}
