#!/usr/bin/env python3
"""
load_runs.py - load GRFICS physics-detector runs into Postgres for Grafana.

Reads, for each run:
    detection_<run_id>.csv   expected / residual / z / CUSUM / alarm per stream
    raw_<run_id>.csv         raw register values (valve, flow, setpoints)
    run_onsets.json          ground-truth onset + basis + rate

Writes:
    runs      one row per run
    process   one row per (timestamp, stream), long format
    alerts    one row per alarm sample per side that breached

Usage:
    python load_runs.py --data-dir . --dry-run
    python load_runs.py --data-dir .
    python load_runs.py --data-dir . --only drift_0005 baseline

Re-running a run deletes and reloads that run only. Safe to repeat.
"""

import argparse
import glob
import json
import os
import re
import sys

import pandas as pd

# ---------------------------------------------------------------- locked facts

PARAMS = {
    "product": {"m": 2.12546546, "c": -0.00309158, "sigma": 0.07179686,
                "slack": 0.75, "h": 5.0},
    "purge":   {"m": 4.48571184, "c": 0.00409840, "sigma": 0.08653334,
                "slack": 0.5,  "h": 6.0},
}

VALVE_FULL_SCALE = 100.0
FLOW_FULL_SCALE = 500.0
REG_MAX = 65535.0
UNSET_SP = 65535          # manual setpoints park here when not in use

# PollGuard, mirrors the C# detector
NOMINAL_DT = 1.0
DT_MULTIPLIER = 1.5
RECOVERY_SAMPLES = 5

# raw register column per stream
RAW_COLS = {
    "product": {"valve": "ProductValvePosReg", "flow": "ProductFlowReg",
                "manual_sp": "ProductManualSp"},
    "purge":   {"valve": "PurgeValvePosReg",   "flow": "PurgeFlowReg",
                "manual_sp": "PurgeManualSp"},
}

DB = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", 5432)),
    "user": os.environ.get("PGUSER", "ics"),
    "password": os.environ.get("PGPASSWORD", "icsdash"),
    "dbname": os.environ.get("PGDATABASE", "icsdash"),
}


# ------------------------------------------------------------ run metadata

def classify(run_id, meta):
    """Return label, attack_type, attack_variant from the run id."""
    name = run_id.split("_", 1)[1] if "_" in run_id else run_id

    if name.startswith("baseline"):
        return "clean", None, None
    if name.startswith("false_sensor"):
        return "attack", "false_sensor", None
    if name.startswith("replay"):
        return "attack", "replay", None
    if name.startswith("drift"):
        rate = meta.get("rate")
        variant = f"{rate} sigma/s" if rate is not None else None
        return "attack", "covert_drift", variant
    if name.startswith("cmd_"):
        variant = {
            "cmd_autosp": "in_auto_override_sp",
            "cmd_manual_setpoint": "manual_setpoint",
            "cmd_aggressive": "manual_aggressive",
            "cmd_manual_sweep": "manual_sweep",
        }.get(name, name)
        return "attack", "command_injection", variant
    return "attack", name, None


def to_epoch(series):
    return pd.to_datetime(series, format="mixed", utc=True).astype("int64") / 1e9


# ------------------------------------------------------------ transform

def read_csv_clean(path):
    df = pd.read_csv(path, skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    return df


def poll_excluded_flags(ts_epoch):
    """PollGuard: exclude a sample if the gap before it was too long,
    then exclude the next RECOVERY_SAMPLES samples too."""
    dt = ts_epoch.diff()
    late = dt > (NOMINAL_DT * DT_MULTIPLIER)
    excluded = late.copy().fillna(False)
    idx = list(excluded.index)
    for i, pos in enumerate(idx):
        if late.iloc[i]:
            for j in range(i + 1, min(i + 1 + RECOVERY_SAMPLES, len(idx))):
                excluded.iloc[j] = True
    return dt, excluded


def scale_sp(raw):
    if pd.isna(raw) or int(raw) == UNSET_SP:
        return None
    return float(raw) / REG_MAX * FLOW_FULL_SCALE


def build_run(run_id, det_path, raw_path):
    det = read_csv_clean(det_path)
    det["ts_epoch"] = to_epoch(det["timestamp"])
    det = det.sort_values("ts_epoch").reset_index(drop=True)

    raw = None
    join_mode = "no raw file"
    if raw_path and os.path.exists(raw_path):
        raw = read_csv_clean(raw_path)
        raw["ts_epoch"] = to_epoch(raw["timestamp"])
        raw = raw.sort_values("ts_epoch").reset_index(drop=True)
        exact = det["ts_epoch"].isin(set(raw["ts_epoch"]))
        if exact.mean() >= 0.99:
            join_mode = "exact"
            raw_idx = raw.set_index("ts_epoch")
            merged = det.join(raw_idx.drop(columns=["timestamp"]), on="ts_epoch")
        else:
            join_mode = "nearest (asof, 0.5 s)"
            merged = pd.merge_asof(
                det, raw.drop(columns=["timestamp"]),
                on="ts_epoch", direction="nearest",
                tolerance=0.5,
            )
        det = merged

    dt, excluded = poll_excluded_flags(det["ts_epoch"])

    process_rows = []
    alert_rows = []

    for stream in ("product", "purge"):
        p = PARAMS[stream]
        pref = stream  # column prefix in the detection csv
        cols = RAW_COLS[stream]

        for i in range(len(det)):
            r = det.iloc[i]

            valve_raw = r.get(cols["valve"])
            flow_raw = r.get(cols["flow"])
            valve = (float(valve_raw) / REG_MAX * VALVE_FULL_SCALE
                     if pd.notna(valve_raw) else None)
            flow = (float(flow_raw) / REG_MAX * FLOW_FULL_SCALE
                    if pd.notna(flow_raw) else None)

            s_high = float(r[f"{pref}SHigh"])
            s_low = float(r[f"{pref}SLow"])
            alarm = bool(int(r[f"{pref}Alarm"]))
            ts = float(r["ts_epoch"])

            process_rows.append((
                run_id, ts, stream,
                int(valve_raw) if pd.notna(valve_raw) else None, valve,
                int(flow_raw) if pd.notna(flow_raw) else None, flow,
                float(r[f"{pref}Expected"]),
                float(r[f"{pref}Residual"]),
                float(r[f"{pref}Z"]),
                s_high, s_low, p["h"],
                scale_sp(r.get(cols["manual_sp"])),
                None if pd.isna(dt.iloc[i]) else float(dt.iloc[i]),
                bool(excluded.iloc[i]),
                alarm,
            ))

            if not alarm:
                continue

            if s_high >= p["h"]:
                alert_rows.append((
                    run_id, ts, "physics", "cusum_high", stream, "alarm",
                    s_high, p["h"],
                    f"{stream} flow above expected: CUSUM high {s_high:.2f} "
                    f"over threshold {p['h']:.1f}, residual {r[f'{pref}Z']:.2f} sigma",
                ))
            if abs(s_low) >= p["h"]:
                alert_rows.append((
                    run_id, ts, "physics", "cusum_low", stream, "alarm",
                    abs(s_low), p["h"],
                    f"{stream} flow below expected: CUSUM low {abs(s_low):.2f} "
                    f"over threshold {p['h']:.1f}, residual {r[f'{pref}Z']:.2f} sigma",
                ))

    return process_rows, alert_rows, join_mode, len(det)


# ------------------------------------------------------------ database

PROCESS_COLS = ("run_id, ts_epoch, stream, valve_raw, valve, flow_raw, flow, "
                "flow_expected, residual, residual_sigma, cusum_high, cusum_low, "
                "threshold_h, setpoint, dt_seconds, poll_excluded, alarm")

ALERT_COLS = ("run_id, ts_epoch, source, rule, stream, severity, value, "
              "threshold, detail")


def load(conn, run_id, run_meta, process_rows, alert_rows):
    from psycopg2.extras import execute_values
    cur = conn.cursor()
    cur.execute("DELETE FROM alerts WHERE run_id = %s", (run_id,))
    cur.execute("DELETE FROM process WHERE run_id = %s", (run_id,))
    cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
    cur.execute(
        "INSERT INTO runs (run_id, label, attack_type, attack_variant, "
        "attack_onset_epoch, attack_offset_epoch, vantage, notes) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", run_meta)
    execute_values(cur, f"INSERT INTO process ({PROCESS_COLS}) VALUES %s",
                   process_rows, page_size=5000)
    execute_values(cur, f"INSERT INTO alerts ({ALERT_COLS}) VALUES %s",
                   alert_rows, page_size=5000)
    conn.commit()
    cur.close()


# ------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=".")
    ap.add_argument("--onsets", default=None,
                    help="path to run_onsets.json (default: <data-dir>/run_onsets.json)")
    ap.add_argument("--only", nargs="*", default=None,
                    help="load only runs whose id contains one of these strings")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse and report, do not touch the database")
    args = ap.parse_args()

    onsets_path = args.onsets or os.path.join(args.data_dir, "run_onsets.json")
    with open(onsets_path) as f:
        onsets = json.load(f)

    det_files = sorted(glob.glob(os.path.join(args.data_dir, "detection_*.csv")))
    if not det_files:
        sys.exit(f"no detection_*.csv found in {args.data_dir}")

    conn = None
    if not args.dry_run:
        import psycopg2
        conn = psycopg2.connect(**DB)

    total_p = total_a = 0
    for det_path in det_files:
        base = os.path.basename(det_path)
        run_id = re.sub(r"^detection_|\.csv$", "", base)
        if args.only and not any(k in run_id for k in args.only):
            continue

        meta = onsets.get(base, {})
        if not meta:
            print(f"WARN {run_id}: no entry in run_onsets.json, loading with no onset")

        label, atype, variant = classify(run_id, meta)
        onset = meta.get("onset")
        onset_epoch = (pd.Timestamp(onset).timestamp() if onset else None)
        notes_bits = [f"basis={meta.get('basis', 'unknown')}"]
        if meta.get("rate") is not None:
            notes_bits.append(f"rate={meta['rate']} sigma/s")

        raw_path = os.path.join(args.data_dir, f"raw_{run_id}.csv")
        process_rows, alert_rows, join_mode, nrows = build_run(
            run_id, det_path, raw_path)

        run_meta = (run_id, label, atype, variant, onset_epoch, None,
                    "poller", "; ".join(notes_bits))

        print(f"{run_id:34s} rows={nrows:5d} process={len(process_rows):6d} "
              f"alerts={len(alert_rows):5d} raw-join={join_mode}")

        total_p += len(process_rows)
        total_a += len(alert_rows)

        if not args.dry_run:
            load(conn, run_id, run_meta, process_rows, alert_rows)

    print(f"\ntotal process rows {total_p}, alert rows {total_a}")
    if args.dry_run:
        print("dry run, nothing written")
    else:
        conn.close()
        print("committed")


if __name__ == "__main__":
    main()