# Physics-Informed Detection of Cyber-Attacks on Industrial Control Systems

MSc Cybersecurity dissertation project, University of Birmingham, 2025-26.

A physics-informed anomaly detector for an industrial control system, built on
the GRFICSv3 testbed. The detector fits a linear valve-to-flow invariant from
clean process data and applies a two-sided cumulative sum test to the residual.
It is evaluated against four families of Modbus-level attack and compared against
a Zeek network-layer baseline.

Headline results: 14 of 16 attacks detected, per-event F1 of 0.903, held-out
false-positive rate of 0.037 per cent, and covert drift detected reliably down to
0.001 standard deviations per second.

---

## Repository layout

```
analysis/            Python calibration and scoring scripts
data/
  evaluation/        17 evaluation runs: detection and raw CSVs, run_onsets.json
  calibration/       clean baselines and development runs used for fitting
  meta/              per-run metadata
  attack-logs/       proxy tamper logs, one per attack run
dashboard/           Postgres schema, Grafana dashboards, docker-compose
docs/                proposal and literature review
network/             Zeek capture and scoring notes
src/
  ModbusPoller/      C# .NET 8 detection engine
  attack-scripts/    Python attack tooling
progress-log.md      development log
```

---

## Requirements

- Docker Desktop with a WSL2 Ubuntu backend, on Windows 11
- .NET 8 SDK, if building the poller outside Docker
- Python 3.11 or later, with `pandas`, `numpy`, and `psycopg2` for database loading
- A running GRFICSv3 deployment

Windows notes: use `python`, not `python3`. PowerShell does not expand `*.csv`
globs, so pass file lists explicitly (see the analysis commands below) or run the
scripts from WSL.

---

## Detection engine (`src/ModbusPoller`)

Polls the controller over Modbus TCP using NModbus, reading a contiguous register
block in a single call so that every recorded row is a same-instant snapshot. Raw
16-bit register values are written to file unmodified and scaled during analysis.

- `Detection/CusumDetector.cs` applies the fitted invariant and the two-sided
  cumulative sum test.
- `Detection/PollGuard.cs` blocks the detector update when a poll cycle stalls,
  then continues blocking for five further samples.
- `Detection/Replay.cs` and `Detection/GuardReplay.cs` replay recorded data
  through the detector, used to verify the C# engine against the Python reference
  to machine precision.
- `Livewriter.cs` streams samples into Postgres through a bounded channel and a
  background task, so a slow database write never blocks the poll loop.

Detector parameters are held in `appsettings.json`, not in code.

### Recording a run

```
docker build -t modbus-poller src/ModbusPoller/ModbusPoller

docker run --rm --network shyam_b-ics-net \
  -v "$(pwd)/data:/app/data" \
  modbus-poller <run_id> 192.168.95.2
```

The poller refuses to overwrite existing files, so each `run_id` must be new. Each
run produces `raw_<run_id>.csv`, `detection_<run_id>.csv`, `<run_id>_modbus.log`
and `<run_id>_meta.csv`.

---

## Attack tooling (`src/attack-scripts`)

Input registers are read-only at the protocol level, so three of the four attacks
tamper with values in transit through a transparent Modbus relay. The relay
forwards frames byte for byte and rewrites only the target register's bytes in the
response, leaving the rest of the frame untouched. The controller's own registers
are never modified.

- `false_sensor_injection.py` rewrites the product flow register to a value
  derived from the live valve position and the calibration line, offset by a set
  multiple of the residual standard deviation.
- `replay.py` feeds a matched valve and flow pair from an earlier recording.
- `covert_drift.py` adds a growing offset, expressed in standard deviations per
  second and passed as an argument.

Command injection is a genuine Modbus write and is not scripted here. Its recorded
outcomes are in `data/evaluation/` and the register map is in
`src/ModbusPoller/ModbusPoller/ModbusRegisterMap.cs`.

Start the relay, then point the poller at the relay instead of the controller.

---

## Analysis (`analysis/`)

- `AnalyseReading.py` fits the valve-to-flow invariant on the clean baseline and
  produces the verification traces.
- `metrics.py` computes per-event and per-window classification metrics and
  PR-AUC.
- `physics_recall.py` computes detection and latency per run, measured from the
  logged onset.
- `load_runs.py` loads recorded runs into Postgres for the dashboard.
- `score_network.py` scores the Zeek network arm.

Reproduce the headline metrics:

```
# WSL
python3 analysis/metrics.py data/evaluation/detection_*.csv
python3 analysis/physics_recall.py data/evaluation/detection_*.csv
```

```
# PowerShell
python analysis\metrics.py (Get-ChildItem data\evaluation\detection_*.csv).FullName
python analysis\physics_recall.py (Get-ChildItem data\evaluation\detection_*.csv).FullName
```

`metrics.py` and `physics_recall.py` read `data/evaluation/run_onsets.json` for
ground-truth onsets.

---

## Dashboard (`dashboard/`)

Postgres 16 and Grafana in containers. Three tables: `runs`, `process` and
`alerts`. The `process` table is long format, one row per timestamp per stream.
Two dashboards are provided, one for replaying recorded runs and one for live
monitoring.

```
docker compose -f dashboard/docker-compose.yml up -d

python analysis/load_runs.py --data-dir data/evaluation --dry-run
python analysis/load_runs.py --data-dir data/evaluation
```

Grafana is then on port 3000. Loading is idempotent per run, so re-running a load
replaces that run only.

### Live monitoring

Set `PG_CONN` when starting the poller to stream a run into the dashboard as it is
recorded.

```
docker network connect shyam_b-ics-net icsdash-db

docker run --rm --network shyam_b-ics-net \
  -v "$(pwd)/data:/app/data" \
  -e PG_CONN="Host=icsdash-db;Port=5432;Username=ics;Password=icsdash;Database=icsdash" \
  modbus-poller <run_id> 192.168.95.2
```

Without `PG_CONN` the poller behaves exactly as it does for a recorded run. A
`Cannot load library libgssapi_krb5.so.2` message on startup is harmless; Npgsql
falls back to password authentication and connects.

---

## Network arm (`network/`)

See `network/README.md` for the Zeek capture and scoring procedure. The network
arm is reported as detection coverage, not latency, because its rules are keyed to
the Docker subnet used during calibration and the network was recreated before the
evaluation runs.

---

## Locked detector parameters

Fitted once on a clean baseline, then frozen. Not re-tuned for any run.

| Stream  | m        | c         | sigma    | slack | threshold |
|---------|----------|-----------|----------|-------|-----------|
| Product | 2.125465 | -0.003092 | 0.071797 | 0.75  | 5.0       |
| Purge   | 4.485712 | 0.004098  | 0.086533 | 0.5   | 6.0       |

Poll guard: nominal interval 1.0 s, multiplier 1.5, recovery 5 samples.

Scaling:

```
valve_percent    = raw / 65535 * 100
flow_engineering = raw / 65535 * 500
```

Register map: `ManualMode` is coil 0, `PurgeManualSP` is holding 12,
`ProductManualSP` is holding 13, `OverrideSp` is holding 1027. Manual setpoints
park at 65535 when unused, so treat that value as unset when scaling.

---

## Dataset

Seventeen runs recorded on 4 and 5 August 2026, after the parameters were locked.
One clean run and sixteen attack runs, covering ten drift rates, four command
injection variants, one false sensor injection and one replay.

Ground-truth onsets are in `data/evaluation/run_onsets.json` with a stated basis
for each. Onsets marked `logged` are written by the attack script on the same
clock as the poller and are exact. Onsets marked `inferred` are recovered from
residual departure, and latency is withheld for those runs.

The `data/calibration/` folder holds clean baselines and earlier development runs
used during fitting and verification. These are development history, not
evaluation results.

---

## Author

Shyam Vaideeaswaran, MSc Cybersecurity, University of Birmingham.
Supervised by Dr Wendy Yanez.
