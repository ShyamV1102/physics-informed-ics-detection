#!/usr/bin/env python3
"""
Detection metrics for the physics arm.

Two scoring modes, both reported:

PER-EVENT (headline): each attack run is one event. Detected if the injected
  stream alarms at least once inside the attack window. This answers "was the
  attack caught" and pairs with detection latency. Recall, precision, F1 and
  accuracy are computed over the set of runs.

PER-WINDOW (false-alarm rate): each 1-second row is a sample. Used to report
  the false-positive rate on clean runs and to build the PR curve. A row inside
  an attack window with an alarm is a true positive; a clean row with an alarm
  is a false positive.

Onsets and provenance come from run_onsets.json. Detection is judged on the
INJECTED stream: product for drift and false sensor, either stream for command
injection (manual mode disturbs both). Purge false alarms on long product-drift
runs are excluded from the detection decision, matching physics_recall.py.

PR-AUC is computed per-window by sweeping the CUSUM threshold h over the stored
SHigh / |SLow| values, so it needs no re-running of attacks.

Usage:
  python3 metrics.py detection_*.csv
Expects run_onsets.json in the same directory.
"""

import json
import os
import sys

import numpy as np
import pandas as pd

ONSET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evaluation/run_onsets.json")

# Which stream carries the injected signal for each attack type.
# "product" -> judge product only. "both" -> either stream counts.
INJECTED_STREAM = {
    "drift": "product",
    "false_sensor": "product",
    "cmd_manual_setpoint": "both",
    "cmd_manual_aggressive": "both",
    "cmd_manual_sweep": "both",
    "cmd_override_inauto": "both",
    "replay": "both",
}


def load_onsets():
    with open(ONSET_FILE) as fh:
        raw = json.load(fh)
    out = {}
    for k, v in raw.items():
        if k.startswith("_"):
            continue
        out[k] = {
            "onset": pd.to_datetime(v["onset"]).tz_localize("UTC")
            if v.get("onset") and pd.to_datetime(v["onset"]).tzinfo is None
            else (pd.to_datetime(v["onset"]) if v.get("onset") else None),
            "offset": pd.to_datetime(v["offset"]).tz_localize("UTC")
            if v.get("offset") and pd.to_datetime(v["offset"]).tzinfo is None
            else (pd.to_datetime(v["offset"]) if v.get("offset") else None),
            "basis": v.get("basis", "unknown"),
            "attack_type": v.get("attack_type"),
            "rate": v.get("rate"),
        }
    return out


def attack_class(meta, fname):
    """Map a run to an attack class key for INJECTED_STREAM."""
    at = meta.get("attack_type")
    if at:
        return at
    # fall back to filename hints
    n = fname.lower()
    if "drift" in n:
        return "drift"
    if "false_sensor" in n:
        return "false_sensor"
    if "replay" in n:
        return "replay"
    if "autosp" in n or "override" in n:
        return "cmd_override_inauto"
    if "manual_setpoint" in n:
        return "cmd_manual_setpoint"
    if "aggressive" in n:
        return "cmd_manual_aggressive"
    if "sweep" in n:
        return "cmd_manual_sweep"
    return None


def alarm_series(df, stream_choice):
    """Boolean alarm series for the chosen injected stream(s)."""
    prod = df["productAlarm"].astype(bool) if "productAlarm" in df.columns else pd.Series(False, index=df.index)
    purge = df["purgeAlarm"].astype(bool) if "purgeAlarm" in df.columns else pd.Series(False, index=df.index)
    if stream_choice == "product":
        return prod
    if stream_choice == "purge":
        return purge
    return prod | purge


def load_run(path, onsets):
    name = os.path.basename(path)
    df = pd.read_csv(path, skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    t = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
    meta = onsets.get(name, {"onset": None, "offset": None, "basis": "unknown", "attack_type": None})
    cls = attack_class(meta, name)
    is_clean = meta["basis"] == "clean"
    stream = INJECTED_STREAM.get(cls, "both")
    alarms = alarm_series(df, stream)

    onset = meta["onset"]
    offset = meta["offset"]
    # attack window mask: onset to offset if both known, onset to end if only onset,
    # empty for clean runs
    if is_clean or onset is None:
        in_window = pd.Series(False, index=df.index)
    elif offset is not None:
        in_window = (t >= onset) & (t <= offset)
    else:
        in_window = t >= onset

    return {
        "name": name,
        "df": df,
        "t": t,
        "alarms": alarms,
        "in_window": in_window,
        "is_clean": is_clean,
        "basis": meta["basis"],
        "cls": cls,
        "rate": meta.get("rate"),
        "onset": onset,
        "stream": stream,
    }


def per_event(runs):
    """Each attack run = one event. Clean runs are the negative class."""
    tp = fp = tn = fn = 0
    rows = []
    for r in runs:
        if r["is_clean"]:
            # negative event: any alarm at all is a false positive event
            fired = bool(r["alarms"].any())
            if fired:
                fp += 1
                verdict = "FP (clean alarmed)"
            else:
                tn += 1
                verdict = "TN"
        else:
            # positive event: alarm inside the window = detected
            if r["onset"] is None:
                # blind-by-design run with no onset: detection = any alarm at all
                detected = bool(r["alarms"].any())
            else:
                detected = bool((r["alarms"] & r["in_window"]).any())
            if detected:
                tp += 1
                verdict = "TP"
            else:
                fn += 1
                verdict = "FN (missed)"
        rows.append((r["name"], r["cls"], verdict))
    return tp, fp, tn, fn, rows


def per_window(runs):
    """Each 1s row = one sample. Attack-window rows are positives."""
    tp = fp = tn = fn = 0
    for r in runs:
        a = r["alarms"].values
        w = r["in_window"].values
        tp += int((a & w).sum())
        fn += int((~a & w).sum())
        fp += int((a & ~w).sum())
        tn += int((~a & ~w).sum())
    return tp, fp, tn, fn


def prf(tp, fp, tn, fn):
    prec = tp / (tp + fp) if (tp + fp) else float("nan")
    rec = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) and prec == prec and rec == rec else float("nan")
    acc = (tp + tn) / (tp + fp + tn + fn) if (tp + fp + tn + fn) else float("nan")
    return prec, rec, f1, acc


def pr_auc(runs):
    """
    Per-window PR curve by sweeping a single CUSUM threshold across both streams.
    Score for a row = max(productSHigh, |productSLow|, purgeSHigh, |purgeSLow|).
    Label = row is inside an attack window on the injected stream.
    """
    scores = []
    labels = []
    for r in runs:
        df = r["df"]

        def col(c):
            return df[c].values if c in df.columns else np.zeros(len(df))

        s = np.maximum.reduce([
            col("productSHigh"), np.abs(col("productSLow")),
            col("purgeSHigh"), np.abs(col("purgeSLow")),
        ])
        scores.append(s)
        labels.append(r["in_window"].values.astype(int))

    scores = np.concatenate(scores)
    labels = np.concatenate(labels)
    if labels.sum() == 0:
        return float("nan")

    order = np.argsort(-scores)
    labels = labels[order]
    tp = np.cumsum(labels)
    fp = np.cumsum(1 - labels)
    total_pos = labels.sum()
    prec = tp / (tp + fp)
    rec = tp / total_pos
    # integrate precision over recall (trapezoid), prepend recall 0
    rec = np.concatenate([[0.0], rec])
    prec = np.concatenate([[1.0], prec])
    # np.trapz renamed to np.trapezoid in newer numpy; support both
    trap = getattr(np, "trapezoid", None) or getattr(np, "trapz")
    return float(trap(prec, rec))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    onsets = load_onsets()
    runs = []
    for p in sys.argv[1:]:
        try:
            runs.append(load_run(p, onsets))
        except Exception as e:
            print(f"ERROR {p}: {e}")

    attacks = [r for r in runs if not r["is_clean"]]
    cleans = [r for r in runs if r["is_clean"]]

    # ---------- PER-EVENT ----------
    tp, fp, tn, fn, rows = per_event(runs)
    prec, rec, f1, acc = prf(tp, fp, tn, fn)

    print("=" * 60)
    print("PER-EVENT  (each run = one event; clean runs = negatives)")
    print("=" * 60)
    for name, cls, verdict in rows:
        print(f"  {name:<42}{cls or '-':<22}{verdict}")
    print(f"\n  TP {tp}  FP {fp}  TN {tn}  FN {fn}")
    print(f"  precision {prec:.3f}  recall {rec:.3f}  F1 {f1:.3f}  accuracy {acc:.3f}")

    # ---------- PER-WINDOW ----------
    wtp, wfp, wtn, wfn = per_window(runs)
    wprec, wrec, wf1, wacc = prf(wtp, wfp, wtn, wfn)

    # clean-only FPR
    ctp, cfp, ctn, cfn = per_window(cleans)
    clean_rows = cfp + ctn
    clean_fpr = cfp / clean_rows if clean_rows else float("nan")

    print("\n" + "=" * 60)
    print("PER-WINDOW  (each 1s row = one sample)")
    print("=" * 60)
    print(f"  TP {wtp}  FP {wfp}  TN {wtn}  FN {wfn}")
    print(f"  precision {wprec:.3f}  recall {wrec:.3f}  F1 {wf1:.3f}")
    print(f"  clean-run FPR: {cfp} alarms in {clean_rows} clean rows = {100*clean_fpr:.3f}%")

    # ---------- PR-AUC ----------
    auc = pr_auc(runs)
    print("\n" + "=" * 60)
    print("PR-AUC  (per-window, CUSUM threshold sweep)")
    print("=" * 60)
    print(f"  PR-AUC = {auc:.4f}")

    print("\nNotes:")
    print("  - Detection judged on the injected stream per attack type.")
    print("  - Per-event recall is the headline; per-window FPR is the")
    print("    false-alarm figure; PR-AUC sweeps the CUSUM threshold.")


if __name__ == "__main__":
    main()
