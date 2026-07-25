# Project Progress Log

**Project:** Physics-Informed Detection of Cyber-Attacks on Industrial Control Systems
**Testbed:** GRFICSv3 (Docker, WSL2/Ubuntu on Windows 11)
**Detection engine:** .NET (C#)

This is a running log of daily progress on the project. New entries are added
under dated headings. Each entry lists what was done and a short note on why it 
mattered.

---

## Sunday, 5 July 2026

**Focus: proving the data path and deciding the core design.**

- **Modbus data path.** Polled the OpenPLC
  controller over Modbus TCP from inside the OT network and read a live process
  value back. This retires the main toolchain integration risk before any
  detection code is written, which was a deliberate sequencing choice. It proves the
  pipe works before building on top of it.

- **Design decisions**
  - The detector reads process data by polling the PLC directly over Modbus,
    not through the HMI.
  - The engine runs in controlled experiments where a crash means restarting the run, 
    so in-memory state is sufficient and avoids an extra integration dependency.
  - Baseline data is stored raw, keeping the recorded data close to the wire and 
    making any scaling correction a one-line fix rather than a re-recording.
  - The detection approach is a physics-informed residual method with a CUSUM
    accumulator. The expected values from a hand-written process model are compared
    against observed values, and sustained deviation is accumulated over time to
    separate real signal from noise.

---

## Tuesday, 7 July 2026

**Focus: confirming the register map, scaling, and data-integrity details.**

- **Extracted the full register map from OpenPLC.** Recorded every process
  variable with its name, address, and register table (input registers for
  measured values and valve positions, holding registers for setpoints). This
  gives the .NET poller a single reference for what to read and what each value 
  represents.

- **Confirmed the correct scaling for raw register values.** Each raw 16-bit
  integer maps to a real engineering value through a per-variable linear scale.
  Verified the scaling against the multiplier values in the ScadaLTS Points
  table (e.g. the pressure multiplier), so real-world values can be recovered
  accurately at a later stage.

- **Caught and understood the off-by-one wire address offset.** Cross-checked
  two independent Modbus tools and found one uses 1-based register numbering
  while the other uses raw 0-based wire addresses, so the same physical value
  appears one address apart between them. Confirmed the wire address is the
  ground truth, matching the OpenPLC address offsets.

---
 
## Wednesday, 8 July 2026
 
**Focus: containerising the .NET poller onto the OT network.**
 
- **Confirmed no wire address offset in the .NET reader.** Cross-checked the
  register addresses read by the NModbus code against the tool baseline for
  multiple registers.
- **Containerised the .NET poller and ran it on the OT network.** Wrote a
  Dockerfile for the poller, built the image, and ran the container attached to
  the GRFICS process network. The container read a live register value back from
  the OpenPLC controller from inside the segmented network.
- **Contiguous block read.** The poller reads a contiguous block of
  registers in a single Modbus call rather than one call per register.
---

## Thursday, 9 July 2026
 
**Focus: CSV logging and capturing baseline.**
 
- **Added CSV logging to the poller.** The containerised poller now reads the
  full register block each poll and appends a timestamped row to a CSV on a
  mounted host folder.
- **Captured a clean one-hour steady-state baseline.** Reset the GRFICS
  containers, let the process settle, then recorded 3600 rows over about 64
  minutes.
---

## Friday, 10 July 2026
 
**Focus: baseline analysis and scaling in Python.**
 
- **Built the Python analysis layer.** Loaded the baseline CSV with pandas and
  scaled the raw 16-bit unsigned values to engineering units using per-point
  multipliers from the PLC config. Scaling is applied in memory only, so the
  raw CSV on disk stays untouched and can be re-scaled if a multiplier is
  corrected.
- **Characterised the baseline.** Confirmed setpoints are flat, pressure and
  level are stable, one input flow is constant with its valve at 100%
  (a fixed boundary condition), and the second input flow drops to zero in steady 
  state. Two columns are pinned at constant values and are treated as boundary 
  conditions.
---
 
## Monday, 13 July 2026
 
**Focus: searching for a usable detection invariant.**
 
- **Tested the level balance from the proposal.** Fitted the tank constant k
  from the baseline by least squares, in both a single-step and a windowed
  form. Neither produced any residual reduction, and k came out negative.
- **Diagnosed why.** The level is under closed-loop control, so the controller
  holds it at setpoint and it barely moves. There is nothing for the model to
  explain and least squares fits noise. The physics is not wrong; the model is
  not observable in this data. Rejected as a detection invariant on that basis.
- **Found a working invariant instead.** Flow predicted from valve position by
  linear fit. Confirmed both valves swing the full 0 to 100 range, so the fit
  is genuinely using the valve rather than collapsing to mean subtraction.
---
 
## Wednesday, 15 July 2026
 
**Focus: CUSUM implementation**
 
- **Implemented two-sided CUSUM.** Residuals are normalised to standard
  deviation units, a slack dead zone is subtracted each step so ordinary noise
  cannot accumulate, and an alarm fires when either running sum crosses the
  threshold. Only the tripped sum resets.
- **First calibration run failed.** At slack 0.5 and threshold 5 the clean
  baseline produced 23 alarms on product and 15 on purge. Every alarm on
  attack-free data is by definition false, so the target is zero.
---
 
## Thursday, 16 July 2026
 
**Focus: diagnosing the false alarms rather than tuning them away.**
 
- **Rejected the obvious fix.** Widening the detector's tolerance silenced one
  stream but not the other, which was the clue. Ordinary noise washes out as
  tolerance widens, and this did not. Raising the threshold far enough to
  swallow the spikes would have left the detector blind to the slow, sustained
  attacks the project targets, so the cause had to be found rather than tuned
  away.
- **Traced the root cause.** The false alarms sat on isolated spikes, each one
  following a poll cycle that took longer than normal. The poller reads the flow
  and the valve position at slightly different moments; normally that gap is
  small and constant and the model absorbs it, but a delayed cycle stretches it,
  so the two readings no longer describe the same instant. The spike is an
  artefact of how the data is sampled, not a real process event.
- **Tested and rejected an alternative explanation.** Checked whether fast valve
  movement alone could produce the spikes. It could not, so the delayed polls
  remained the explanation.
---
 
## Friday, 17 July 2026
 
**Focus: the exclusion filter and locking the parameters.**
 
- **Found the filter needed a recovery window.** Excluding only the delayed
  sample fixed one stream but barely helped the other. The remaining false
  alarms came in short runs, each beginning right after a delay, showing the
  disturbance persists for a few samples after the poller recovers. Carrying the
  exclusion forward across those samples fixed it.
- **Swept the recovery length.** False alarms fell steadily as the window
  widened, and the residual tightened at each step, confirming the filter
  removes genuinely corrupted samples rather than discarding good data. A small
  window was enough, excluding well under one percent of the baseline.
- **Locked the detector parameters.** Both streams now produce zero false alarms
  on the baseline, tuned separately since their noise differs. The tolerance was
  kept as low as possible, because it acts as a hard floor on sensitivity: a
  drift smaller than the tolerance never registers at all.
- **Sanity check passed.** Both fitted models predict zero flow at a closed
  valve without being told to. The model matches how a valve actually behaves
  rather than being a curve fit that happens to work.
---
 
## Friday, 18 July 2026
 
**Focus: testing whether the plant offers any further invariants.**
 
- **Pressure against valve position: rejected.** Effectively no improvement over
  a null model. Valve position does not predict pressure in this process.
- **Pressure against several process variables: rejected.** A broader fit looked
  promising at first, but inspecting it showed the model had quietly rebuilt the
  existing valve-flow signal out of the other terms. It is the same check, so an attacker 
  who evades the first would evade this one too.
- **Level against setpoint: rejected.** The gap from setpoint looked tight 
  but calibration produced hundreds of false alarms on clean data.
  Plotting it showed the level does not sit still at setpoint; it oscillates
  gently around it all hour as the controller hunts. The detector accumulates
  sustained movement in one direction, which is exactly what each swing of that
  oscillation is. A slow attack and the controller's own hunting are
  indistinguishable to it.
---
 
## Monday, 20 July 2026
 
**Focus: consolidating the result and preparing the port.**
 
- **Closed the invariant search.** The plant yields one fitted invariant,
  valve-to-flow, calibrated on two streams. Four candidates were tested and
  rejected with evidence.
- **Recorded known limitations.** The zero false alarm result was measured on
  the same hour used for fitting and tuning, so it is not an honest
  false-positive rate; a second clean capture is needed as a held-out check. The
  exclusion filter also leaves the detector blind during excluded samples.
---

## Tuesday, 21 July 2026

**Focus: starting the C# port of the detector.**

- **Moved the locked constants into config.** The fit coefficients, sigma, slack
  and threshold for both streams added to `appsettings.json` instead of the
  code.
- **Ported the CUSUM recursion to C#.** Added a `CusumDetector` class that holds
  one stream's state and updates it each poll. It follows the Python version 
  because the parameters were tuned against that exact form.
- **Kept the detector separate from the poll loop.** The Modbus code stays
  untouched and the same detector class can later be replayed against the
  baseline CSV to check the port is correct.
---

## Thursday, 23 July 2026

**Focus: porting the poll-delay exclusion.**

- **Added a `PollGuard` class.** It measures the gap between polls and blocks the
  detector when a cycle stalls, then keeps blocking for the next four samples.
- **Changed how the recovery window works.** Python looked backwards over a
  finished file, which a live detector cannot do. The window is now a counter that
  is set when a stall happens and counts down.
---

**Focus: finishing the C# port and checking it against Python.**

- **Wired the guard and detectors into the poll loop.** The detection now runs
  live alongside the polling.
- **Added detection output to its own CSV.** One row per poll, keyed by the same
  timestamp as the readings file, so the raw readings stay untouched.
- **Checked the port against Python.** Replayed the baseline through both. The
  numbers matched to machine precision, and the guard dropped the same rows,
  apart from five at the very start that come from a harmless difference in how
  the first row is handled.
- **Port complete.** The parameters tuned in Python now carry over to the live
  detector unchanged.
---