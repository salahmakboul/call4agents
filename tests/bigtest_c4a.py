"""The big offline test (0 LLM calls). Run: python -m tests.bigtest_c4a [--quick]

A  CHAOS     W worker processes charge the SAME N orders at the same time through a fake payment API that
             delays answers, drops answers AFTER charging, and gets retries. Workers are killed at random
             points (before the call, after the API charged but before the record, after the record) and
             restarted. Half-way, the approval secret changes (old one kept for old approvals).
             Some orders also need an approved refund. A CONTROL round runs the same chaos without the
             kit and must show double charges, or the test proves nothing.
B  LONG RUN  thousands of actions in one record across many restarts (a kill every few hundred), then
             archive, then verify: no action twice, integrity ok, size and speed reported.
C  BAD INPUT hand-made and damaged record files, huge and unusual arguments: each must work or fail clearly.

Every number is printed as measured. Exit 1 if any invariant fails.
"""

import json
import os
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILED = []
QUICK = "--quick" in sys.argv


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if detail != "" else ""), flush=True)
    if not ok:
        FAILED.append(name)


# ----------------------------------------------------------------------------- fake payment API
class Pay(BaseHTTPRequestHandler):
    lock = threading.Lock()
    charges = []            # (order, charge id)
    by_key = {}             # idempotency key -> response
    requests = 0
    dropped = 0
    rng = random.Random(7)
    drop_p, delay_p = 0.15, 0.25

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        key = self.headers.get("Idempotency-Key")
        with Pay.lock:
            Pay.requests += 1
            if key and key in Pay.by_key:
                resp = Pay.by_key[key]                         # duplicate: same answer, no new charge
            else:
                resp = {"id": f"ch_{len(Pay.charges) + 1}", "order": body["order"], "kind": body["kind"]}
                Pay.charges.append((body["order"], body["kind"], resp["id"]))
                if key:
                    Pay.by_key[key] = resp
            drop = Pay.rng.random() < Pay.drop_p
            delay = Pay.rng.random() < Pay.delay_p
        if delay:
            time.sleep(Pay.rng.uniform(0.05, 0.4))
        if drop:                                               # charged, but the caller never hears it
            with Pay.lock:
                Pay.dropped += 1
            self.close_connection = True
            self.connection.shutdown(2)
            return
        out = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def start_api():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Pay)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/charge"


# ----------------------------------------------------------------------------- worker process (part A)
def worker(argv):
    url, record, orders, kill_p, use_kit, seed = argv[0], argv[1], int(argv[2]), float(argv[3]), argv[4] == "1", int(argv[5])
    rng = random.Random(seed)
    sys.path.insert(0, str(ROOT))
    import call4agents as c4a

    def post(order, kind):
        headers = {"Content-Type": "application/json"}
        if use_kit:
            headers.update(c4a.idempotency_headers())
        req = urllib.request.Request(url, data=json.dumps({"order": order, "kind": kind}).encode(), headers=headers)
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
        if rng.random() < kill_p:
            os._exit(17)                       # the API charged; our record has no result
        return resp

    def charge(order):
        return post(order, "charge")

    def refund(order):
        return post(order, "refund")
    if use_kit:
        c4a.init(record)                       # secret from C4A_SECRET / C4A_OLD_SECRETS (rotated by the parent)
        charge = c4a.effect(charge, name="big.charge", idempotent_for=3600, wait=0.5)
        refund = c4a.effect(refund, name="big.refund", approval=True, idempotent_for=3600, wait=0.5)
        ctx = c4a.run("big")
    else:
        done_file = Path(record + ".done")     # the "framework" a naive app has: a done-list, written after
        done = set(done_file.read_text().split()) if done_file.exists() else set()

        class _Naive:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        ctx = _Naive()
    order_ids = list(range(orders))
    rng.shuffle(order_ids)
    stats = Counter()
    with ctx:
        for o in order_ids:
            if rng.random() < kill_p / 4:
                os._exit(18)                   # killed between actions
            if not use_kit and str(o) in done:
                continue
            try:
                charge(o)
                stats["charged"] += 1
                if not use_kit:
                    with open(record + ".done", "a") as f:
                        f.write(f"{o}\n")
                    done.add(str(o))
                if use_kit and o % 10 == 0:
                    refund(o)
                    stats["refunded"] += 1
            except Exception as exc:          # in doubt, approval missing, dropped answer...
                stats[type(exc).__name__] += 1
    print("STATS", json.dumps(stats))


def run_chaos(use_kit, workers, orders, rounds, kill_p, rotate):
    Pay.charges.clear()
    Pay.by_key.clear()
    Pay.requests = Pay.dropped = 0
    d = Path(tempfile.mkdtemp(prefix="c4a-big-"))
    record = str(d / "big.sqlite")
    s1, s2 = "first-secret-0123456789", "second-secret-9876543210"
    env = {**os.environ, "C4A_SECRET": s1, "PYTHONWARNINGS": "ignore"}
    env.pop("C4A_OLD_SECRETS", None)
    srv, url = start_api()
    if use_kit:                                   # approvals for the refunds of orders 0, 10, 20... (before rotation)
        sys.path.insert(0, str(ROOT))
        import call4agents as c4a
        os.environ["C4A_SECRET"] = s1
        lg = c4a.init(record)
        for o in range(0, orders, 10):
            with c4a.run("big"):
                c4a.approve("big.refund", order=o)
        os.environ.pop("C4A_SECRET", None)
    kills = restarts = 0
    t0 = time.time()
    stats = Counter()
    for rnd in range(rounds):
        if use_kit and rotate and rnd == rounds // 2:
            env["C4A_SECRET"], env["C4A_OLD_SECRETS"] = s2, s1     # secret changes mid-run
        procs = [subprocess.Popen([sys.executable, "-m", "tests.bigtest_c4a", "_worker", url, record, str(orders),
                                   str(kill_p if rnd < rounds - 1 else 0.0), "1" if use_kit else "0",
                                   str(rnd * 1000 + w)], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for w in range(workers)]
        for p in procs:
            out, err = p.communicate(timeout=900)
            if p.returncode in (17, 18):
                kills += 1
            elif p.returncode != 0:
                stats["worker_crash"] += 1
                if stats["worker_crash"] <= 2:
                    print("    worker error:", err.strip().splitlines()[-1][:200] if err.strip() else p.returncode)
            for line in out.splitlines():
                if line.startswith("STATS"):
                    stats.update(json.loads(line[6:]))
            restarts += 1
    took = time.time() - t0
    srv.shutdown()
    charges = Counter(o for o, kind, _ in Pay.charges if kind == "charge")
    refunds = Counter(o for o, kind, _ in Pay.charges if kind == "refund")
    res = {"workers": workers, "orders": orders, "rounds": rounds, "processes_started": restarts, "killed": kills,
           "api_requests": Pay.requests, "answers_dropped": Pay.dropped, "seconds": round(took, 1),
           "orders_charged_twice_or_more": sum(1 for n in charges.values() if n > 1),
           "max_charges_for_one_order": max(charges.values() or [0]), "orders_charged": len(charges),
           "refunds_twice": sum(1 for n in refunds.values() if n > 1), "refunds": sum(refunds.values()),
           "refunds_without_approval": sum(1 for o in refunds if o % 10), "worker_errors": dict(stats)}
    if use_kit:
        from call4agents import ops
        lg = ops.open_record(record)
        res["verify"] = ops.verify(record)
        res["in_doubt_left"] = len(ops.in_doubt(lg))
        acts = ops.actions(lg)
        res["charges_recorded_done"] = sum(1 for a in acts.values() if a["tool"] == "big.charge" and a["done"])
        res["record_main_mb"] = round((d / "big.sqlite").stat().st_size / 1e6, 2)
        wal = d / "big.sqlite-wal"
        res["record_wal_mb"] = round(wal.stat().st_size / 1e6, 2) if wal.exists() else 0.0
    shutil.rmtree(d, ignore_errors=True)
    return res


# ----------------------------------------------------------------------------- part B: long run
def long_child(argv):
    record, start, count, kill_at = argv[0], int(argv[1]), int(argv[2]), int(argv[3])
    sys.path.insert(0, str(ROOT))
    import call4agents as c4a
    c4a.init(record)
    out = Path(record + ".sent")
    with open(out, "a") as f:
        @c4a.effect(name="long.send")
        def send(i):
            f.write(f"{i}\n")
            f.flush()
            return i
        with c4a.run("long"):
            for n, i in enumerate(range(start, start + count)):
                if n == kill_at:
                    os._exit(19)
                send(i)


def run_long(total, chunk):
    d = Path(tempfile.mkdtemp(prefix="c4a-long-"))
    record = str(d / "long.sqlite")
    env = {**os.environ, "C4A_SECRET": "long-run-secret-0123456789", "PYTHONWARNINGS": "ignore"}
    rng = random.Random(3)
    t0 = time.time()
    kills = 0
    done_upto = 0
    while done_upto < total:
        kill_at = rng.randrange(chunk // 2, chunk) if rng.random() < 0.7 else -1
        if done_upto > total // 2 and "C4A_OLD_SECRETS" not in env:
            env["C4A_OLD_SECRETS"], env["C4A_SECRET"] = env["C4A_SECRET"], "long-run-secret-ROTATED-987"
        p = subprocess.run([sys.executable, "-m", "tests.bigtest_c4a", "_long", record, str(max(0, done_upto - 50)),
                            str(chunk), str(kill_at)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
        if p.returncode == 19:
            kills += 1
            done_upto = max(0, done_upto - 50) + kill_at        # resume from before the kill (overlap re-calls)
        elif p.returncode == 0:
            done_upto = max(0, done_upto - 50) + chunk
        else:
            print("    long child error:", p.stderr.strip().splitlines()[-1][:200])
            break
    took = time.time() - t0
    sent = Path(record + ".sent").read_text().split()
    counts = Counter(sent)
    sys.path.insert(0, str(ROOT))
    from call4agents import ops
    v = ops.verify(record)
    size = (d / "long.sqlite").stat().st_size
    wal_mb = round((d / "long.sqlite-wal").stat().st_size / 1e6, 2) if (d / "long.sqlite-wal").exists() else 0.0
    con = sqlite3.connect(record)
    con.execute("UPDATE entries SET t = t - 40 * 86400")
    con.commit()
    con.close()
    t1 = time.time()
    arch = ops.archive(record, str(d / "archive.sqlite"), 30)
    arch_s = time.time() - t1
    res = {"distinct_actions_sent": len(counts),
           "actions_sent_twice": sum(1 for n in counts.values() if n > 1), "kills": kills,
           "seconds": round(took, 1), "record_main_mb": round(size / 1e6, 2), "record_wal_mb": wal_mb,
           "main_bytes_per_row": round(size / max(1, v["rows"])), "rows": v["rows"],
           "integrity": v["integrity"], "in_doubt": v["in_doubt"], "archive": arch, "archive_seconds": round(arch_s, 2)}
    shutil.rmtree(d, ignore_errors=True)
    return res


# ----------------------------------------------------------------------------- part C: bad input
def run_bad_input():
    sys.path.insert(0, str(ROOT))
    import call4agents as c4a
    from call4agents.ledger import Ledger, LedgerError
    rows = {}
    d = Path(tempfile.mkdtemp(prefix="c4a-bad-"))

    def outcome(fn):
        try:
            fn()
            return "worked"
        except (LedgerError, TypeError, c4a.EffectInDoubt, c4a.ApprovalRequired) as e:
            return f"clear error: {type(e).__name__}"
        except Exception as e:  # noqa: BLE001  - an unclear error is a finding
            return f"UNCLEAR {type(e).__name__}: {str(e)[:60]}"
    (d / "random.sqlite").write_bytes(os.urandom(5000))
    rows["random bytes as record"] = outcome(lambda: Ledger(str(d / "random.sqlite"), secret=b"x" * 16))
    good = d / "good.sqlite"
    lg = Ledger(str(good), secret=b"x" * 16)
    for i in range(300):
        lg.append("r", "tool_done", f"k{i}", {"result": "x" * 200})
    with lg._conn() as con0:
        con0.execute("PRAGMA wal_checkpoint(TRUNCATE)")      # all rows into the main file, then cut it
    lg._conn().close()
    lg._local.conn = None
    data = good.read_bytes()
    (d / "truncated.sqlite").write_bytes(data[: len(data) // 2])
    rows["record cut in half"] = outcome(lambda: Ledger(str(d / "truncated.sqlite"), secret=b"x" * 16).entries())
    con = sqlite3.connect(d / "other.sqlite")
    con.execute("CREATE TABLE entries (a, b)")
    con.commit()
    con.close()
    rows["sqlite file with a different 'entries' table"] = outcome(lambda: Ledger(str(d / "other.sqlite"), secret=b"x" * 16))
    lg = c4a.init(str(d / "work.sqlite"), secret=b"x" * 16)
    con = sqlite3.connect(d / "work.sqlite")
    from call4agents.ledger import effect_key
    k = effect_key("bad", "bad.send", {"to": "x"})
    con.execute("INSERT INTO entries (run_id, kind, key, payload, t) VALUES ('bad', 'tool_done', ?, '{not json', 0)", (k,))
    con.commit()
    con.close()
    send = c4a.effect(lambda to: "ok", name="bad.send")
    with c4a.run("bad"):
        rows["hand-written result row with broken JSON"] = outcome(lambda: send("x"))
    lg = c4a.init(str(d / "args.sqlite"), secret=b"x" * 16)
    echo = c4a.effect(lambda **kw: "ok", name="bad.echo")
    with c4a.run("args"):
        rows["10 MB argument"] = outcome(lambda: echo(blob="x" * 10_000_000))
        rows["emoji, RTL and NUL characters in arguments"] = outcome(lambda: echo(text="a‮\U0001F600" + chr(0) + "z"))
        rows["NaN and infinity in arguments"] = outcome(lambda: echo(x=float("nan"), y=float("inf")))
        rows["deeply nested argument (depth 5000)"] = outcome(lambda: echo(deep=nest(5000)))
        rows["a socket object as argument"] = outcome(lambda: echo(sock=__import__("socket").socket()))
    shutil.rmtree(d, ignore_errors=True)
    return rows


def nest(n):
    x = []
    for _ in range(n):
        x = [x]
    return x


# ----------------------------------------------------------------------------- main
def main():
    workers, orders, rounds = (6, 80, 4) if QUICK else (12, 300, 6)
    print(f"A CHAOS: {workers} processes x {rounds} rounds on the same {orders} orders, kills, drops, delays, secret change", flush=True)
    control = run_chaos(False, workers, orders, rounds, kill_p=0.02, rotate=False)
    print("  control (no kit):", json.dumps(control), flush=True)
    check("A0 control: without the kit the same chaos DOES charge orders twice (the test can fail)",
          control["orders_charged_twice_or_more"] > 0, control["orders_charged_twice_or_more"])
    kit = run_chaos(True, workers, orders, rounds, kill_p=0.02, rotate=True)
    print("  with the kit:", json.dumps(kit), flush=True)
    check("A1 no order charged twice", kit["orders_charged_twice_or_more"] == 0, kit["orders_charged_twice_or_more"])
    check("A2 every order charged once and recorded done", kit["orders_charged"] == orders
          and kit["charges_recorded_done"] == orders, (kit["orders_charged"], kit["charges_recorded_done"]))
    check("A3 no refund twice, none without an approval (old-secret approvals still valid after rotation)",
          kit["refunds_twice"] == 0 and kit["refunds_without_approval"] == 0 and kit["refunds"] == len(range(0, orders, 10)),
          (kit["refunds"], kit["refunds_twice"], kit["refunds_without_approval"]))
    check("A4 record integrity ok, nothing left in doubt, no late results", kit["verify"]["integrity"] == "ok"
          and kit["in_doubt_left"] == 0 and kit["verify"]["late_results"] == 0, (kit["verify"], kit["in_doubt_left"]))
    check("A5 no worker crashed for an unexpected reason", "worker_crash" not in kit["worker_errors"], kit["worker_errors"])

    total, chunk = (2000, 400) if QUICK else (20000, 2000)
    print(f"B LONG RUN: {total} actions, a kill in about 70% of {chunk}-action chunks, 50-action overlap re-calls, secret rotated half-way", flush=True)
    long = run_long(total, chunk)
    print("  ", json.dumps(long), flush=True)
    check("B1 every action sent exactly once over all restarts", long["actions_sent_twice"] == 0
          and long["distinct_actions_sent"] >= total, (long["distinct_actions_sent"], long["actions_sent_twice"]))
    check("B2 integrity ok after restarts; archive moved the finished run", long["integrity"] == "ok"
          and long["archive"]["runs_moved"] == 1, long["archive"])

    print("C BAD INPUT", flush=True)
    bad = run_bad_input()
    for k, v in bad.items():
        print(f"     {k:48} {v}")
    check("C1 every bad input either works or gives a clear error (no unclear crash)",
          not any(v.startswith("UNCLEAR") for v in bad.values()), [k for k, v in bad.items() if v.startswith("UNCLEAR")])

    print(f"\n{len(FAILED)} big-test check(s) failed" if FAILED else "\nall big-test checks pass")
    return 1 if FAILED else 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "_worker":
        worker(sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] == "_long":
        long_child(sys.argv[2:])
    else:
        sys.exit(main())
