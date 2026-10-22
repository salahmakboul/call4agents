"""One run record shared by several machines (PostgreSQL). Run: python -m tests.test_shared_record

Needs psycopg 3 and a server: C4A_TEST_PG_DSN, or pgserver (pip install "psycopg[binary]" pgserver), which
starts a throwaway local PostgreSQL. Each "machine" below is a separate process with its own connection,
so nothing is shared except the database. Offline, 0 LLM calls.

S1 8 machines call the same effect at once: it runs exactly once, and all 8 get its result.
S2 a long effect is still running on machine A when the run is sent again to machine B (the shape of
   langgraph#7417): B does not run it, it raises EffectInDoubt; a later machine C gets A's recorded result.
S3 machine A dies during the effect: machine B raises EffectInDoubt and does not run it again.
S4 a "yes" given on machine A counts on machine B for exactly those arguments, not for other arguments,
   and not on a machine that has a different secret.
S5 a shared record without a secret is refused (a local secret file would differ per machine).
S6 the password in the connection string never appears in an error message.
S7 keys with NUL (approval keys use it as a separator) are stored without NUL and come back unchanged."""
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


# ------------------------------------------------------------------ one machine (a child process)
def machine(role, dsn, work, run_id, secret):
    import call4agents as c4a

    work = Path(work)

    def side(tag):
        with open(work / "side.txt", "a") as f:
            f.write(tag + "\n")

    @c4a.effect(name="s.send", wait=30)
    def send(to):
        side("send " + to)
        time.sleep(0.5)                   # long enough for every racer to arrive while it runs
        return "sent to " + to

    @c4a.effect(name="s.long", wait=0.5)
    def long_task(job):
        (work / "a_started").write_text("1")
        side("long " + job)
        time.sleep(4)
        return "done " + job

    @c4a.effect(name="s.crash")
    def crashing(job):
        side("crash " + job)
        os._exit(9)                       # the effect happened; no result is recorded

    @c4a.effect(name="s.pay", approval=True)
    def pay(amount, to):
        side(f"pay {amount} {to}")
        return "paid"

    c4a.init(dsn, secret=secret.encode())
    with c4a.run(run_id):
        try:
            if role in ("race",):
                print("RESULT", send("bob"))
            elif role in ("long", "later"):
                print("RESULT", long_task("j1"))
            elif role == "crash":
                crashing("j1")
            elif role == "approve":
                c4a.approve(pay, amount=10, to="alice")
                print("RESULT approved")
            elif role == "pay":
                print("RESULT", pay(10, "alice"))
            elif role == "pay_other":
                print("RESULT", pay(9999, "mallory"))
        except c4a.EffectInDoubt:
            print("RESULT IN_DOUBT")
        except c4a.ApprovalRequired:
            print("RESULT APPROVAL_REQUIRED")


def start(role, dsn, work, run_id, secret="s" * 24):
    return subprocess.Popen([sys.executable, "-m", "tests.test_shared_record", "machine", role, dsn, str(work),
                             run_id, secret], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def result(p, timeout=60):
    out, err = p.communicate(timeout=timeout)
    lines = [ln[7:] for ln in out.splitlines() if ln.startswith("RESULT ")]
    return lines[-1] if lines else f"(exit {p.returncode}) {err.strip()[-300:]}"


def side_lines(work):
    p = Path(work) / "side.txt"
    return p.read_text().splitlines() if p.exists() else []


# ------------------------------------------------------------------ the checks
def server_dsn():
    if os.environ.get("C4A_TEST_PG_DSN"):
        return os.environ["C4A_TEST_PG_DSN"], None
    import pgserver
    srv = pgserver.get_server(tempfile.mkdtemp(prefix="c4a-pg-"), cleanup_mode="stop")
    return srv.get_uri(), srv


def main():
    dsn, srv = server_dsn()
    new = lambda: (Path(tempfile.mkdtemp(prefix="c4a-shared-")), "run-" + uuid.uuid4().hex[:8])

    # S1
    work, run_id = new()
    procs = [start("race", dsn, work, run_id) for _ in range(8)]
    outs = [result(p) for p in procs]
    check("S1 8 machines, one effect: it ran once", side_lines(work) == ["send bob"], side_lines(work))
    check("S1 all 8 machines got its result", outs == ["sent to bob"] * 8, outs)

    # S2
    work, run_id = new()
    a = start("long", dsn, work, run_id)
    deadline = time.monotonic() + 30
    while not (work / "a_started").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    b = result(start("long", dsn, work, run_id))
    a_out = result(a)
    c = result(start("later", dsn, work, run_id))
    check("S2 the second machine does not run it while it is still running", b == "IN_DOUBT", b)
    check("S2 the first machine finishes normally", a_out == "done j1", a_out)
    check("S2 a later machine gets the recorded result", c == "done j1", c)
    check("S2 the effect ran once", side_lines(work) == ["long j1"], side_lines(work))

    # S3
    work, run_id = new()
    first = start("crash", dsn, work, run_id)
    first.communicate(timeout=60)
    b = result(start("crash", dsn, work, run_id))
    check("S3 after a crash on machine A, machine B raises EffectInDoubt", b == "IN_DOUBT", b)
    check("S3 the effect ran once", side_lines(work) == ["crash j1"], side_lines(work))

    # S4
    work, run_id = new()
    result(start("approve", dsn, work, run_id))
    other = result(start("pay_other", dsn, work, run_id))
    stranger = result(start("pay", dsn, work, run_id, secret="o" * 24))
    paid = result(start("pay", dsn, work, run_id))
    check("S4 a yes from machine A is refused for other arguments", other == "APPROVAL_REQUIRED", other)
    check("S4 a yes is refused on a machine with a different secret", stranger == "APPROVAL_REQUIRED", stranger)
    check("S4 a yes from machine A counts on machine B", paid == "paid", paid)
    check("S4 only the approved payment ran", side_lines(work) == ["pay 10 alice"], side_lines(work))

    # S5, S6
    from call4agents.ledger import LedgerError
    from call4agents.stores.postgres import PostgresLedger
    old = os.environ.pop("C4A_SECRET", None)
    try:
        PostgresLedger(dsn)
        check("S5 a shared record without a secret is refused", False, "no error")
    except LedgerError as exc:
        check("S5 a shared record without a secret is refused", "same secret" in str(exc), str(exc))
    finally:
        if old is not None:
            os.environ["C4A_SECRET"] = old
    try:
        PostgresLedger("postgresql://someone:TOPSECRETPW@127.0.0.1:1/nothing?connect_timeout=2", secret=b"x" * 16)
        check("S6 the password is not shown in errors", False, "no error")
    except LedgerError as exc:
        check("S6 the password is not shown in errors", "TOPSECRETPW" not in str(exc), str(exc))

    # S7
    from call4agents.stores.postgres import _pg_in, _pg_out
    samples = ["", "a", "\x00", "\x01", "\x01\x02", "\x00\x01", "run\x00tool", "run\x01\x02tool", "\x01\x01\x00"]
    check("S7 text with NUL comes back unchanged", all(_pg_out(_pg_in(s)) == s for s in samples))
    check("S7 no two texts are stored the same way", len({_pg_in(s) for s in samples}) == len(samples))
    check("S7 nothing stored contains NUL", all("\x00" not in _pg_in(s) for s in samples))

    if srv is not None:
        srv.cleanup()
    if FAILED:
        print(f"{len(FAILED)} shared-record check(s) FAILED")
        sys.exit(1)
    print("all shared-record checks pass")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "machine":
        machine(*sys.argv[2:7])
    else:
        main()
