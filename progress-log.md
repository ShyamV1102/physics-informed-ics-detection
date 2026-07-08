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