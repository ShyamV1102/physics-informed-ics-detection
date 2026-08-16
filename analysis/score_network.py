#!/usr/bin/env python3
"""
Network-only detection scoring for the physics-vs-network comparison arm.

Reads Zeek modbus.log files, scores context-aware rules R1-R5 against the
locked baseline normal set, and reports:
  - network false-positive rate on the clean baseline (pooled and per hour)
  - detection latency on each attack log (first alarm after first attack packet)

Decision unit is a 1-second bin, to match the physics detector's ~1 Hz
decision rate. At most one network alarm is emitted per bin.

Run in WSL where the zeek_* folders live. Example:
  python3 score_network.py \
    --baseline zeek_modbus_baseline_20260801_121641/modbus.log \
               zeek_modbus_baseline_20260801_131641/modbus.log \
               zeek_modbus_baseline_20260801_141641/modbus.log \
               zeek_modbus_baseline_20260801_151641/modbus.log \
    --attack autosp zeek_modbus_cmdinj_autosp/modbus.log \
    --attack subtle zeek_modbus_cmdinj_subtle/modbus.log
"""

import argparse
import math
from collections import defaultdict

# ---- Locked baseline normal set (from 3 clean hours) --------------------
# The PLC is reachable under two addresses depending on route:
#   OT subnet     192.168.95.2
#   Docker bridge 172.18.0.8
PLC_ADDRS = {"192.168.95.2", "172.18.0.8"}

# Hosts that legitimately originate Modbus in the baseline.
KNOWN_SOURCES = {"192.168.95.2", "192.168.95.3", "192.168.90.107"}

# Hosts that only ever READ in the baseline (never write).
READ_ONLY_SOURCES = {"192.168.95.3", "192.168.90.107"}

# Poller expected request rate band (requests per 1s bin).
# Poller sends 2 reads/sec (READ_INPUT + READ_HOLDING). Allow slack for jitter.
POLLER_SRC = "192.168.95.3"
POLLER_MIN_PER_BIN = 1     # below this = silence (possible replay/DoS)
POLLER_MAX_PER_BIN = 6     # above this = flooding

WRITE_FUNCS = {"WRITE_MULTIPLE_REGISTERS", "WRITE_SINGLE_REGISTER",
               "WRITE_MULTIPLE_COILS", "WRITE_SINGLE_COIL"}


def parse_modbus_log(path):
    """Yield REQ rows as dicts. Handles Zeek TSV with #fields header."""
    fields = None
    with open(path) as f:
        for line in f:
            if line.startswith("#fields"):
                fields = line.rstrip("\n").split("\t")[1:]
                continue
            if line.startswith("#"):
                continue
            if fields is None:
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) != len(fields):
                continue
            row = dict(zip(fields, parts))
            if row.get("pdu_type") != "REQ":
                continue
            yield row


def score_bins(rows):
    """
    Group rows into 1-second bins. For each bin, evaluate rules and record
    which rules fired and the earliest timestamp in the bin.
    Returns: dict bin_sec -> {"t": float, "rules": set, "poller_reqs": int}
    """
    bins = defaultdict(lambda: {"t": None, "rules": set(), "poller_reqs": 0})
    for row in rows:
        try:
            ts = float(row["ts"])
        except (KeyError, ValueError):
            continue
        b = int(math.floor(ts))
        rec = bins[b]
        if rec["t"] is None or ts < rec["t"]:
            rec["t"] = ts

        src = row.get("id.orig_h", "")
        dst = row.get("id.resp_h", "")
        func = row.get("func", "")
        is_write = func in WRITE_FUNCS

        # R1: any write directed at the PLC
        if is_write and dst in PLC_ADDRS:
            rec["rules"].add("R1")
        # R2: any source never seen in baseline
        if src not in KNOWN_SOURCES:
            rec["rules"].add("R2")
        # R3: a write from a host that only reads in baseline
        if is_write and src in READ_ONLY_SOURCES:
            rec["rules"].add("R3")
        # R5: a write from a host that never wrote in baseline
        #     (equivalent to R3 here, kept separate for the confusion matrix;
        #      generalises to unexpected function-from-source combos)
        if is_write and src in READ_ONLY_SOURCES:
            rec["rules"].add("R5")

        # Count poller requests for the R4 rate check
        if src == POLLER_SRC:
            rec["poller_reqs"] += 1
    return bins


def apply_rate_rule(bins):
    """R4: poller request rate outside expected band. Applied post-hoc so
    silence (zero poller requests) can also be detected on empty-ish bins."""
    if not bins:
        return
    lo = min(bins)
    hi = max(bins)
    for b in range(lo, hi + 1):
        rec = bins.get(b)
        reqs = rec["poller_reqs"] if rec else 0
        if reqs < POLLER_MIN_PER_BIN or reqs > POLLER_MAX_PER_BIN:
            if rec is None:
                bins[b] = {"t": float(b), "rules": {"R4"}, "poller_reqs": reqs}
            else:
                rec["rules"].add("R4")


def summarise(name, bins, rate_rule=True):
    if rate_rule:
        apply_rate_rule(bins)
    total_bins = len(bins)
    alarm_bins = [b for b, r in bins.items() if r["rules"]]
    per_rule = defaultdict(int)
    for b in alarm_bins:
        for rule in bins[b]["rules"]:
            per_rule[rule] += 1
    return {
        "name": name,
        "total_bins": total_bins,
        "alarm_bins": sorted(alarm_bins),
        "per_rule": dict(per_rule),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", nargs="+", required=True,
                    help="clean baseline modbus.log files")
    ap.add_argument("--attack", nargs=2, action="append", default=[],
                    metavar=("NAME", "PATH"),
                    help="named attack modbus.log; repeatable")
    ap.add_argument("--no-rate-rule", action="store_true",
                    help="disable R4 poller-rate rule (it can be noisy at "
                         "capture edges and restarts)")
    args = ap.parse_args()

    rate_rule = not args.no_rate_rule

    # ---- Baseline: pooled and per file --------------------------------
    print("=" * 64)
    print("NETWORK-ONLY BASELINE (clean, no attacks)")
    print("=" * 64)
    pooled_bins = {}
    for path in args.baseline:
        rows = list(parse_modbus_log(path))
        bins = score_bins(rows)
        s = summarise(path.split("/")[-2] if "/" in path else path,
                      bins, rate_rule=rate_rule)
        # Merge into pooled (bin keys are epoch seconds, globally unique)
        for b, r in bins.items():
            pooled_bins[b] = r
        fp = len(s["alarm_bins"])
        tb = s["total_bins"]
        fpr = fp / tb if tb else 0.0
        print(f"\n{s['name']}")
        print(f"  decision bins : {tb}")
        print(f"  false alarms  : {fp}")
        print(f"  network FPR   : {fpr:.6f}  ({fpr*100:.4f}%)")
        if s["per_rule"]:
            print(f"  by rule       : {s['per_rule']}")

    ptb = len(pooled_bins)
    pfp = sum(1 for r in pooled_bins.values() if r["rules"])
    pfpr = pfp / ptb if ptb else 0.0
    per_rule = defaultdict(int)
    for r in pooled_bins.values():
        for rule in r["rules"]:
            per_rule[rule] += 1
    print("\n" + "-" * 64)
    print("POOLED BASELINE")
    print(f"  decision bins : {ptb}")
    print(f"  false alarms  : {pfp}")
    print(f"  network FPR   : {pfpr:.6f}  ({pfpr*100:.4f}%)")
    print(f"  by rule       : {dict(per_rule)}")
    print(f"  (physics held-out FPR for comparison: 0.000371 / 0.0371%)")

    # ---- Attacks: recall and latency ----------------------------------
    for name, path in args.attack:
        print("\n" + "=" * 64)
        print(f"ATTACK: {name}  ({path})")
        print("=" * 64)
        rows = list(parse_modbus_log(path))
        # first attack packet = first REQ from a non-baseline source,
        # or first write to the PLC
        attack_ts = None
        for row in rows:
            try:
                ts = float(row["ts"])
            except (KeyError, ValueError):
                continue
            src = row.get("id.orig_h", "")
            dst = row.get("id.resp_h", "")
            func = row.get("func", "")
            if src not in KNOWN_SOURCES or (func in WRITE_FUNCS and dst in PLC_ADDRS):
                attack_ts = ts
                break
        bins = score_bins(rows)
        # do NOT apply the rate rule to short attack captures; it dominates
        s = summarise(name, bins, rate_rule=False)
        alarm_bins = s["alarm_bins"]
        detected = len(alarm_bins) > 0
        print(f"  decision bins : {s['total_bins']}")
        print(f"  alarm bins    : {len(alarm_bins)}")
        print(f"  detected      : {detected}")
        print(f"  by rule       : {s['per_rule']}")
        if attack_ts is not None and alarm_bins:
            attack_bin = int(math.floor(attack_ts))
            # first alarming bin at or after the attack packet's bin
            after = [b for b in alarm_bins if b >= attack_bin]
            first_alarm_bin = min(after) if after else min(alarm_bins)
            # latency in whole decision bins (1s each), matching physics units
            latency_bins = first_alarm_bin - attack_bin
            print(f"  first attack packet : {attack_ts:.3f} (bin {attack_bin})")
            print(f"  first alarm bin     : {first_alarm_bin}")
            print(f"  detection latency   : {latency_bins} bin(s) "
                  f"(~{latency_bins}s at 1 Hz)")
            if not after:
                print("  NOTE: alarm precedes attack packet; check attack "
                      "onset labelling")
        elif attack_ts is None:
            print("  no attack packet identified (check sources/vantage)")


if __name__ == "__main__":
    main()
