"""Cost of the kit per call (offline, 0 LLM calls). Run: python -m tests.bench_c4a [n]

Measures, on this machine:
  - a plain function call (no kit) as the floor;
  - a NEW once-only action (intent + result written to disk);
  - a REPEATED action (the recorded result is returned);
  - an approval-gated action (approve + spend + run);
  - memory growth over n actions (tracemalloc) and record size on disk per action.
Prints one JSON object. The numbers depend on the disk; they are not a promise."""

import json
import os
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

import call4agents as c4a


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def timed(fn, n):
    out = []
    for i in range(n):
        t = time.perf_counter()
        fn(i)
        out.append((time.perf_counter() - t) * 1e6)
    return out


def main(n: int = 2000) -> dict:
    d = Path(tempfile.mkdtemp(prefix="c4a-bench-"))
    c4a.init(str(d / "l.sqlite"), secret=b"bench")

    def plain(to):
        return "sent"
    send = c4a.effect(plain, name="bench.send")
    pay = c4a.effect(lambda amount, to: "paid", approval=True, name="bench.pay")

    res = {"n": n, "python": sys.version.split()[0], "platform": sys.platform}
    with c4a.run("bench"):
        floor = timed(lambda i: plain(f"u{i}"), n)
        tracemalloc.start()
        m0 = tracemalloc.get_traced_memory()[0]
        new = timed(lambda i: send(f"u{i}"), n)
        m1 = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        rep = timed(lambda i: send(f"u{i}"), n)

        def approved(i):
            c4a.approve(pay, amount=i, to="a")
            pay(amount=i, to="a")
        appr = timed(approved, max(200, n // 5))
    for label, xs in (("plain_call_us", floor), ("new_action_us", new), ("repeat_action_us", rep),
                      ("approve_and_pay_us", appr)):
        res[label] = {"median": round(statistics.median(xs), 1), "p99": round(pct(xs, 0.99), 1)}
    with c4a.current_ledger()._conn() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")       # count the main file only, after a checkpoint
    size = (d / "l.sqlite").stat().st_size
    rows = len(c4a.current_ledger().entries())
    res["memory_growth_bytes_per_action"] = round((m1 - m0) / n, 1)
    res["record_bytes_per_row"] = round(size / rows, 1)
    res["rows"] = rows
    return res


if __name__ == "__main__":
    print(json.dumps(main(int(sys.argv[1]) if len(sys.argv) > 1 else 2000), indent=1))
