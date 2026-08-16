import csv, sys, datetime

path = sys.argv[1]
onset = sys.argv[2] if len(sys.argv) > 2 else None  # ISO onset timestamp, optional

rows = [r for r in csv.reader(open(path)) if r and r[0].strip()]
hdr = [h.strip() for h in rows[0]]
data = rows[1:]
ai = hdr.index('productAlarm')
ti = hdr.index('timestamp')

total = len(data)
alarms = sum(1 for r in data if r[ai].strip() == '1')
rate = 100 * alarms / total if total else 0

print(f"file: {path}")
print(f"rows: {total}  alarms: {alarms}  alarm rate: {rate:.2f}%")

if onset:
    t0 = datetime.datetime.fromisoformat(onset.replace('Z', '+00:00'))
    first = None
    for r in data:
        if r[ai].strip() == '1':
            t = datetime.datetime.fromisoformat(r[ti].strip().replace('Z', '+00:00'))
            if t >= t0:
                first = (t - t0).total_seconds()
                break
    print(f"first alarm after onset: {first if first is not None else 'none'} s")