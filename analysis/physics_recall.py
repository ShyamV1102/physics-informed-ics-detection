#!/usr/bin/env python3
"""
Physics recall check across attack detection CSVs.

Latency is measured from ATTACK ONSET, not from the first row of the file.
Onsets and their provenance live in run_onsets.json alongside this script.

For each detection CSV, report:
  - whether the detector alarmed (recall 1 or 0)
  - detection latency from attack onset to the first alarming row
  - false alarms in the pre-onset (clean) part of the run
  - peak |z| and peak CUSUM per stream, against the h thresholds

Run in WSL over your attack CSVs. Example:
  python3 physics_recall.py detection_*.csv

If a file has no onset in run_onsets.json, latency is reported as "no onset"
rather than silently measured from the start of the file.
"""

import json
import os
import sys

import pandas as pd

# h thresholds from the locked detector parameters
H_PRODUCT = 5.0
H_PURGE = 6.0

ONSET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "evaluation", "run_onsets.json")


def load_onsets():
    """Load onset ground truth. Missing file is not fatal."""
    try:
        with open(ONSET_FILE) as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        print(f"WARNING: {ONSET_FILE} not found, no latency will be reported\n")
        return {}

    out = {}
    for key, val in raw.items():
        if key.startswith("_"):
            continue
        onset = val.get("onset")
        out[key] = {
            "onset": pd.to_datetime(onset) if onset else None,
            "basis": val.get("basis", "unknown"),
            "rate": val.get("rate"),
            "note": val.get("note"),
        }
    return out


def check(path, onsets):
    df = pd.read_csv(path, skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]

    have_product = "productAlarm" in df.columns
    have_purge = "purgeAlarm" in df.columns

    t = pd.to_datetime(df["timestamp"], format="ISO8601")
    n = len(df)
    span = (t.iloc[-1] - t.iloc[0]) if n > 1 else pd.Timedelta(0)

    name = os.path.basename(path)
    meta = onsets.get(name, {"onset": None, "basis": "unknown", "rate": None, "note": None})
    onset = meta["onset"]

    prod_alarms = int(df["productAlarm"].sum()) if have_product else 0
    purge_alarms = int(df["purgeAlarm"].sum()) if have_purge else 0
    detected = (prod_alarms + purge_alarms) > 0

    alarm_mask = pd.Series(False, index=df.index)
    if have_product:
        alarm_mask |= df["productAlarm"].astype(bool)
    if have_purge:
        alarm_mask |= df["purgeAlarm"].astype(bool)

    # Split the run at onset. Alarms before onset are false alarms even in an
    # attack run, because the attack had not started yet.
    if onset is not None:
        pre = t < onset
        pre_rows = int(pre.sum())
        pre_alarms = int(alarm_mask[pre].sum())
        post_rows = n - pre_rows
        post_alarms = int(alarm_mask[~pre].sum())
    else:
        pre_rows = pre_alarms = None
        post_rows = n
        post_alarms = int(alarm_mask.sum())

    # Latency from onset to the first alarm at or after onset.
    latency_s = None
    if detected and onset is not None:
        after = alarm_mask & (t >= onset)
        if after.any():
            first_idx = after.idxmax()
            latency_s = (t.iloc[first_idx] - onset).total_seconds()

    def peak(col):
        return df[col].max() if col in df.columns else float("nan")

    peak_pz = df["productZ"].abs().max() if "productZ" in df.columns else float("nan")
    peak_uz = df["purgeZ"].abs().max() if "purgeZ" in df.columns else float("nan")

    # SLow is stored as a negative-going quantity, so its magnitude is the
    # low-side CUSUM. Taking max(SHigh, SLow) directly would always return
    # SHigh and hide every low-side detection.
    def pk(stream):
        hi = df[stream + "SHigh"].max() if stream + "SHigh" in df.columns else float("nan")
        lo = df[stream + "SLow"].min() if stream + "SLow" in df.columns else float("nan")
        vals = [v for v in (hi, abs(lo) if lo == lo else lo) if v == v]
        return max(vals) if vals else float("nan")

    peak_pcusum = pk("product")
    peak_ucusum = pk("purge")

    # Which side fired. Drift pushes the residual up, command injection pushes
    # it down, so this is a useful discriminator for the dashboard.
    def side(stream):
        if stream + "SHigh" not in df.columns:
            return "-"
        hi = df[stream + "SHigh"].max()
        lo = abs(df[stream + "SLow"].min())
        if max(hi, lo) < 1e-9:
            return "-"
        return "high" if hi >= lo else "low"

    return {
        "file": name,
        "rows": n,
        "span_s": round(span.total_seconds(), 1),
        "basis": meta["basis"],
        "rate": meta["rate"],
        "note": meta["note"],
        "has_onset": onset is not None,
        "prod_alarms": prod_alarms,
        "purge_alarms": purge_alarms,
        "detected": detected,
        "pre_rows": pre_rows,
        "pre_alarms": pre_alarms,
        "post_rows": post_rows,
        "post_alarms": post_alarms,
        "latency_s": round(latency_s, 1) if latency_s is not None else None,
        "peak_pz": round(peak_pz, 2),
        "peak_uz": round(peak_uz, 2),
        "peak_pcusum_vs_h": f"{peak_pcusum:.2f} / {H_PRODUCT}",
        "peak_ucusum_vs_h": f"{peak_ucusum:.2f} / {H_PURGE}",
        "side_p": side("product"),
        "side_u": side("purge"),
        "is_clean": meta["basis"] == "clean",
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    onsets = load_onsets()

    results = []
    for path in sys.argv[1:]:
        try:
            results.append(check(path, onsets))
        except Exception as e:
            print(f"ERROR reading {path}: {e}")

    if not results:
        return

    for r in results:
        print("=" * 66)
        print(r["file"])
        print(f"  rows {r['rows']}  span {r['span_s']}s  onset basis: {r['basis']}")
        print(f"  product alarms {r['prod_alarms']}   purge alarms {r['purge_alarms']}")

        if r["is_clean"]:
            n_alarm = r["prod_alarms"] + r["purge_alarms"]
            if n_alarm:
                print(f"  CLEAN RUN: {n_alarm} FALSE ALARMS in {r['rows']} rows "
                      f"({100.0*n_alarm/r['rows']:.3f}%)")
            else:
                print(f"  CLEAN RUN: no alarms in {r['rows']} rows, as expected")
        elif r["detected"]:
            if r["latency_s"] is not None:
                print(f"  DETECTED: True   latency {r['latency_s']}s from onset")
            elif r["has_onset"]:
                print("  DETECTED: True   but all alarms fall BEFORE onset, check labels")
            else:
                print("  DETECTED: True   latency unavailable (no onset recorded)")
        else:
            print("  DETECTED: False  (physics blind to this attack)")

        if r["pre_rows"] is not None:
            print(f"  pre-onset  {r['pre_rows']:5d} rows, {r['pre_alarms']} alarms (false)")
            print(f"  post-onset {r['post_rows']:5d} rows, {r['post_alarms']} alarms")

        print(f"  peak |z|  product {r['peak_pz']}   purge {r['peak_uz']}")
        print(f"  peak CUSUM vs h  product {r['peak_pcusum_vs_h']} ({r['side_p']})"
              f"   purge {r['peak_ucusum_vs_h']} ({r['side_u']})")
        if r["note"]:
            print(f"  NOTE: {r['note']}")

    print("\n" + "=" * 66)
    print("PHYSICS RECALL SUMMARY  (latency measured from attack onset)")
    print("=" * 66)
    hdr = f"{'file':<38}{'detect':<8}{'latency':<12}{'basis':<11}{'peak z (p/u)':<16}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        if r["is_clean"]:
            det = "CLEAN" if not r["detected"] else "CLEAN*"
            lat = "-"
        elif not r["detected"]:
            det, lat = "BLIND", "-"
        elif r["latency_s"] is not None:
            det, lat = "YES", f"{r['latency_s']}s"
        else:
            det, lat = "YES", "no onset"
        pz = f"{r['peak_pz']}/{r['peak_uz']}"
        print(f"{r['file']:<38}{det:<8}{lat:<12}{r['basis']:<11}{pz:<16}")

    unknown = [r["file"] for r in results if r["basis"] == "unknown"]
    if unknown:
        print("\nNot listed in run_onsets.json:")
        for f in unknown:
            print("  " + f)


if __name__ == "__main__":
    main()