"""Crash at every write. Run: python -m tests.test_crash_matrix

A crash test only finds what happens at the point where you crash. So this one crashes everywhere: a real
process is killed (os._exit) before and after EVERY write to the run record, and inside the action itself, for
each kind of action. Then a new process comes back, either with the same call or with a different call under the
same key. Offline, 0 LLM calls.

Whatever the crash point, these must hold:
M1 an approved action is charged at most once.
M2 a call nobody approved is never charged.
M3 a plain (no approval) action is charged at most once.
M4 once the first call is recorded, a different call under the same explicit key is never charged. (A crash
   before anything was recorded means the first call never happened, so a later call is simply a new action.)
M5 the first run without a crash shows every write point was reached (the matrix really covers them).
M6 two crashes in a row (the recovery crashes too, at every point), then a clean run: charged at most once."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

FAILED = []
KINDS = ("approval_named", "approval_derived", "plain_named", "plain_derived")


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def child(work, kind, step, amount, crash_at, when):
    """One process. crash_at counts writes to the record: it dies just before or just after that write."""
    import call4agents as c4a
    work = Path(work)
    lg = c4a.init(str(work / "record.sqlite"), secret=b"m" * 16)
    crash_at = int(crash_at)
    writes = {"n": 0}
    append = lg.append

    def counted(*args, **kwargs):
        writes["n"] += 1
        if writes["n"] == crash_at and when == "before":
            os._exit(9)
        out = append(*args, **kwargs)
        if writes["n"] == crash_at and when == "after":
            os._exit(9)
        (work / "writes.txt").write_text(str(writes["n"]))
        return out

    lg.append = counted

    def pay(amount):
        with open(work / "bank.txt", "a") as f:
            f.write(f"{amount}\n")
        if when == "inside" and crash_at == 1:
            os._exit(9)
        return "paid"

    pay = c4a.effect(pay, name="m.pay", approval=kind.startswith("approval"))
    options = {"c4a_key": "order-1"} if kind.endswith("named") else {}
    with c4a.run("run-1"):
        if step == "approve":
            c4a.approve(pay, amount=10)
        try:
            print("RESULT", pay(int(amount), **options))
        except Exception as exc:                      # refused or in doubt: what matters is what the bank saw
            print("RESULT", type(exc).__name__)


def start(work, kind, step, amount, crash_at=0, when="none"):
    return subprocess.run([sys.executable, "-m", "tests.test_crash_matrix", "--child", str(work), kind, step, str(amount),
                           str(crash_at), when], capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})


def bank(work):
    p = Path(work) / "bank.txt"
    return [int(x) for x in p.read_text().split()] if p.exists() else []


def records(work):
    p = Path(work) / "record.sqlite"
    if not p.exists():
        return set()
    db = sqlite3.connect(p)
    try:
        return {r[0] for r in db.execute("SELECT DISTINCT kind FROM entries")}
    finally:
        db.close()


def points(kind):
    """How many writes a clean run makes, so every one of them gets a crash before and after."""
    work = tempfile.mkdtemp(prefix="c4a-matrix-")
    start(work, kind, "approve", 10)
    p = Path(work) / "writes.txt"
    return int(p.read_text()) if p.exists() else 0, bank(work)


def main():
    table, crashes = [], 0
    for kind in KINDS:
        n, clean = points(kind)
        check(f"M5 {kind}: a clean run charges once and makes writes to crash at", clean == [10] and n >= 2, (n, clean))
        cases = [(i, w) for i in range(1, n + 1) for w in ("before", "after")] + [(1, "inside")]
        for at, when in cases:
            for second in (10, 20):                    # the same call comes back, or a different one
                work = tempfile.mkdtemp(prefix="c4a-matrix-")
                start(work, kind, "approve", 10, at, when)
                recorded = "tool_intent" in records(work)
                r = start(work, kind, "resume", second)
                crashes += 1
                paid = bank(work)
                table.append({"kind": kind, "at": at, "when": when, "second": second, "paid": paid, "recorded": recorded,
                              "result": [ln[7:] for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]})
    bad = [t for t in table if t["paid"].count(10) > 1]
    check(f"M1/M3 charged at most once, at every crash point ({crashes} crashes)", not bad, bad[:3])
    bad = [t for t in table if 20 in t["paid"] and t["kind"].startswith("approval")]
    check("M2 a call nobody approved is never charged", not bad, bad[:3])
    bad = [t for t in table if t["kind"].endswith("named") and 20 in t["paid"] and (10 in t["paid"] or t["recorded"])]
    check("M4 once the first call is recorded, a different call under the same key is never charged", not bad, bad[:3])

    double = 0
    for kind in ("approval_named", "plain_named"):
        n, _ = points(kind)
        cases = [(i, w) for i in range(1, n + 1) for w in ("before", "after")] + [(1, "inside")]
        for at, when in cases:
            for at2, when2 in cases:
                work = tempfile.mkdtemp(prefix="c4a-matrix-")
                start(work, kind, "approve", 10, at, when)
                start(work, kind, "resume", 10, at2, when2)
                start(work, kind, "resume", 10)
                double += 1
                if bank(work).count(10) > 1:
                    table.append({"kind": kind, "double": [at, when, at2, when2], "paid": bank(work)})
    bad = [t for t in table if "double" in t]
    check(f"M6 two crashes in a row: charged at most once ({double} pairs)", not bad, bad[:3])
    crashes += 2 * double
    out = Path(tempfile.gettempdir()) / "c4a_crash_matrix.json"
    out.write_text(json.dumps(table))
    print(f"\n{crashes} crashes tested; table in {out}")
    if FAILED:
        print(f"{len(FAILED)} crash-matrix check(s) failed")
        return 1
    print("all crash-matrix checks pass")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(*sys.argv[2:8])
    else:
        sys.exit(main())
