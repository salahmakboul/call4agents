"""The run record under load and crashes (offline, 0 calls).
  S1 many writers: 12 processes x 150 appends each, plus 8 threads in one process
  S2 crash in the middle of writes: a process is killed while appending in a loop
  S3 the SAME effect called at the same moment from 8 threads and 6 processes
Run: python -m tests.stress_c4a_ledger"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])


def child_append(path, n, tag):
    from call4agents.ledger import Ledger
    lg = Ledger(path, secret=b"s")
    for i in range(n):
        lg.append("r", "note", f"{tag}-{i}", {"i": i})


def child_crash(path):
    from call4agents.ledger import Ledger
    lg = Ledger(path, secret=b"s")
    i = 0
    while True:
        lg.append("r", "note", f"crash-{i}", {"i": i, "pad": "x" * 2000})
        i += 1


def child_effect(path, out, start_at):
    import call4agents as c4a
    c4a.init(path, secret=b"s")

    @c4a.effect(name="send")   # one tool, defined in the child and in the parent
    def send(to):
        with open(out, "a") as f:
            f.write(f"{os.getpid()}\n")
        time.sleep(0.05)
        return "sent"
    while time.time() < start_at:
        time.sleep(0.001)
    with c4a.run("same-run"):
        try:
            send("bob")
            print("RESULT ok")
        except c4a.EffectInDoubt:
            print("RESULT in_doubt")


def spawn(*args):
    return subprocess.Popen([sys.executable, "-m", "tests.stress_c4a_ledger", *args], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def main():
    out = {}
    tmp = Path(tempfile.mkdtemp(prefix="c4a-stress-"))
    # S1
    path = str(tmp / "s1.sqlite")
    from call4agents.ledger import Ledger
    Ledger(path, secret=b"s")
    procs = [spawn("append", path, "150", f"p{k}") for k in range(12)]
    errs = []
    threads = [threading.Thread(target=lambda k=k: child_append(path, 150, f"t{k}")) for k in range(8)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    except Exception as exc:
        errs.append(repr(exc))
    for p in procs:
        _, e = p.communicate()
        if p.returncode:
            errs.append(e.strip().splitlines()[-1][:160] if e.strip() else f"exit {p.returncode}")
    rows = sqlite3.connect(path).execute("SELECT COUNT(*), COUNT(DISTINCT key) FROM entries").fetchone()
    out["S1 many writers"] = {"expected": 20 * 150, "rows": rows[0], "distinct": rows[1], "errors": errs[:3]}
    # S2
    path = str(tmp / "s2.sqlite")
    Ledger(path, secret=b"s")
    killed = []
    for _ in range(5):
        p = spawn("crash", path)
        time.sleep(0.4)
        p.kill()
        p.wait()
        killed.append(p.returncode)
    conn = sqlite3.connect(path)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    bad = 0
    for (payload,) in conn.execute("SELECT payload FROM entries"):
        try:
            json.loads(payload)
        except ValueError:
            bad += 1
    seqs = [r[0] for r in conn.execute("SELECT seq FROM entries ORDER BY seq")]
    out["S2 killed mid-write x5"] = {"integrity": integrity, "rows": len(seqs), "unreadable_rows": bad,
                                     "after_reopen_append_ok": _append_ok(path)}
    # S3
    path, outbox = str(tmp / "s3.sqlite"), str(tmp / "s3_outbox.txt")
    import call4agents as c4a
    c4a.init(path, secret=b"s")
    start_at = time.time() + 1.5
    procs = [spawn("effect", path, outbox, str(start_at)) for _ in range(6)]
    results = []

    @c4a.effect(name="send")   # one tool, defined in the child and in the parent
    def send(to):
        with open(outbox, "a") as f:
            f.write("thread\n")
        time.sleep(0.05)
        return "sent"

    def thread_call():
        while time.time() < start_at:
            time.sleep(0.001)
        with c4a.run("same-run"):
            try:
                send("bob")
                results.append("ok")
            except c4a.EffectInDoubt:
                results.append("in_doubt")
    threads = [threading.Thread(target=thread_call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for p in procs:
        o, e = p.communicate()
        results += [l.split()[1] for l in o.splitlines() if l.startswith("RESULT")] or [f"error: {e.strip()[-120:]}"]
    sent = len(Path(outbox).read_text().splitlines()) if Path(outbox).exists() else 0
    out["S3 same effect from 14 callers at once"] = {"effects": sent, "results": sorted(set(results)),
                                                    "callers": len(results)}
    print(json.dumps(out, indent=1))
    return out


def _append_ok(path):
    from call4agents.ledger import Ledger
    try:
        Ledger(path, secret=b"s").append("r", "note", "after", {})
        return True
    except Exception as exc:
        return repr(exc)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "append":
        child_append(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    elif len(sys.argv) > 1 and sys.argv[1] == "crash":
        child_crash(sys.argv[2])
    elif len(sys.argv) > 1 and sys.argv[1] == "effect":
        child_effect(sys.argv[2], sys.argv[3], float(sys.argv[4]))
    else:
        r = main()
        ok = (r["S1 many writers"]["rows"] == r["S1 many writers"]["expected"] and not r["S1 many writers"]["errors"]
              and r["S2 killed mid-write x5"]["integrity"] == "ok" and r["S2 killed mid-write x5"]["unreadable_rows"] == 0
              and r["S3 same effect from 14 callers at once"]["effects"] == 1)
        print("stress checks pass" if ok else "STRESS CHECKS FAILED")
        sys.exit(0 if ok else 1)
