# Network detection arm

The network arm is a Zeek baseline used for comparison against the physics
detector. It is run over saved packet captures, not live.

## Capture

Capture in the controller container's network namespace, not the poller's. The
controller vantage sees all traffic reaching the device, including writes sent
from the host, while the poller vantage sees only its own leg.

    docker run --rm --net=container:plc -v /home/shyam/caps:/caps \
      <zeek-image> zeek -C -r /caps/<capture>.pcap

The `-C` flag is required because checksums are offloaded, so Zeek would
otherwise discard every packet as having a bad checksum.

## Scoring

The scoring script reads the Zeek Modbus logs and applies the detection rules.

    python analysis/score_network.py --logs <zeek-log-dir>

Detection rules were derived from a clean three-hour baseline, which showed that
no write of any kind reaches the controller during normal operation. Any write
directed at the controller is therefore anomalous by itself. The rules key on
source address and function code. Unit identifier is not used, because the
poller uses unit 0 and unit 1 belongs to the interface, so a unit-based rule
would fire on every interface poll.

Network transactions are binned into one-second windows with at most one alarm
per window, so the network arm and the physics arm have comparable decision
counts.

## Known issue

The rules are keyed to the Docker subnet in use when the calibration captures
were taken (172.18.x). The Docker network was recreated before the evaluation
runs, which moved them to a different subnet (172.19.x), so the source address
set in the rules needs re-keying before the network arm can be scored on the
evaluation dataset. The capture container clock also drifted from the poller
clock on the command injection captures. For these reasons the network arm is
reported as detection coverage, whether a rule fired for a given attack, and not
as latency.
