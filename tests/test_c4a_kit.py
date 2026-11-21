"""Call4Agents v0.1 kit - offline checks (0 calls). Run: python -m tests.test_c4a_kit"""

import asyncio
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import call4agents as c4a
from call4agents.ledger import canonical

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def fresh():
    return c4a.init(str(Path(tempfile.mkdtemp(prefix="c4a-test-")) / "ledger.sqlite"), secret=b"test-secret")


K29_CHILD = """
import os, sys, time
from pathlib import Path
import call4agents as c4a
work, mode = Path(sys.argv[1]), sys.argv[2]
c4a.init(str(work / "rec.sqlite"), secret=b"k29-secret")

def pay(amount):
    if mode == "die":
        print(os.getpid(), flush=True)  # the owner pid in the intent (K29e checks it was not reused)
        os._exit(9)                     # killed inside the call: nothing reached the bank
    time.sleep(0.5)
    with open(work / "bank.txt", "a") as f:
        f.write("%s %s\\n" % (amount, os.getpid()))
    return "paid"

def charged(amount):
    p = work / "bank.txt"
    return "paid" if p.exists() and p.read_text().strip() else None

pay = c4a.effect(pay, name="k29.pay", reconcile=charged)
if mode == "race":
    while not (work / "go").exists():
        time.sleep(0.005)
with c4a.run("k29"):
    try:
        print(pay(50))
    except Exception as e:
        print(type(e).__name__, str(e)[:300])
"""


def k29():
    """K29 (Fix 17, found by the TLA+ model and the benchmark): reconcile= is asked only when the attempt's
    owner is provably gone, and an attempt is given up once. Before: 2 payments with a truthful reconcile."""
    import subprocess
    import threading
    import warnings
    from call4agents import owner as c4a_owner

    # a: one call is slower than wait=; a second caller must not ask the provider (it truthfully says "not yet")
    fresh()
    bank, out = [], []
    started, second_done = threading.Event(), threading.Event()

    def slow_pay(amount):
        started.set()
        second_done.wait(10)            # still running until the second caller has finished: no timing guesses
        bank.append(amount)
        return "paid"
    slow_pay = c4a.effect(slow_pay, name="k29.slow", reconcile=lambda amount: "paid" if bank else None, wait=0.1)

    def caller():
        with c4a.run("k29a"), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                out.append(slow_pay(10))
            except c4a.EffectInDoubt as exc:
                out.append(str(exc))
    first = threading.Thread(target=caller)
    first.start()
    started.wait(10)
    try:
        caller()                        # the first call is now provably still running
    finally:
        second_done.set()
    first.join()
    check("K29a a call still running in this process is not reconciled: 1 payment, the other caller is in doubt",
          bank == [10] and "paid" in out and any("still running in this process" in o for o in out), (bank, out))

    # b: the first try raised (nothing happened); two callers then reconcile at the same moment
    lg = fresh()
    bank2, tries, out2 = [], {"n": 0}, []
    both_in = threading.Barrier(2, timeout=5)

    def flaky(amount):
        tries["n"] += 1
        if tries["n"] == 1:
            raise ConnectionError("refused before sending")
        time.sleep(0.2)
        bank2.append(amount)
        return "paid"

    def check_bank(amount):
        found = "paid" if bank2 else None
        both_in.wait()
        return found
    flaky = c4a.effect(flaky, name="k29.flaky", reconcile=check_bank, wait=10.0)
    with c4a.run("k29b"):
        try:
            flaky(10)
        except ConnectionError:
            pass

    def caller2():
        with c4a.run("k29b"):
            try:
                out2.append(flaky(10))
            except Exception as exc:  # noqa: BLE001 - the type is the result
                out2.append(type(exc).__name__)
    ts = [threading.Thread(target=caller2) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    intents = [e for e in lg.entries() if e["kind"] == "tool_intent"]
    check("K29b two callers giving up the same attempt start ONE new attempt: 1 payment, both get its result",
          bank2 == [10] and out2 == ["paid", "paid"] and len(intents) == 2, (bank2, out2, len(intents)))

    # c: giving up is once per attempt, and gives a limit's hold back once
    lg = fresh()
    lg.append("k29c", "limit_reserved", "lim", {"effect": "E", "cost": 5})
    first = lg.give_up("k29c", "E", 1, release=("lim",))
    second = lg.give_up("k29c", "E", 1, release=("lim",))
    released = [e for e in lg.entries() if e["kind"] == "limit_released"]
    check("K29c an attempt is given up once; its hold is released once; the next attempt is 2",
          (first, second, len(released), lg.next_attempt("E")) == (True, False, 1, 2),
          (first, second, len(released), lg.next_attempt("E")))
    lg.append("k29c", "tool_abandoned", "E", {"attempt": 1})     # a duplicate, as records from before 0.8 can have
    check("K29c a duplicate give-up row (older records) does not skip an attempt", lg.next_attempt("E") == 2,
          lg.next_attempt("E"))

    # d: the owner rules
    now = time.time()
    me = c4a_owner.me()
    done_proc = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    dead_pid = int(done_proc.stdout.strip())

    def intent(owner, age):
        return {"payload": {"owner": owner}, "t": now - age}
    cases = {
        "other host, young": c4a_owner.may_still_run("i", intent(dict(me, host=me["host"] + "-x"), 5), 600) is not None,
        "other host, past the lease": c4a_owner.may_still_run("i", intent(dict(me, host=me["host"] + "-x"), 700), 600) is None,
        "our pid, another process before us": c4a_owner.may_still_run("i", intent(dict(me, token="old"), 5), 600) is None,
        "a dead process on this host": c4a_owner.may_still_run("i", intent(dict(me, pid=dead_pid, token="t"), 5), 600) is None,
        "a live process on this host": c4a_owner.may_still_run("i", intent(dict(me, pid=__import__("os").getppid(), token="t"), 5), 600) is not None,
        "this process, call finished": c4a_owner.may_still_run("i", intent(me, 5), 600) is None,
        "written before 0.8 (no owner)": c4a_owner.may_still_run("i", {"payload": {}, "t": now}, 600) is None,
    }
    c4a_owner.started("i")
    cases["this process, call in progress"] = c4a_owner.may_still_run("i", intent(me, 5), 600) is not None
    c4a_owner.finished("i")
    check("K29d the owner check: running only while it can be (pid, token, host, lease)", all(cases.values()),
          [k for k, v in cases.items() if not v])
    try:
        c4a.effect(lambda: None, name="k29.bad", lease=0)
        bad_lease = "accepted"
    except ValueError:
        bad_lease = "refused"
    check("K29d lease=0 is refused when the action is declared", bad_lease == "refused", bad_lease)

    # f (Fix 19, found by the TLA+ model): no gap between the claim and "running in this process"
    lg = fresh()
    bank3, out3, paused = [], [], threading.Event()
    real_claim = lg.claim

    def slow_claim(run_id, key, payload=None):          # thread A stops right after its intent is written
        won = real_claim(run_id, key, payload)
        if won and not paused.is_set():
            paused.set()
            time.sleep(0.5)
        return won
    lg.claim = slow_claim

    def pay3(amount):
        bank3.append(amount)
        return "paid"
    pay3 = c4a.effect(pay3, name="k29.window", reconcile=lambda amount: "paid" if bank3 else None, wait=0.05)

    def caller3():
        with c4a.run("k29f"), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                out3.append(pay3(10))
            except c4a.EffectInDoubt:
                out3.append("in doubt")
    a = threading.Thread(target=caller3)
    a.start()
    paused.wait(10)
    caller3()                                            # thread B arrives while A sits between claim and call
    a.join()
    check("K29f a call paused right after its claim still counts as running here: 1 payment, the other in doubt",
          bank3 == [10] and sorted(out3) == ["in doubt", "paid"], (bank3, out3))

    # e: real processes: one killed inside the call, then two resume at the same moment (twice)
    # (0.8 skeptic, risk 7) A run is repeated, at most twice, only when the killed process's pid was given to a live
    # process before the resumers looked: then "may still be running" is the right answer (0 charges, the safe side
    # the owner check promises), not the case under test. 2 charges are never repeated. The resumers' answers are
    # kept, so a failure shows why.
    charges, seen, reused = [], [], 0
    while len(charges) < 2:
        work = Path(tempfile.mkdtemp(prefix="c4a-k29-"))
        (work / "child.py").write_text(K29_CHILD, encoding="utf-8")
        env = dict(__import__("os").environ, PYTHONPATH=str(Path(c4a.__file__).resolve().parents[1]))
        died = subprocess.run([sys.executable, str(work / "child.py"), str(work), "die"], env=env,
                              capture_output=True, text=True, timeout=180)
        dead_pid = int(died.stdout.split()[0])
        ps = [subprocess.Popen([sys.executable, str(work / "child.py"), str(work), "race"], env=env,
                               stdout=subprocess.PIPE, text=True) for _ in range(2)]
        time.sleep(1.0)
        pid_taken = c4a_owner.pid_alive(dead_pid)
        (work / "go").write_text("go")
        outs = [p.communicate(timeout=180)[0].strip() for p in ps]
        pid_taken = pid_taken or c4a_owner.pid_alive(dead_pid)
        bank_file = work / "bank.txt"
        n = len(bank_file.read_text().splitlines()) if bank_file.exists() else 0
        if n == 0 and pid_taken and reused < 2:
            reused += 1
            continue
        charges.append(n)
        seen.append(outs)
    check("K29e a kill, then two processes resume at once with a truthful reconcile: 1 charge each time",
          charges == [1, 1], (charges, seen, f"repeated {reused} run(s) with a reused pid"))


K30_CHILD = """
import os, sys
from pathlib import Path
import call4agents as c4a
work, mode = Path(sys.argv[1]), sys.argv[2]
c4a.init(str(work / "rec.sqlite"), secret=b"k30-secret-k30-secret")

def pay(amount):
    if mode == "pay_die_before":
        os._exit(9)                     # killed before the bank was reached
    with open(work / "bank.txt", "a") as f:
        f.write("%s\\n" % amount)
    if mode == "pay_die_after":
        os._exit(9)                     # the bank charged; the result is never saved
    return "paid"

def charged(amount):
    p = work / "bank.txt"
    return "paid" if p.exists() and p.read_text().strip() else None

pay = c4a.effect(pay, name="k30.pay", approval=True, reconcile=charged if mode.endswith("_rc") else None)
with c4a.run("k30"):
    if mode.startswith("pay"):
        c4a.approve(pay, ttl=float(sys.argv[3]), amount=10)
    if mode == "approve_again":
        c4a.approve(pay, ttl=60, amount=10)
    try:
        print(pay(amount=10))
    except Exception as e:
        print(type(e).__name__)
"""


def _raw_rows(lg, kind):
    conn = sqlite3.connect(lg.path)
    try:
        return conn.execute("SELECT seq, payload FROM entries WHERE kind = ? ORDER BY seq", (kind,)).fetchall()
    finally:
        conn.close()


def _raw_edit(lg, seq, payload):
    conn = sqlite3.connect(lg.path)
    try:
        with conn:
            conn.execute("UPDATE entries SET payload = ? WHERE seq = ?", (json.dumps(payload), seq))
    finally:
        conn.close()


def _outcome(fn, **kw):
    try:
        return fn(**kw)
    except (c4a.ApprovalRequired, c4a.EffectInDoubt) as e:
        return type(e).__name__ + (":expired" if "expired" in str(e) else "")


def k30():
    """K30 (0.8, PLAN_1_0 item 1): approve(..., ttl=SECONDS). An expired yes admits no NEW action; an action it
    already admitted keeps its behaviour (replay, in doubt, reconcile), never a second run."""
    import hashlib
    import hmac as _hmac
    import subprocess
    paid = []

    def make(name="k30.pay", **kw):
        def pay(amount):
            paid.append(amount)
            return f"paid {amount}"
        return c4a.effect(pay, name=name, approval=True, **kw)

    # a: an expired yes admits nothing; nothing runs, no intent, the yes stays unspent; a new yes works
    lg = fresh()
    pay = make()
    with c4a.run("k30a"):
        c4a.approve(pay, ttl=0.3, amount=10)
        time.sleep(0.5)
        late = _outcome(pay, amount=10)
        intents = lg.count_run_kind("k30a", "tool_intent")
        used = lg.count_run_kind("k30a", "approval_used")
        c4a.approve(pay, ttl=60, amount=10)
        again = _outcome(pay, amount=10)
    check("K30a an expired yes admits no new action: ApprovalRequired saying it expired, nothing runs or is recorded",
          late == "ApprovalRequired:expired" and paid == [10] and intents == 0 and used == 0 and again == "paid 10",
          (late, paid, intents, used, again))

    # b: within its ttl it works like any yes; a retry returns the recorded result
    lg = fresh()
    paid.clear()
    pay = make()
    with c4a.run("k30b"):
        c4a.approve(pay, ttl=60, amount=10)
        first, second = pay(amount=10), pay(amount=10)
        time.sleep(0.01)
    check("K30b a yes inside its ttl admits once; the retry returns the recorded result", (first, second, paid) ==
          ("paid 10", "paid 10", [10]), (first, second, paid))

    # c: without ttl the row is exactly 0.7's (same fields, same MAC text), and a 0.7 row still admits
    lg = fresh()
    paid.clear()
    pay = make()
    with c4a.run("k30c"):
        c4a.approve(pay, amount=10)
        seq, text = _raw_rows(lg, "approval")[0]
        row = json.loads(text)
        old_mac = _hmac.new(b"test-secret", f"{pay.c4a_name}\x00{canonical({'amount': 10})}".encode(),
                            hashlib.sha256).hexdigest()
    check("K30c without ttl the yes is stored exactly as in 0.7 (fields and MAC)",
          set(row) == {"mac", "args"} and row["mac"] == old_mac, row)

    # d: the expiry is inside the MAC: pushing it later, or removing it, makes the yes count for nothing
    results = []
    for change in ("later", "removed"):
        lg = fresh()
        paid.clear()
        pay = make()
        with c4a.run("k30d"):
            c4a.approve(pay, ttl=0.2, amount=10)
            seq, text = _raw_rows(lg, "approval")[0]
            row = json.loads(text)
            if change == "later":
                row["exp"] += 3600
            else:
                del row["exp"]
            _raw_edit(lg, seq, row)
            results.append(_outcome(pay, amount=10))
    check("K30d an expiry edited later or removed in the record is refused (it is signed with the yes)",
          results == ["ApprovalRequired", "ApprovalRequired"] and paid == [], (results, paid))

    # e: admitted, then in doubt (it raised), then the yes expires: still in doubt, a new yes changes nothing
    lg = fresh()
    tries = []

    def flaky(amount):
        tries.append(amount)
        raise ConnectionError("timeout after the charge")
    flaky = c4a.effect(flaky, name="k30.flaky", approval=True)
    with c4a.run("k30e"):
        c4a.approve(flaky, ttl=0.3, amount=10)
        try:
            flaky(amount=10)
        except ConnectionError:
            pass
        time.sleep(0.5)
        after = _outcome(flaky, amount=10)
        c4a.approve(flaky, ttl=60, amount=10)
        after_new_yes = _outcome(flaky, amount=10)
    check("K30e an action in doubt stays in doubt after its yes expires, and a new yes does not run it again",
          after == "EffectInDoubt" and after_new_yes == "EffectInDoubt" and tries == [10], (after, after_new_yes, tries))

    # f: admitted and done, the yes expires: the result is replayed, nothing runs
    lg = fresh()
    paid.clear()
    pay = make()
    with c4a.run("k30f"):
        c4a.approve(pay, ttl=0.3, amount=10)
        pay(amount=10)
        time.sleep(0.5)
        replay = _outcome(pay, amount=10)
    check("K30f a finished action replays its result after its yes expired", replay == "paid 10" and paid == [10],
          (replay, paid))

    # g: ttl must be seconds above 0
    bad = []
    for ttl in (0, -1, True, "5", float("nan"), float("inf")):
        try:
            with c4a.run("k30g"):
                c4a.approve(pay, ttl=ttl, amount=10)
            bad.append(ttl)
        except ValueError:
            pass
    check("K30g ttl= 0, negative, a bool, text, NaN or infinite is refused", not bad, bad)

    # h: real processes: killed during the payment, the yes expires, then the resume
    def child_case(kill, resume, then=None):
        work = Path(tempfile.mkdtemp(prefix="c4a-k30-"))
        (work / "child.py").write_text(K30_CHILD, encoding="utf-8")
        env = dict(__import__("os").environ, PYTHONPATH=str(Path(c4a.__file__).resolve().parents[1]))
        first = subprocess.run([sys.executable, str(work / "child.py"), str(work), kill, "0.5"], env=env,
                               capture_output=True, text=True)
        time.sleep(0.8)                                   # the yes has expired
        outs = [first.returncode]
        for mode in [resume] + ([then] if then else []):
            outs.append(subprocess.run([sys.executable, str(work / "child.py"), str(work), mode], env=env,
                                       capture_output=True, text=True).stdout.strip())
        bank = work / "bank.txt"
        return outs, len(bank.read_text().splitlines()) if bank.exists() else 0
    killed_plain = child_case("pay_die_after", "resume", then="approve_again")
    killed_rc = child_case("pay_die_after", "resume_rc")
    killed_before_rc = child_case("pay_die_before", "resume_rc")
    check("K30h killed after the charge, yes expired: the resume stays in doubt, a new yes does not pay again (1 charge)",
          killed_plain == ([9, "EffectInDoubt", "EffectInDoubt"], 1), killed_plain)
    check("K30h with reconcile the resume returns the recorded payment, it does not pay again (1 charge)",
          killed_rc == ([9, "paid"], 1), killed_rc)
    check("K30h killed before the bank, reconcile proves it did not happen: the admitted action runs once (1 charge)",
          killed_before_rc == ([9, "paid"], 1), killed_before_rc)


def k31():
    """K31 (0.8, PLAN_1_0 item 2): @c4a.effect(version="2"). The version is signed with the yes; a yes for another
    version, or for none, does not run it. Without version= nothing changes, and 0.7 approvals keep working."""
    import hashlib
    import hmac as _hmac
    from call4agents.ops import actions
    paid = []

    def make(version=None, name="k31.pay"):
        def pay(amount):
            paid.append((version, amount))
            return f"paid {amount} v{version}"
        return c4a.effect(pay, name=name, approval=True, version=version)

    # a: a yes for version 1, or for no version, does not run version 2; a yes for version 2 does
    fresh()
    v1, v2, plain = make("1"), make("2"), make(None)
    with c4a.run("k31a"):
        c4a.approve(v1, amount=10)
        c4a.approve(plain, amount=10)
        refused = _outcome(v2, amount=10)
        c4a.approve(v2, amount=10)
        ran = _outcome(v2, amount=10)
    check("K31a a yes for version 1 or for no version does not run version 2; a yes for version 2 does",
          refused == "ApprovalRequired" and ran == "paid 10 v2" and paid == [("2", 10)], (refused, ran, paid))

    # b: without version= a yes without version admits, a versioned yes does not (0.7 behaviour kept)
    fresh()
    paid.clear()
    v2, plain = make("2"), make(None)
    with c4a.run("k31b"):
        c4a.approve(v2, amount=10)
        no = _outcome(plain, amount=10)
        c4a.approve("k31.pay", amount=10)
        yes = _outcome(plain, amount=10)
    check("K31b a tool without version= is admitted by a yes without version only", (no, yes) ==
          ("ApprovalRequired", "paid 10 vNone"), (no, yes, paid))

    # c: a 0.7 row (old MAC text, no version) still admits a tool without version, never a versioned one
    lg = fresh()
    paid.clear()
    old_mac = _hmac.new(b"test-secret", f"k31.pay\x00{canonical({'amount': 10})}".encode(), hashlib.sha256).hexdigest()
    with c4a.run("k31c"):
        lg.append("k31c", "approval", "k31c\x00k31.pay", {"mac": old_mac, "args": {"amount": 10}})
        versioned = _outcome(make("2"), amount=10)
        old_tool = _outcome(make(None), amount=10)
    check("K31c a 0.7 approval row admits the tool without version, not the versioned one",
          (versioned, old_tool) == ("ApprovalRequired", "paid 10 vNone"), (versioned, old_tool))

    # d: the version is inside the MAC: a row edited from 2 to 3 does not run version 3
    lg = fresh()
    paid.clear()
    with c4a.run("k31d"):
        c4a.approve(make("2"), amount=10)
        seq, text = _raw_rows(lg, "approval")[0]
        row = json.loads(text)
        row["version"] = "3"
        _raw_edit(lg, seq, row)
        edited = _outcome(make("3"), amount=10)
        broken = _outcome(make("2"), amount=10)
    check("K31d a version edited in the record counts for neither version", (edited, broken, paid) ==
          ("ApprovalRequired", "ApprovalRequired", []), (edited, broken, paid))

    # e: the version is not part of the key: a finished v1 action is replayed by v2 code, not run again
    fresh()
    paid.clear()
    with c4a.run("k31e"):
        v1 = make("1")
        c4a.approve(v1, amount=10)
        v1(amount=10)
        replay = _outcome(make("2"), amount=10)
    check("K31e a finished version-1 action returns its result to version-2 code and does not run again",
          replay == "paid 10 v1" and paid == [("1", 10)], (replay, paid))

    # f: a no covers every version
    fresh()
    paid.clear()
    with c4a.run("k31f"):
        v2 = make("2")
        c4a.approve(v2, amount=10)
        c4a.deny("k31.pay", amount=10)
        denied = _outcome(v2, amount=10)
    check("K31f a no without version cancels a yes for version 2", denied == "ApprovalRequired" and paid == [],
          (denied, paid))

    # g: tickets carry the version: a ticket for version 2 runs version 2; one for version 1 does not
    lg = fresh()
    paid.clear()
    with c4a.run("k31g"):
        t1 = c4a.pause(make("1"), amount=10)
        c4a.decide(t1, "approve", by="anna")
        v1_ticket = _outcome(make("2"), amount=10)
        t2 = c4a.pause(make("2"), amount=10)
        c4a.decide(t2, "approve", by="anna")
        v2_ticket = _outcome(make("2"), amount=10)
    check("K31g a ticket decided for version 1 does not run version 2; one for version 2 does",
          (v1_ticket, v2_ticket) == ("ApprovalRequired", "paid 10 v2") and t1 != t2, (v1_ticket, v2_ticket))

    # h: in doubt under a version-1 yes: version-2 code does not continue it
    fresh()
    paid.clear()
    tries = []

    def flaky(amount):
        tries.append(amount)
        raise ConnectionError("timeout")
    with c4a.run("k31h"):
        f1 = c4a.effect(flaky, name="k31.flaky", approval=True, version="1",
                        reconcile=lambda amount: None)
        c4a.approve(f1, amount=10)
        try:
            f1(amount=10)
        except ConnectionError:
            pass
        f2 = c4a.effect(flaky, name="k31.flaky", approval=True, version="2", reconcile=lambda amount: None)
        c4a.approve(f2, amount=10)
        cont = _outcome(f2, amount=10)
    check("K31h an action admitted under a version-1 yes and in doubt is not continued by version-2 code",
          cont == "ApprovalRequired" and tries == [10], (cont, tries))

    bad = []
    for v in (2, "", b"2"):
        try:
            c4a.effect(lambda amount: None, name="k31.bad", version=v)
            bad.append(v)
        except ValueError:
            pass
    check("K31i version= must be a non-empty string", not bad, bad)


def k32():
    """K32 (0.8): `with c4a.run(id, ordered=True)`: a new action is refused while another action of the run has no
    final outcome (in doubt, running, or an unchecked late result). Resolving it, or reconcile on its retry, unblocks."""
    import threading
    from call4agents import ops
    from call4agents.ledger import effect_key
    ran = []

    def step(name, fail=False, reconcile=None, approval=False):
        def body(order):
            ran.append(name)
            if fail:
                raise ConnectionError(f"{name}: timeout")
            return f"{name} ok"
        return c4a.effect(body, name=f"k32.{name}", reconcile=reconcile, approval=approval)

    def blocked(rid, ordered, later):
        try:
            with c4a.run(rid, ordered=ordered):
                later(order="A")
            return "ran"
        except c4a.EffectInDoubt as e:
            return "refused:" + ("k32.charge" in str(e) and "ordered run" in str(e) and "nothing ran" in str(e)) * "named"

    # a: an earlier action in doubt refuses a new one; nothing runs, nothing is recorded for it. Control: unordered.
    found = {}
    for ordered in (True, False):
        lg = fresh()
        ran.clear()
        charge, email = step("charge", fail=True), step("email")
        with c4a.run("k32a", ordered=ordered):
            try:
                charge(order="A")
            except ConnectionError:
                pass
        found[ordered] = (blocked("k32a", ordered, email), list(ran),
                          lg.latest("tool_intent", effect_key("k32a", "k32.email", {"order": "A"}) + "#1") is None)
    check("K32a ordered: a new action after one in doubt is refused naming it; nothing runs or is recorded",
          found[True] == ("refused:named", ["charge"], True), found[True])
    check("K32a control: the same run without ordered=True runs the later step (as in 0.7)",
          found[False] == ("ran", ["charge", "email"], False), found[False])

    # b: resolve done, resolve not-done, and reconcile on the retry each unblock
    unblocked = {}
    for how in ("done", "not-done", "reconcile"):
        lg = fresh()
        ran.clear()
        charge = step("charge", fail=True, reconcile=(lambda order: "charge ok") if how == "reconcile" else None)
        email = step("email")
        with c4a.run("k32b", ordered=True):
            try:
                charge(order="A")
            except ConnectionError:
                pass
            before = blocked("k32b", True, email)
            if how == "reconcile":
                charge(order="A")                          # the retry asks the provider: it happened
            else:
                ops.resolve(lg, effect_key("k32b", "k32.charge", {"order": "A"})[:12], how, "checked the bank")
            after = blocked("k32b", True, email)
        unblocked[how] = (before, after, list(ran))
    check("K32b resolve done, resolve not-done, or a reconcile on the retry unblocks the next step",
          all(b == "refused:named" and a == "ran" and r == ["charge", "email"] for b, a, r in unblocked.values()),
          unblocked)

    # c: a late result blocks until a person checks it (`resolve ... done` writes late_checked)
    lg = fresh()
    ran.clear()
    charge, email = step("charge"), step("email")
    with c4a.run("k32c", ordered=True):
        charge(order="A")
        k = effect_key("k32c", "k32.charge", {"order": "A"})
        lg.append("k32c", "late_result", k, {"attempt": 1})
        late = blocked("k32c", True, email)
        listed = len(ops.in_doubt(lg))
        try:
            ops.resolve(lg, k[:12], "done", "no duplicate at the bank")
        except Exception as e:                             # noqa: BLE001 (shown in the check below)
            listed = repr(e)
        checked = blocked("k32c", True, email)
        listed_after = len(ops.in_doubt(lg))
    check("K32c a late result blocks the next step until `resolve ... done` records that it was checked",
          (late, checked, listed, listed_after) == ("refused:named", "ran", 1, 0), (late, checked, listed, listed_after))

    # d: the uncertain action's own retry is not refused, a re-entered run keeps the flag, a refused step keeps its yes
    lg = fresh()
    ran.clear()
    charge = step("charge", fail=True)
    pay = step("pay", approval=True)
    with c4a.run("k32d", ordered=True):
        try:
            charge(order="A")
        except ConnectionError:
            pass
        own = _outcome(charge, order="A")
        c4a.approve(pay, order="A")
        with c4a.run("k32d"):                              # an adapter enters the same run again
            nested = _outcome(pay, order="A")
        spent = lg.count_run_kind("k32d", "approval_used")
    check("K32d the action in doubt itself stays in doubt (not refused as 'earlier'); a nested run keeps ordered; "
          "the refused step's yes stays unspent", own == "EffectInDoubt" and nested == "OrderRefused" and spent == 0
          and ran == ["charge"], (own, nested, spent, ran))

    # f: only a NEW action is refused: the retry of one already started goes on even while another is unsettled
    lg = fresh()
    ran.clear()
    charge = step("charge")
    email = step("email", fail=True, reconcile=lambda order: None)   # the provider says: it did not happen
    with c4a.run("k32f", ordered=True):
        charge(order="A")
        try:
            email(order="A")
        except ConnectionError:
            pass
        lg.append("k32f", "late_result", effect_key("k32f", "k32.charge", {"order": "A"}), {"attempt": 1})
        try:
            email(order="A")                                 # reconcile: not done -> attempt 2 runs
        except ConnectionError:
            pass
        except c4a.EffectInDoubt:
            ran.append("retry refused")
        new = blocked("k32f", True, step("receipt"))
    check("K32f the retry of an action already started is not refused; a new one is (late result unchecked)",
          ran == ["charge", "email", "email"] and new == "refused:named", (ran, new))

    # g (formal review 0.8, TLC bug_ordered_latest_only): attempt 1 reached the provider then raised; the same-key
    # resend (attempt 2) got an error in retry_on. Attempt 2 proves nothing about attempt 1: the action is still
    # uncertain, so the next step is refused, and `doubt` / `story` show it as uncertain.
    lg = fresh()
    ran.clear()

    class Busy(Exception):
        pass
    answers = [ConnectionError("timeout reading the answer"), Busy("429: resend refused")]

    def charge_idem(order):
        ran.append(("charge", c4a.idempotency_key()))
        raise answers.pop(0)
    charge_idem = c4a.effect(charge_idem, name="k32.charge", idempotent_for=60, retry_on=(Busy,))
    with c4a.run("k32g", ordered=True):
        for _ in range(2):
            try:
                charge_idem(order="A")
            except (ConnectionError, Busy):
                pass
        after = blocked("k32g", True, step("email"))
    from call4agents.story import story as _story
    state = [a["state"] for a in ops.actions(lg).values()]
    page = _story(str(lg.path))
    check("K32g attempt 1 raised, the same-key resend got a retry_on error: still uncertain, the next step is refused, "
          "and doubt/story say it may have happened", after == "refused:named" and len(ran) == 2
          and ran[0][1] == ran[1][1] and state == ["IN DOUBT"] and len(ops.in_doubt(lg)) == 1
          and "It may or may not have happened" in page, (after, ran, state, page[-400:]))
    with c4a.run("k32g", ordered=True):
        ops.resolve(lg, effect_key("k32g", "k32.charge", {"order": "A"})[:12], "not-done", "the bank has no charge")
        by_hand = blocked("k32g", True, step("email"))
    check("K32g ... and a person resolving it as not done unblocks the next step", by_hand == "ran", by_hand)

    # e: 8 threads start 8 different actions of one ordered run at once: one runs, 7 are refused (one transaction)
    for ordered in (True, False):
        lg = fresh()
        ran.clear()
        outs = []
        go = threading.Event()

        def slow(order):
            # Hold the action open until every other caller has either run or been refused, so all 8 really
            # overlap on any machine (a fixed sleep let a slow CI thread arrive after the first had finished).
            ran.append(order)
            deadline = time.time() + 10
            while len(ran) + outs.count("OrderRefused") < 8 and time.time() < deadline:
                time.sleep(0.01)
            return "ok"
        slow = c4a.effect(slow, name="k32.slow")

        def racer(i):
            go.wait()
            with c4a.run("k32e", ordered=ordered):
                outs.append(_outcome(slow, order=f"o{i}"))
        ts = [threading.Thread(target=racer, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        go.set()
        [t.join() for t in ts]
        if ordered:
            serial = (len(ran), outs.count("ok"), outs.count("OrderRefused"))
        else:
            parallel = (len(ran), outs.count("ok"))
    check("K32e 8 different actions at once in one ordered run: exactly 1 runs, 7 refused (control unordered: 8 run)",
          serial == (1, 1, 7) and parallel == (8, 8), (serial, parallel))


K33_CHILD = """
import os, sys
from pathlib import Path
import call4agents as c4a
work, step, flag = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
c4a.init(str(work / "rec.sqlite"), secret=b"k33-secret-k33-secret")

def act(order):
    if step == "charge":
        os._exit(9)                      # killed inside the call: the charge is in doubt
    with open(work / "ran.txt", "a") as f:
        f.write(step + "\\n")
    return "ok"

act = c4a.effect(act, name="k33." + step)
with c4a.run("k33e", **({"ordered": True} if flag == "ordered" else {})):
    try:
        print(act(order="A"))
    except c4a.OrderRefused as e:
        print("OrderRefused", e.tool, e.refused_tool)
"""


def k33():
    """K33 (0.8, owner order 21 Nov): the ordered flag is STORED in the run. The first action an ordered=True caller
    tries marks the run (run_ordered, in the admission transaction, kept even when that action is refused); every
    later action of the run is then ordered, also from a caller that forgot the flag. ordered=False on a marked run
    is refused loudly (RunIsOrdered, nothing runs or is recorded)."""
    import os
    import subprocess
    import threading
    from call4agents.ledger import effect_key
    ran = []

    def step(name, fail=False, **kw):
        def body(order, amount=0):
            ran.append(name)
            if fail:
                raise ConnectionError(f"{name}: timeout")
            return f"{name} ok"
        return c4a.effect(body, name=f"k33.{name}", **kw)

    def attempt(rid, fn, flag="unset", **kw):
        """Run fn in ANOTHER thread (a separate caller: no context inherited), entering the run with or without the
        flag. Returns the outcome name."""
        out = []

        def caller():
            try:
                with c4a.run(rid, **({} if flag == "unset" else {"ordered": flag})):
                    fn(order="A", **kw)
                out.append("ran")
            except c4a.OrderRefused as e:
                out.append("refused:" + str(e.tool) + ">" + str(e.refused_tool))
            except c4a.RunIsOrdered:
                out.append("RunIsOrdered")
            except Exception as e:                         # noqa: BLE001 (named in the check)
                out.append(type(e).__name__)
        t = threading.Thread(target=caller)
        t.start()
        t.join()
        return out[0]

    def doubt_first(rid, flag):
        charge = step("charge", fail=True)
        return attempt(rid, charge, flag)                  # ConnectionError: the charge is in doubt

    # a: caller 1 opens the run as ordered; caller 2 (another thread, no flag) is refused. Control: caller 1 unordered
    found = {}
    for flag in (True, "unset"):
        lg = fresh()
        ran.clear()
        doubt_first("k33a", flag)
        email = step("email")
        found[flag] = (attempt("k33a", email), list(ran), lg.is_ordered("k33a"),
                       lg.latest("tool_intent", effect_key("k33a", "k33.email", {"order": "A", "amount": 0}) + "#1") is None)
    check("K33a once a caller opened the run with ordered=True, a caller that forgot the flag is refused (nothing runs "
          "or is recorded)", found[True] == ("refused:k33.charge>k33.email", ["charge"], True, True), found[True])
    check("K33a control: no caller passed ordered=True: the run is not marked and the later step runs",
          found["unset"] == ("ran", ["charge", "email"], False, False), found["unset"])

    # b: ordered=False on a marked run is refused loudly, on every admission path; nothing is written
    lg = fresh()
    ran.clear()
    doubt_first("k33b", True)
    paths = {"plain": step("plain"), "limit": step("limited", limit=c4a.Limit("k33-lim", 5)),
             "approval": step("approved", approval=True),
             "rules": step("ruled", rules=c4a.Rules({"k33.ruled": {"max": {"amount": 10}}}))}
    with c4a.run("k33b"):
        c4a.approve(paths["approval"], order="A")
    before = len(lg.entries())
    loud = {how: attempt("k33b", fn, False, **({"amount": 1} if how == "rules" else {})) for how, fn in paths.items()}
    after = len(lg.entries())
    try:
        with c4a.run("k33b-nested", ordered=True):
            with c4a.run("k33b-nested", ordered=False):
                pass
        nested = "accepted"
    except c4a.RunIsOrdered:
        nested = "RunIsOrdered"
    check("K33b ordered=False on a marked run raises RunIsOrdered on every path (plain, limit, approval, rules); "
          "nothing runs or is recorded; a nested run cannot drop the flag",
          set(loud.values()) == {"RunIsOrdered"} and ran == ["charge"] and after == before
          and nested == "RunIsOrdered", (loud, ran, after - before, nested))

    # c: the mark is written even when the ordered caller's own first action is refused (plain claim path), so a
    # forgetful caller that comes later is refused too. Control: before any ordered caller, nothing is ordered.
    lg = fresh()
    ran.clear()
    doubt_first("k33c", "unset")                           # a forgetful caller's action is in doubt
    early = attempt("k33c", step("early"))                 # still unordered: no caller asked for order yet
    refused_own = attempt("k33c", step("report"), True)    # the ordered caller is refused, and marks the run
    marked = lg.is_ordered("k33c")
    late = attempt("k33c", step("late"))                   # a forgetful caller after the mark: refused
    check("K33c the mark is kept when the ordered caller's first action is refused; a later forgetful caller is "
          "refused; before the mark the run is unordered", (early, refused_own.split(">")[0], marked,
          late.split(">")[0], ran) == ("ran", "refused:k33.charge", True, "refused:k33.charge", ["charge", "early"]),
          (early, refused_own, marked, late, ran))

    # d: each admission path reads the mark (a forgetful caller after the mark, with an action in doubt)
    lg = fresh()
    ran.clear()
    doubt_first("k33d", True)
    paths = {"plain": step("plain"), "limit": step("limited", limit=c4a.Limit("k33d-lim", 5)),
             "approval": step("approved", approval=True),
             "rules": step("ruled", rules=c4a.Rules({"k33.ruled": {"max": {"amount": 10}}}))}
    with c4a.run("k33d"):
        c4a.approve(paths["approval"], order="A")
    each = {how: attempt("k33d", fn, **({"amount": 1} if how == "rules" else {})).split(">")[0]
            for how, fn in paths.items()}
    spent = lg.count_run_kind("k33d", "approval_used")
    check("K33d a forgetful caller is refused on every admission path (plain, limit, approval, rules); the yes "
          "stays unspent", set(each.values()) == {"refused:k33.charge"} and ran == ["charge"] and spent == 0,
          (each, ran, spent))

    # e: real processes: process 1 opens the run ordered and is killed inside the charge; process 2 starts with NO
    # flag and is refused. Control: process 1 without the flag, then process 2 runs.
    outcome = {}
    for flag in ("ordered", "none"):
        work = Path(tempfile.mkdtemp(prefix="c4a-k33-"))
        (work / "child.py").write_text(K33_CHILD, encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(Path(c4a.__file__).resolve().parents[1]))
        subprocess.run([sys.executable, str(work / "child.py"), str(work), "charge", flag], env=env, timeout=120)
        second = subprocess.run([sys.executable, str(work / "child.py"), str(work), "email", "none"], env=env,
                                capture_output=True, text=True, timeout=120)
        outcome[flag] = (second.stdout.strip(), (work / "ran.txt").exists())
    check("K33e real processes: a process killed inside an action of an ordered run; a new process without the flag "
          "is refused (control without the flag: it runs)", outcome == {
              "ordered": ("OrderRefused k33.charge k33.email", False), "none": ("ok", True)}, outcome)

    # f: the refusal names both actions: e.tool / e.call_args = the earlier one (to resolve), e.refused_tool /
    # e.refused_args = the refused one; it is an EffectInDoubt (old handlers still catch it)
    lg = fresh()
    ran.clear()
    doubt_first("k33f", True)
    try:
        with c4a.run("k33f"):
            step("email")(order="B")
        got = None
    except c4a.EffectInDoubt as e:
        got = e
    earlier = effect_key("k33f", "k33.charge", {"order": "A", "amount": 0})
    check("K33f OrderRefused: e.tool is the earlier action, e.refused_tool the refused one, both named in the message",
          isinstance(got, c4a.OrderRefused) and got.tool == "k33.charge" and got.call_args == {"order": "A", "amount": 0}
          and got.refused_tool == "k33.email" and got.refused_args == {"order": "B", "amount": 0} and got.earlier_key == earlier
          and "k33.email" in str(got) and "k33.charge" in str(got), repr(got))

    # g: a nested `with c4a.run(same id)` keeps the outer ordered=True before the run is marked (an adapter enters
    # the run again): a forgetful caller's action is in doubt, the ordered caller's first action is in the nested block
    lg = fresh()
    ran.clear()
    doubt_first("k33g", "unset")
    out = []

    def nested_caller():
        with c4a.run("k33g", ordered=True):
            with c4a.run("k33g"):
                out.append(_outcome(step("email"), order="A"))
    t = threading.Thread(target=nested_caller)
    t.start()
    t.join()
    check("K33g a nested run inside an ordered one keeps the flag (refused, and the run is marked)",
          out == ["OrderRefused"] and ran == ["charge"] and lg.is_ordered("k33g"), (out, ran))


def k34():
    """K34 (0.8 skeptic review): the documented edges of expiry and versions, reproduced so the docs stay true."""
    import call4agents.ledger as ledger_mod
    from call4agents import ops
    from call4agents.ledger import effect_key

    # a (risk 1): a yes with ttl admits an action; attempt 1 PROVES nothing happened (retry_on); after the ttl the
    # retry still runs under the spent yes, once. ttl bounds the spend, not the effect (documented).
    fresh()
    paid = []

    class Busy(Exception):
        pass
    answers = [Busy("429"), None]

    def pay(amount):
        a = answers.pop(0)
        if a:
            raise a
        paid.append(amount)
        return "paid"
    pay = c4a.effect(pay, name="k34.pay", approval=True, retry_on=(Busy,))
    with c4a.run("k34a"):
        c4a.approve(pay, amount=5, ttl=0.3)
        try:
            pay(amount=5)
            first = "ran"
        except Busy:
            first = "Busy"
        time.sleep(0.5)
        late = _outcome(pay, amount=5)
    check("K34a (by design) an admitted action whose first try proved nothing happened runs once after its ttl",
          (first, late, paid) == ("Busy", "paid", [5]), (first, late, paid))

    # b (risk 3): expiry compares the APPROVER's clock + ttl with the ADMITTING process's clock. Simulated skew:
    # an approver 30 s behind gives a 10 s yes that is already expired; one 30 s ahead gives a 1 s yes that is
    # still live after 1 s; an admitter whose clock is set back makes an expired yes live again.
    real = ledger_mod.time.time

    class Clock:
        offset = 0.0

        @staticmethod
        def time():
            return real() + Clock.offset

        def __getattr__(self, name):
            return getattr(__import__("time"), name)
    seen = {}
    ledger_mod.time = Clock()
    try:
        for name, approver, ttl, admitter in (("approver behind", -30, 10, 0), ("approver ahead", 30, 1, 2),
                                              ("admitter set back", 0, 1, -5)):
            fresh()
            fn = c4a.effect(lambda amount: "paid", name=f"k34.skew{len(seen)}", approval=True)
            with c4a.run("k34b"):
                Clock.offset = approver
                c4a.approve(fn, amount=1, ttl=ttl)
                Clock.offset = admitter if name != "admitter set back" else 3      # expired at +3 s ...
                expired_then = _outcome(fn, amount=1) if name == "admitter set back" else None
                Clock.offset = admitter                                           # ... live again at -5 s
                seen[name] = (expired_then, _outcome(fn, amount=1))
    finally:
        ledger_mod.time = __import__("time")
        Clock.offset = 0.0
    check("K34b clock skew moves the expiry by the skew (no second run, no run without a yes)", seen == {
        "approver behind": (None, "ApprovalRequired:expired"), "approver ahead": (None, "paid"),
        "admitter set back": ("ApprovalRequired:expired", "paid")}, seen)

    # c (risk 5): an action started under a version-1 yes and in doubt: version-2 code does not continue it, also
    # after `resolve not-done` and a new version-2 yes; the message says what to do. resolve done: v2 replays.
    tries = []

    def flaky(amount):
        tries.append(amount)
        raise ConnectionError("timeout")
    out = {}
    for how in ("not-done", "done"):
        lg = fresh()
        with c4a.run("k34c"):
            f1 = c4a.effect(flaky, name="k34.flaky", approval=True, version="1")
            c4a.approve(f1, amount=10)
            try:
                f1(amount=10)
            except ConnectionError:
                pass
            f2 = c4a.effect(lambda amount: "v2 paid", name="k34.flaky", approval=True, version="2")
            c4a.approve(f2, amount=10)
            ops.resolve(lg, effect_key("k34c", "k34.flaky", {"amount": 10})[:12], how, "checked")
            c4a.approve(f2, amount=10)
            try:
                out[how] = f2(amount=10)
            except c4a.ApprovalRequired as e:
                out[how] = "ApprovalRequired" + (":explained" if "another version" in str(e) and "c4a_key=" in str(e)
                                                 else "")
    check("K34c a version-1 action in doubt: version-2 code never runs it (after not-done the refusal explains "
          "why); after resolve done it returns the recorded result", out == {"not-done": "ApprovalRequired:explained",
                                                                            "done": None} and tries == [10, 10],
          (out, tries))


def main():
    sent = []

    # K1 once-only: the same call in the same run returns the recorded result, effect runs once
    fresh()

    @c4a.effect
    def send(to, body="x"):
        sent.append(to)
        return f"sent to {to}"
    with c4a.run("r1"):
        a, b = send("bob"), send(to="bob", body="x")
    check("K1 a repeated call (same run, same args) runs the effect once", sent == ["bob"] and a == b, sent)
    with c4a.run("r2"):
        send("bob")
    check("K1b another run is another effect", sent == ["bob", "bob"], sent)

    # K2 in doubt: intent recorded, no result (crash during the effect) -> refuse, never repeat silently
    lg = fresh()
    sent.clear()

    @c4a.effect
    def mail(to):
        sent.append(to)
        return "ok"
    from call4agents.ledger import effect_key, intent_key
    lg.append("r1", "tool_intent", intent_key(effect_key("r1", mail.c4a_name, {"to": "bob"})), {"tool": "mail"})
    try:
        with c4a.run("r1"):
            mail("bob")
        doubt = False
    except c4a.EffectInDoubt:
        doubt = True
    check("K2 a crash during the effect is reported in doubt, not repeated", doubt and sent == [], sent)

    # K3 reconcile decides: found -> recorded, not repeated
    lg = fresh()
    sent.clear()

    @c4a.effect(reconcile=lambda to: "already sent")
    def mail2(to):
        sent.append(to)
        return "ok"
    lg.append("r1", "tool_intent", intent_key(effect_key("r1", mail2.c4a_name, {"to": "bob"})), {"tool": "mail2"})
    with c4a.run("r1"):
        got = mail2("bob")
    check("K3 reconcile() finding the effect stops a repeat", got == "already sent" and sent == [], (got, sent))

    # K25 reconcile() can say "cannot tell yet" (c4a.UNKNOWN): stays in doubt, records nothing, runs nothing;
    # the next try asks again (a lookup that lags behind writes must never trigger a re-run)
    lg = fresh()
    sent.clear()
    answers = [c4a.UNKNOWN, c4a.UNKNOWN, None]

    @c4a.effect(reconcile=lambda to: answers.pop(0))
    def mail3(to):
        sent.append(to)
        return "ok"
    k3 = effect_key("r1", mail3.c4a_name, {"to": "bob"})
    lg.append("r1", "tool_intent", intent_key(k3), {"tool": "mail3"})
    outcomes = []
    for _ in range(3):
        try:
            with c4a.run("r1"):
                outcomes.append(mail3("bob"))
        except c4a.EffectInDoubt as exc:
            outcomes.append("doubt: " + ("could not tell" in str(exc) and "unknown" or str(exc)))
        except Exception as exc:  # noqa: BLE001  (a wrong path must FAIL the check below, not crash the suite)
            outcomes.append(type(exc).__name__)
        if len(outcomes) == 2:
            kinds_after_unknown = [e["kind"] for e in lg.entries("r1")]
    check("K25 reconcile() returning c4a.UNKNOWN keeps the call in doubt and runs nothing",
          outcomes[:2] == ["doubt: unknown"] * 2 and kinds_after_unknown == ["tool_intent"], (outcomes, kinds_after_unknown))
    check("K25b after UNKNOWN, a later 'did not happen' (None) runs it once", outcomes[2] == "ok" and sent == ["bob"],
          (outcomes, sent))
    try:
        bool(c4a.UNKNOWN)
        truthy = True
    except TypeError:
        truthy = False
    check("K25c c4a.UNKNOWN has no truth value (`if reconcile(...)` would treat it as an answer)", not truthy)

    # K26 retries=N: retry only when the record decides the next try; any other error is raised at once
    class Busy(Exception):
        pass

    class NoAnswer(Exception):
        pass

    def k26_call(fn, *a):
        try:
            with c4a.run("r1"):
                return fn(*a)
        except c4a.EffectInDoubt as exc:
            return "doubt: " + str(exc)
        except Exception as exc:  # noqa: BLE001  (a wrong path must FAIL a check, not crash the suite)
            return type(exc).__name__

    lg = fresh()
    tries = []

    @c4a.effect(retry_on=(Busy,), retries=3, backoff=0)
    def k26a(to):
        tries.append(to)
        if len(tries) < 3:
            raise Busy("try later")
        return "sent"
    got = k26_call(k26a, "bob")
    kinds = [e["kind"] for e in lg.entries("r1")]
    check("K26 an error that proves nothing happened is retried, and each try is recorded",
          got == "sent" and len(tries) == 3 and kinds.count("tool_abandoned") == 2 and kinds.count("tool_done") == 1,
          (got, tries, kinds))

    lg = fresh()
    tries = []

    @c4a.effect(retry_on=(Busy,), retries=3, backoff=0)
    def k26b(to):
        tries.append(to)
        raise NoAnswer("no answer")
    first, second = k26_call(k26b, "bob"), k26_call(k26b, "bob")
    check("K26b any other error is raised at once, never retried blind; the next call is in doubt and names it",
          first == "NoAnswer" and second.startswith("doubt") and "NoAnswer" in second and tries == ["bob"],
          (first, second, tries))

    lg = fresh()
    keys, bank = [], {}

    @c4a.effect(idempotent_for=3600, retries=2, backoff=0)
    def k26c(amount):
        k = c4a.idempotency_key()
        keys.append(k)
        bank.setdefault(k, amount)          # the provider charges once per key
        if len(keys) == 1:
            raise NoAnswer("charged, but the answer was lost")
        return "charged"
    got = k26_call(k26c, 10)
    check("K26c with idempotent_for the retry resends the SAME key: the provider charges once",
          got == "charged" and len(keys) == 2 and keys[0] == keys[1] and len(bank) == 1, (got, keys, bank))

    lg = fresh()
    tries = []

    @c4a.effect(retry_on=(Busy,), retries=2, backoff=0)
    def k26d(to):
        tries.append(to)
        raise Busy("still busy")
    got = k26_call(k26d, "bob")
    check("K26d after the last retry the real error is raised, after exactly retries + 1 tries",
          got == "Busy" and len(tries) == 3, (got, tries))

    lg = fresh()
    tries = []

    @c4a.effect(approval=True, retry_on=(Busy,), retries=1, backoff=0)
    def k26e(amount):
        tries.append(amount)
        if len(tries) == 1:
            raise Busy("busy")
        return "paid"
    with c4a.run("r1"):
        c4a.approve(k26e, amount=5)
    got = k26_call(k26e, 5)
    check("K26e an approved action retried after a 'nothing happened' error needs no second yes, and finishes once",
          got == "paid" and tries == [5, 5], (got, tries))

    try:
        @c4a.effect(retries=2)
        def k26f(to):
            return to
        refused = False
    except ValueError:
        refused = True
    check("K26f retries= with no retry_on, idempotent_for or reconcile is refused when the action is declared", refused)

    lg = fresh()
    tries = []

    @c4a.effect(reconcile=lambda to: None, retries=3, backoff=0)
    def k26g(to):
        tries.append(to)
        raise KeyboardInterrupt
    try:
        with c4a.run("r1"):
            k26g("bob")
        stopped = False
    except KeyboardInterrupt:
        stopped = True
    check("K26g a cancel or exit (not an Exception) is never retried", stopped and tries == ["bob"], tries)

    lg = fresh()
    tries = []

    @c4a.effect(reconcile=lambda to: "found it", retries=2, backoff=0)
    def k26h(to):
        tries.append(to)
        raise NoAnswer("timed out after sending")
    got = k26_call(k26h, "bob")
    check("K26h with reconcile the retry asks the provider first: it found the action, so it does not run again",
          got == "found it" and tries == ["bob"], (got, tries))

    import asyncio
    lg = fresh()
    tries = []

    @c4a.effect(retry_on=(Busy,), retries=2, backoff=0)
    async def k26i(to):
        tries.append(to)
        if len(tries) < 2:
            raise Busy("busy")
        return "sent"

    async def k26i_run():
        with c4a.run("r1"):
            return await k26i("bob")
    got = asyncio.run(k26i_run())
    check("K26i the async version retries the same way", got == "sent" and tries == ["bob", "bob"], (got, tries))

    # K27 shared limits: the check and the hold are one transaction with the intent
    def k27_call(fn, *a, run="r1"):
        try:
            with c4a.run(run):
                return fn(*a)
        except c4a.LimitReached:
            return "limit"
        except c4a.EffectInDoubt:
            return "doubt"
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__

    lg = fresh()
    sent.clear()
    three = c4a.Limit("k27-mails", 3)

    @c4a.effect(limit=three)
    def k27a(to):
        sent.append(to)
        return "sent"
    got = [k27_call(k27a, who) for who in ("a", "b", "c", "d")]
    kinds = [e["kind"] for e in lg.entries("r1")]
    check("K27 a limit of 3 actions: the 4th is refused, nothing runs and nothing is recorded for it",
          got == ["sent"] * 3 + ["limit"] and sent == ["a", "b", "c"] and kinds.count("tool_intent") == 3
          and kinds.count("limit_reserved") == 3, (got, sent, kinds))

    lg = fresh()
    paid = []
    budget = c4a.Limit("k27-refunds", 500, cost=lambda amount, **_: amount)

    @c4a.effect(limit=budget)
    def k27b(order, amount):
        paid.append(amount)
        return "refunded"
    got = [k27_call(k27b, o, a) for o, a in (("o1", 200), ("o2", 250), ("o3", 100), ("o4", 50))]
    check("K27b costs add up: 200 + 250 held, 100 more is refused, 50 more fits", got == ["refunded", "refunded",
          "limit", "refunded"] and paid == [200, 250, 50], (got, paid))

    lg = fresh()
    tries = []

    @c4a.effect(limit=c4a.Limit("k27-one", 1), retry_on=(Busy,))
    def k27c(to):
        tries.append(to)
        if to == "busy":
            raise Busy("not sent")
        return "sent"
    got = [k27_call(k27c, "busy"), k27_call(k27c, "bob")]
    check("K27c an error that proves nothing happened gives the cost back", got == ["Busy", "sent"]
          and tries == ["busy", "bob"], (got, tries))

    lg = fresh()
    tries = []

    @c4a.effect(limit=c4a.Limit("k27-doubt", 1))
    def k27d(to):
        tries.append(to)
        if to == "lost":
            raise NoAnswer("sent, maybe")
        return "sent"
    got = [k27_call(k27d, "lost"), k27_call(k27d, "lost"), k27_call(k27d, "bob")]
    check("K27d an action in doubt keeps holding its cost: the next action is refused", got == ["NoAnswer", "doubt",
          "limit"] and tries == ["lost"], (got, tries))

    lg = fresh()
    sent.clear()

    @c4a.effect(limit=c4a.Limit("k27-same", 1))
    def k27e(to):
        sent.append(to)
        return "sent"
    got = [k27_call(k27e, "bob"), k27_call(k27e, "bob")]
    kinds = [e["kind"] for e in lg.entries("r1")]
    check("K27e the same action again returns its result and does not hold the limit twice",
          got == ["sent", "sent"] and sent == ["bob"] and kinds.count("limit_reserved") == 1, (got, sent, kinds))

    lg = fresh()
    paid = []

    @c4a.effect(approval=True, limit=c4a.Limit("k27-approved", 100, cost=lambda amount: amount))
    def k27f(amount):
        paid.append(amount)
        return "paid"
    with c4a.run("r1"):
        c4a.approve(k27f, amount=80)
        c4a.approve(k27f, amount=30)
    got = [k27_call(k27f, 80), k27_call(k27f, 30)]
    kinds = [e["kind"] for e in lg.entries("r1")]
    check("K27f with approvals: over the limit is refused and its yes stays unspent (one transaction)",
          got == ["paid", "limit"] and paid == [80] and kinds.count("approval_used") == 1, (got, paid, kinds))

    lg = fresh()
    sent.clear()

    @c4a.effect(limit=c4a.Limit("k27-window", 1, per=0.3))
    def k27g(to):
        sent.append(to)
        return "sent"
    first, second = k27_call(k27g, "a"), k27_call(k27g, "b")
    time.sleep(0.4)
    third = k27_call(k27g, "c")
    check("K27g per=SECONDS: a hold older than the window no longer counts", (first, second, third) ==
          ("sent", "limit", "sent") and sent == ["a", "c"], (first, second, third, sent))

    lg = fresh()

    @c4a.effect(limit=c4a.Limit("k27-bad", 10, cost=lambda to: -1))
    def k27h(to):
        return "sent"
    bad = [k27_call(k27h, "x")]
    for wrong in (lambda: c4a.Limit("", 1), lambda: c4a.Limit("x", -1), lambda: c4a.Limit("x", 1, per=0)):
        try:
            wrong()
            bad.append("accepted")
        except ValueError:
            bad.append("refused")
    check("K27h a negative cost, an empty name, a negative maximum and per=0 are refused",
          bad == ["ValueError", "refused", "refused", "refused"], bad)

    lg = fresh()
    keys = []

    @c4a.effect(limit=c4a.Limit("k27-resend", 1), idempotent_for=3600, retries=1, backoff=0)
    def k27i(amount):
        keys.append(c4a.idempotency_key())
        if len(keys) == 1:
            raise NoAnswer("charged, answer lost")
        return "charged"
    got = k27_call(k27i, 10)
    kinds = [e["kind"] for e in lg.entries("r1")]
    check("K27i a same-key resend of the same action keeps its one hold (a limit of 1 is enough)",
          got == "charged" and len(keys) == 2 and kinds.count("limit_reserved") == 1, (got, keys, kinds))

    # K28 rules checked against the bound arguments before a new attempt is admitted
    def k28_call(fn, *a, run="r1", **kw):
        try:
            with c4a.run(run):
                return fn(*a, **kw)
        except c4a.RuleRefused as exc:
            return "refused " + exc.rule
        except c4a.ApprovalRequired:
            return "ask"
        except c4a.LimitReached:
            return "limit"
        except c4a.EffectInDoubt:
            return "doubt"
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__

    lg = fresh()
    paid = []
    shop = c4a.Rules({"k28.refund": {"max": {"amount": 500}, "allow": {"currency": ["EUR"]},
                                     "ask_over": {"amount": 100}, "per_run": 2}})

    @c4a.effect(name="k28.refund", rules=shop)
    def k28a(order, amount, currency="EUR"):
        paid.append((order, amount))
        return "refunded"
    got = [k28_call(k28a, "o1", 600), k28_call(k28a, "o2", 50, "USD"), k28_call(k28a, "o3", "50"),
           k28_call(k28a, "o4", float("nan"))]
    kinds = [e["kind"] for e in lg.entries("r1")]
    check("K28 over max, a currency not in the list, an amount that is text or NaN: refused, nothing runs, "
          "no intent, each refusal recorded",
          got == ["refused max.amount", "refused allow.currency", "refused max.amount", "refused max.amount"]
          and paid == [] and kinds.count("tool_intent") == 0 and kinds.count("rule_refused") == 4, (got, paid, kinds))

    got = [k28_call(k28a, "o5", 150)]
    with c4a.run("r1"):
        c4a.approve(k28a, order="o5", amount=150, currency="EUR")
    got += [k28_call(k28a, "o5", 150), k28_call(k28a, "o6", 50)]
    check("K28b over ask_over needs a yes for the exact call even without approval=True; under it runs",
          got == ["ask", "refunded", "refunded"] and paid == [("o5", 150), ("o6", 50)], (got, paid))

    got = [k28_call(k28a, "o7", 10), k28_call(k28a, "o5", 150), k28_call(k28a, "o8", 10, run="r2")]
    check("K28c per_run 2: a third action in the run is refused, a repeat of a finished one returns its "
          "result, another run has its own count",
          got == ["refused per_run", "refunded", "refunded"] and paid == [("o5", 150), ("o6", 50), ("o8", 10)],
          (got, paid))

    intent = [e for e in lg.entries("r1") if e["kind"] == "tool_intent"][0]["payload"]
    check("K28d the intent records the rules' digest and the decision",
          intent.get("rules", {}).get("digest") == shop.digest and intent["rules"]["decision"] == "ask", intent)

    lg = fresh()
    paid = []

    @c4a.effect(name="k28.e", rules=c4a.Rules({"k28.e": {"max": {"amount": 500}}}))
    def k28e_before(amount):
        paid.append(amount)
        return "paid"

    @c4a.effect(name="k28.e", rules=c4a.Rules({"k28.e": {"max": {"amount": 100}}}))
    def k28e_after(amount):
        paid.append(amount)
        return "paid again"
    got = [k28_call(k28e_before, 300), k28_call(k28e_after, 300), k28_call(k28e_after, 200)]
    check("K28e a finished action replays its result after the rules are tightened; a new one is refused",
          got == ["paid", "paid", "refused max.amount"] and paid == [300], (got, paid))

    lg = fresh()
    tries = []

    @c4a.effect(name="k28.f", rules=c4a.Rules({"k28.f": {"per_run": 1}}), retry_on=(Busy,))
    def k28f(to):
        tries.append(to)
        if to in ("busy", "lost"):
            raise Busy("not sent") if to == "busy" else NoAnswer("sent, maybe")
        return "sent"
    got = [k28_call(k28f, "busy"), k28_call(k28f, "lost"), k28_call(k28f, "lost"), k28_call(k28f, "bob")]
    check("K28f per_run: an error that proves nothing happened gives the count back; one in doubt keeps it",
          got == ["Busy", "NoAnswer", "doubt", "refused per_run"] and tries == ["busy", "lost"], (got, tries))

    bad = []
    for section in ({"maxx": {"amount": 5}}, {"max": {"amount": "5"}}, {"allow": {"to": "bob"}}, {"per_run": -1},
                    {"max": {}}):
        try:
            c4a.Rules({"k28.g": section})
            bad.append("accepted")
        except ValueError:
            bad.append("refused")
    try:
        c4a.effect(name="k28.other", rules=c4a.Rules({"k28.g": {"per_run": 1}}))(lambda: None)
        bad.append("accepted")
    except ValueError:
        bad.append("refused")
    check("K28g a misspelt rule, a bound that is text, a bad list, a negative count, an empty max and a tool "
          "with no section are refused when declared", bad == ["refused"] * 6, bad)

    lg = fresh()
    paid = []
    seen = []

    def policy(tool, args):
        seen.append(tool)
        args["to"] = "mallory"                       # a policy that changes its input must not change the call
        if args["amount"] == 13:
            raise RuntimeError("policy server down")
        return {1: "allow", 2: "ask", 3: ("deny", "blocked account"), 4: "yes"}.get(args["amount"], "allow")

    @c4a.effect(name="k28.h", rules=policy)
    def k28h(amount, to="alice"):
        paid.append((amount, to))
        return "paid"
    got = [k28_call(k28h, n) for n in (1, 2, 3, 4, 13)]
    recorded = [e["payload"]["args"] for e in lg.entries("r1") if e["kind"] in ("tool_intent", "rule_refused")]
    check("K28h a policy function: allow runs, ask needs a yes, deny and a crashing or unclear policy refuse "
          "(fails closed), and it cannot change the call or what is recorded",
          got[:2] == ["paid", "ask"] and all(g.startswith("refused policy:") for g in got[2:])
          and paid == [(1, "alice")] and len(seen) == 5 and len(recorded) == 4
          and all(a["to"] == "alice" for a in recorded), (got, paid, recorded))

    lg = fresh()
    paid = []

    @c4a.effect(name="k28.i", rules=c4a.Rules({"k28.i": {"per_run": 5}}),
                limit=c4a.Limit("k28-budget", 100, cost=lambda amount: amount))
    def k28i(amount):
        paid.append(amount)
        return "paid"
    got = [k28_call(k28i, 70), k28_call(k28i, 40)]
    held = [e for e in lg.entries("r1") if e["kind"] == "limit_reserved"]
    check("K28i a rule count and a Limit together: the Limit refusing rolls back the rule's count too",
          got == ["paid", "limit"] and paid == [70] and len(held) == 2, (got, paid, held))

    # K4 approvals bound to exact arguments (key order does not matter, values do)
    fresh()
    paid = []

    @c4a.effect(approval=True)
    def pay(amount, to):
        paid.append((amount, to))
        return "paid"
    with c4a.run("r1"):
        c4a.approve(pay, to="alice", amount=10)
        try:
            pay(9999, "mallory")
            refused = False
        except c4a.ApprovalRequired:
            refused = True
        pay(amount=10, to="alice")
    check("K4 a changed payment after the yes is refused; the approved one runs",
          refused and paid == [(10, "alice")], paid)

    # K5 forging an approval without the secret fails
    lg = fresh()
    paid.clear()
    with sqlite3.connect(lg.path) as conn:
        conn.execute("INSERT INTO entries (run_id, kind, key, payload, t) VALUES (?, 'approval', ?, ?, 0)",
                     ("r1", "r1\x00pay", json.dumps({"mac": "0" * 64, "args": {"amount": 9999, "to": "mallory"}})))
    try:
        with c4a.run("r1"):
            pay(9999, "mallory")
        forged = True
    except c4a.ApprovalRequired:
        forged = False
    check("K5 a hand-written approval entry without the secret is refused", not forged and paid == [], paid)

    # K8 explicit key: a deliberate second identical action runs; the same key does not repeat
    fresh()
    sent.clear()

    @c4a.effect(wait=0.1)
    def remind(to):
        sent.append(to)
        return "ok"
    with c4a.run("r1"):
        remind("bob")
        remind("bob", c4a_key="second reminder")
        remind("bob", c4a_key="second reminder")
        remind("bob")
    check("K8 c4a_key allows a deliberate second identical action, once per key", sent == ["bob", "bob"], sent)

    # K9 race: the same effect from 10 threads at once runs exactly once
    import threading
    import time as _t
    fresh()
    hits, outcomes = [], []

    @c4a.effect
    def slow_send(to):
        hits.append(to)
        _t.sleep(0.05)
        return "sent"

    def caller():
        with c4a.run("race"):
            try:
                outcomes.append(slow_send("bob"))
            except c4a.EffectInDoubt:
                outcomes.append("in_doubt")
    ts = [threading.Thread(target=caller) for _ in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("K9 10 simultaneous callers: the effect runs exactly once, everyone gets its result",
          len(hits) == 1 and outcomes == ["sent"] * 10, (len(hits), outcomes))

    # K10 record, then replay offline: same answers, no model call, no effect re-run
    import tempfile as _tf
    from pathlib import Path as _P
    d = _P(_tf.mkdtemp(prefix="c4a-replay-"))
    calls, mails = [], []

    def make(model_answer):
        @c4a.recorded
        def ask_model(prompt):
            calls.append(prompt)
            return model_answer

        @c4a.effect
        def email(to):
            mails.append(to)
            return "sent"
        return ask_model, email

    c4a.init(str(d / "rec.sqlite"), secret=b"s")
    ask, email = make("email bob")
    with c4a.run("t1"):
        plan = ask("what next?")
        email("bob")
    c4a.init(str(d / "replay.sqlite"), secret=b"s", replay_from=str(d / "rec.sqlite"))
    ask, email = make("SHOULD NOT BE CALLED")
    with c4a.run("t1"):
        plan2 = ask("what next?")
        r = email("bob")
    check("K10 replay returns the recording offline: 1 model call, 1 email in total",
          plan2 == plan and len(calls) == 1 and mails == ["bob"] and r == "sent", (plan2, calls, mails))
    c4a.init(str(d / "replay2.sqlite"), secret=b"s", replay_from=str(d / "rec.sqlite"))   # fresh cursor
    ask, email = make("SHOULD NOT BE CALLED")
    try:
        with c4a.run("t1"):
            ask("a changed prompt")             # call #1 exists, its arguments differ
        outcome = "answered"
    except c4a.ReplayMismatch:
        outcome = "mismatch"
    except c4a.ReplayMissing:
        outcome = "missing"
    check("K10b a changed call in replay is reported as a mismatch, not answered", outcome == "mismatch", outcome)

    # K11 diff: the same task with another model shows the first difference
    c4a.init(str(d / "model_b.sqlite"), secret=b"s")
    ask, email = make("email carol")
    with c4a.run("t1"):
        ask("what next?")
        email("carol")
    same = c4a.diff(str(d / "rec.sqlite"), str(d / "replay.sqlite"), "t1")
    changed = c4a.diff(str(d / "rec.sqlite"), str(d / "model_b.sqlite"), "t1")
    check("K11 diff: replay equals the recording; another model differs at step 1 (its answer)",
          same.get("same") and not changed["same"] and changed["first_difference"] == 1
          and changed["field"] == "result", (same, changed))

    # K12 prove: a real guard is caught, a decorative one is MISSED, the control passes
    real = c4a.prove("tests.c4a_prove_sample:real_guard",
                     [{"module": "tests.c4a_prove_sample", "old": "if amount <= 0:", "new": "if False:"}])
    deco = c4a.prove("tests.c4a_prove_sample:decorative_guard",
                     [{"module": "tests.c4a_prove_sample", "old": "if amount <= 0:", "new": "if False:"}])
    check("K12 prove: real guard proven, decorative guard not proven, control passes",
          real["proven"] and real["control"] == "PASSES" and not deco["proven"]
          and deco["mutations"][0]["result"] == "MISSED", (real, deco))

    # K13 replay keeps types: no silent conversion to text
    import datetime as _dtm
    import decimal as _dec
    from tests.c4a_codec_types import FakeModel, Order
    values = [(1, "a"), bytes([0, 255]), _dtm.datetime(2026, 10, 15, 20, 0), _dec.Decimal("10.50"), {1: "x"},
              {"n", "m"}, Order(7, ("tea",)), FakeModel("hi", 3), {"plain": [1, 2.5, None, True]}]
    d2 = _P(_tf.mkdtemp(prefix="c4a-types-"))
    c4a.init(str(d2 / "rec.sqlite"), secret=b"s")
    produced = []

    @c4a.recorded
    def model_call(i):
        produced.append(i)
        return values[i]
    with c4a.run("types"):
        for i in range(len(values)):
            model_call(i)
    c4a.init(str(d2 / "rep.sqlite"), secret=b"s", replay_from=str(d2 / "rec.sqlite"))
    with c4a.run("types"):
        back = [model_call(i) for i in range(len(values))]
    same = [type(x) is type(y) and x == y for x, y in zip(values, back)]
    check("K13 replay returns the same type and value for 9 kinds of result (tuple, bytes, datetime, Decimal, "
          "int-key dict, set, dataclass, model, JSON)", all(same) and len(produced) == len(values),
          [type(v).__name__ for v, ok in zip(values, same) if not ok])

    class Opaque:
        pass
    c4a.init(str(d2 / "rec2.sqlite"), secret=b"s")

    @c4a.recorded
    def opaque_call():
        return Opaque()
    try:
        with c4a.run("types"):
            opaque_call()
        loud = False
    except TypeError:
        loud = True
    check("K13b a type we cannot keep fails when RECORDED, not silently at replay", loud)

    # K14 an approval is scoped to its run: a "yes" in run A does not approve the same call in run B
    fresh()
    paid.clear()
    with c4a.run("run-A"):
        c4a.approve(pay, amount=10, to="alice")
    try:
        with c4a.run("run-B"):
            pay(amount=10, to="alice")
        crossed = True
    except c4a.ApprovalRequired:
        crossed = False
    check("K14 an approval in one run cannot be used in another run", not crossed and paid == [], paid)

    # K17 one "yes" = one action; a "no" is enforced, also for an action in doubt (research 2026-10:
    # Mastra #26133 double run on concurrent approvals, ADK #7148 deny not enforced)
    def refused(fn, **kw):
        try:
            fn(**kw)
            return False
        except c4a.ApprovalRequired:
            return True
    fresh()
    paid.clear()
    with c4a.run("k17"):
        c4a.approve(pay, amount=10, to="alice")
        pay(amount=10, to="alice")
        pay(amount=10, to="alice")                     # a retry of the SAME action: recorded result
        second = refused(pay, amount=10, to="alice", c4a_key="order-2")
        c4a.approve(pay, amount=10, to="alice")
        pay(amount=10, to="alice", c4a_key="order-2")  # a new "yes" covers the second action
    check("K17 one yes covers one action: a retry reuses it, a deliberate second action needs a new yes",
          second and paid == [(10, "alice"), (10, "alice")], (second, paid))

    fresh()
    paid.clear()
    with c4a.run("k17b"):
        c4a.approve(pay, amount=10, to="alice")
        c4a.deny(pay, amount=10, to="alice")
        no = refused(pay, amount=10, to="alice")
    check("K17b a no after the yes is enforced", no and paid == [], paid)

    def in_doubt_retry(say_no):
        lg = fresh()
        paid.clear()
        rid = "k17c-no" if say_no else "k17c-ctrl"
        with c4a.run(rid):                              # the process died DURING the approved payment
            c4a.approve(pay, amount=10, to="alice")
            from call4agents.ledger import effect_key as _ek, intent_key as _ik
            k = _ek(rid, pay.c4a_name, {"amount": 10, "to": "alice"})
            assert lg.use_approval(rid, pay.c4a_name, {"amount": 10, "to": "alice"}, k)
            lg.claim(rid, _ik(k, 1), {"tool": pay.c4a_name})
            if say_no:
                c4a.deny(pay, amount=10, to="alice")     # the human now says no
            pay_rc = c4a.effect(lambda amount, to: paid.append((amount, to)), approval=True, name=pay.c4a_name,
                                reconcile=lambda **a: None, wait=0.05)   # reconcile: "did not happen" -> retry
            return refused(pay_rc, amount=10, to="alice"), list(paid)
    stopped, paid_no = in_doubt_retry(True)
    ran, paid_ctrl = in_doubt_retry(False)
    check("K17c a no stops the retry of an in-doubt approved action (control: without the no it retries once)",
          stopped and paid_no == [] and not ran and paid_ctrl == [(10, "alice")], (stopped, paid_no, ran, paid_ctrl))
    import threading as _th
    fresh()
    paid.clear()
    with c4a.run("k17d"):
        c4a.approve(pay, amount=10, to="alice")
    outcomes = []

    def racer(i):
        with c4a.run("k17d"):
            outcomes.append("paid" if not refused(pay, amount=10, to="alice", c4a_key=f"click-{i}") else "refused")
    threads = [_th.Thread(target=racer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("K17d 8 simultaneous actions on ONE yes: exactly one runs", len(paid) == 1 and outcomes.count("paid") == 1,
          (paid, outcomes))

    # K18 one check per bug found by an outside tester
    import os as _os
    import subprocess as _sp
    import time as _time
    work = Path(tempfile.mkdtemp(prefix="c4a-k18-"))
    for seed in ("1", "2", "3"):                       # 3 processes, 3 hash seeds: the key must not move
        _sp.run([sys.executable, "-m", "tests.c4a_k18_child", str(work)], cwd=Path(__file__).resolve().parents[1],
                env={**_os.environ, "PYTHONHASHSEED": seed}, check=True, capture_output=True)
    lines = (work / "outbox.txt").read_text(encoding="utf-8").splitlines()
    check("K18a a method with a set argument, resumed in 3 new processes, sends once", len(lines) == 1, lines)

    fresh()
    out18 = []

    def send(to):
        out18.append(("slack", to))
        return "slack"
    send.__module__ = "slack_tools"
    slack_send = c4a.effect(send)

    def send(to):  # noqa: F811 - same function name, another module
        out18.append(("email", to))
        return "email"
    send.__module__ = "email_tools"
    email_send = c4a.effect(send)
    with c4a.run("k18b"):
        r18 = (slack_send("bob"), email_send("bob"))
    check("K18b two tools named `send` in different modules are two tools", r18 == ("slack", "email")
          and out18 == [("slack", "bob"), ("email", "bob")], (r18, out18))

    try:
        email_send("bob")
        outside = "ran"
    except RuntimeError:
        outside = "refused"
    lost = []

    def in_thread():
        try:
            email_send("zed")
            lost.append("ran")
        except RuntimeError:
            lost.append("refused")
    with c4a.run("k18c"):
        th = threading.Thread(target=in_thread)
        th.start()
        th.join()
    check("K18c no silent shared run: outside c4a.run, and in a thread that lost the run id, it refuses",
          outside == "refused" and lost == ["refused"], (outside, lost))

    fresh()
    tries = []

    @c4a.effect
    def flaky(to):
        tries.append(to)
        raise TimeoutError("gateway timeout")
    with c4a.run("k18d"):
        try:
            flaky("bob")
        except TimeoutError:
            pass
        t0 = _time.monotonic()
        try:
            flaky("bob")
            msg = "ran"
        except c4a.EffectInDoubt as e:
            msg = str(e)
        took = _time.monotonic() - t0
    check("K18d an effect that raised is in doubt at once (it may have happened), naming the error",
          tries == ["bob"] and "TimeoutError" in msg and took < 1.0, (tries, msg, round(took, 2)))

    fresh()
    tries.clear()

    @c4a.effect(retry_on=(ConnectionRefusedError,))
    def refused_conn(to):
        tries.append(to)
        if len(tries) == 1:
            raise ConnectionRefusedError("nothing sent")
        return "sent"
    with c4a.run("k18e"):
        try:
            refused_conn("bob")
        except ConnectionRefusedError:
            pass
        try:
            r18e = refused_conn("bob")
        except c4a.EffectInDoubt as e:
            r18e = repr(e)
    check("K18e retry_on: an error that proves nothing happened lets the next call run it", r18e == "sent"
          and tries == ["bob", "bob"], (r18e, tries))

    fresh()
    paid3 = []

    @c4a.effect(approval=True)
    def pay3(amount, to, currency="EUR"):
        paid3.append((amount, to, currency))
        return "paid"
    with c4a.run("k18f"):
        c4a.approve(pay3, amount=10, to="alice")
        try:
            pay3(10, "alice")
        except c4a.ApprovalRequired:
            pass
        try:
            pay3(10, "alice", currency="USD")
            usd = "paid"
        except c4a.ApprovalRequired as e:
            usd = str(e)
    check("K18f a yes without the default argument matches the call; another currency is refused, with a message",
          paid3 == [(10, "alice", "EUR")] and "needs approval" in usd and "USD" in usd, (paid3, usd))

    fresh()
    acount = []

    @c4a.effect
    async def aslow(to):
        acount.append(to)
        await _aio_mod.sleep(0.3)
        return "sent"
    import asyncio as _aio_mod

    async def two_at_once():
        with c4a.run("k18g"):
            return await _aio_mod.gather(aslow("bob"), aslow("bob"))
    try:
        both = _aio_mod.run(two_at_once())
    except c4a.EffectInDoubt as e:
        both = repr(e)
    check("K18g two concurrent async calls of one effect: it runs once, both get the result",
          both == ["sent", "sent"] and acount == ["bob"], (both, acount))

    class Opaque2:
        pass
    fresh()

    @c4a.effect
    def take(obj):
        return "taken"
    with c4a.run("k18h"):
        try:
            take(Opaque2())
            loud18 = False
        except TypeError:
            loud18 = True
    check("K18h an argument with no stable text form fails loudly (it would be resent on every resume)", loud18)

    # K19 a crafted record is data, never code: it cannot make the codec call a function (security review)
    from call4agents import codec as _codec19
    from tests import c4a_codec_types as _ct
    crafted = [
        {"__c4a__": "dataclass", "cls": "tests.c4a_codec_types:canary", "v": {"cmd": "x"}},
        {"__c4a__": "model", "cls": "tests.c4a_codec_types:canary", "v": {"cmd": "x"}},
        {"__c4a__": "model", "cls": "tests.c4a_codec_types:NotAModel", "v": {"cmd": "x"}},
        {"__c4a__": "dataclass", "cls": "os:system", "v": {"command": "echo pwned"}},
        {"__c4a__": "dataclass", "cls": "tests.c4a_codec_types:Order.__init__", "v": {}},
    ]
    refused19 = 0
    for item in crafted:
        try:
            _codec19.decode(item)
        except (ValueError, TypeError):
            refused19 += 1
    ok_dc = _codec19.decode(_codec19.encode(_ct.Order(1, ("a",)))) == _ct.Order(1, ("a",))
    check("K19 crafted records naming a function or a non-model are refused; nothing is called; real types still load",
          refused19 == len(crafted) and _ct.CALLED == [] and ok_dc, (refused19, _ct.CALLED, ok_dc))

    # K20 exclude= leaves a framework context out of the key; the adapters find that parameter anywhere
    from call4agents.adapters._common import context_param, wrap_tool
    fresh()
    sent20 = []

    class FakeCtx:                                     # like a framework context: no stable text form
        def __init__(self, conv):
            self.conversation_id = conv

    def tool20(ctx, to):
        sent20.append(to)
        return "sent"
    t20 = wrap_tool(tool20, ("FakeCtx",), lambda c: c.conversation_id, "test", {"name": "k20.send"})
    try:
        t20(FakeCtx("conv-1"), "bob")
        t20(FakeCtx("conv-1"), "bob")                  # a NEW context object, same conversation: no resend
        t20(FakeCtx("conv-2"), "bob")                  # another conversation: a new action
        err20 = None
    except Exception as exc:  # noqa: BLE001 - reported as a FAIL, not a crash
        err20 = repr(exc)[:120]

    def late(title: str, mcp_context: "Context") -> str:   # MCP style: the context is the LAST parameter
        return title
    found20 = context_param(late, ("Context",))
    check("K20 a context parameter is left out of the key; the run id comes from it; found anywhere in the signature",
          err20 is None and sent20 == ["bob", "bob"] and found20 == "mcp_context", (err20, sent20, found20))

    # K15 async effects and async recorded model calls behave like the sync ones
    import asyncio as _aio
    fresh()
    asent, acalls = [], []

    @c4a.effect
    async def asend(to):
        asent.append(to)
        return "sent"

    @c4a.recorded
    async def amodel(prompt):
        acalls.append(prompt)
        return ("plan", 1)

    async def flow():
        with c4a.run("async"):
            a1 = await amodel("p")
            await asend("bob")
            await asend("bob")
            return a1
    got = _aio.run(flow())
    check("K15 async effect runs once; async recorded call keeps its tuple type",
          asent == ["bob"] and acalls == ["p"] and got == ("plan", 1), (asent, acalls, got))

    # K16 a REAL pydantic v2 model (nested model + datetime) survives record -> replay
    from tests.c4a_codec_types import ModelReply, Usage
    if ModelReply is None:
        print("  skip  K16 pydantic not installed")
    else:
        reply = ModelReply(text="ok", created=_dtm.datetime(2026, 10, 16, 9, 0), usage=Usage(prompt_tokens=3, completion_tokens=1))
        d3 = _P(_tf.mkdtemp(prefix="c4a-pyd-"))
        c4a.init(str(d3 / "rec.sqlite"), secret=b"s")

        @c4a.recorded
        def real_model(q):
            return reply
        with c4a.run("pyd"):
            real_model("q")
        c4a.init(str(d3 / "rep.sqlite"), secret=b"s", replay_from=str(d3 / "rec.sqlite"))
        with c4a.run("pyd"):
            back = real_model("q")
        check("K16 a real pydantic model comes back as the same class with equal fields",
              type(back) is ModelReply and back == reply and isinstance(back.usage, Usage), repr(back)[:80])

    # K6 the offline demo shows the promised numbers
    from call4agents.__main__ import demo
    r = demo()
    nums = [r[k]["emails"] for k in list(r)[:3]]
    check("K6 demo: 2 emails without the record, 1 with it (both crash points)", nums == [2, 1, 1], nums)
    a = r["approval"]
    check("K6b demo: changed payment refused, approved one paid once",
          a["changed_payment"].startswith("REFUSED") and a["approved_payment"].startswith("PAID") and a["payments"] == 1, a)

    # K21 a named action (c4a_key= or key=) reused with other arguments is a conflict, not a cache hit (issue #1)
    import decimal
    fresh()
    k21 = []

    @c4a.effect(name="k21.pay")
    def k21_pay(amount):
        k21.append(amount)
        return f"paid {amount}"

    @c4a.effect(name="k21.charge", key=lambda order, amount: order)
    def k21_charge(order, amount):
        k21.append((order, amount))
        return f"charged {amount}"

    def conflict(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except c4a.KeyConflict as exc:
            return f"conflict: {exc}"

    with c4a.run("k21"):
        first = k21_pay(10, c4a_key="order-7")
        same = [conflict(k21_pay, v, c4a_key="order-7") for v in (10, 10.0, decimal.Decimal("10.00"))]
        other = conflict(k21_pay, 20, c4a_key="order-7")
        k21_charge("o-9", 5)
        other_fn = conflict(k21_charge, "o-9", 6)
    check("K21 the same key with the same amount (10, 10.0, Decimal 10.00) returns the recorded result",
          first == "paid 10" and same == ["paid 10"] * 3, same)
    check("K21 the same key with another amount is a conflict and nothing runs",
          other.startswith("conflict:") and "order-7" in other and k21.count(10) == 1 and 20 not in k21, (other, k21))
    check("K21 the same for a key= function", other_fn.startswith("conflict:") and ("o-9", 6) not in k21, other_fn)

    # K22 a "yes" spent under a named key covers only the arguments it was given for (issue #1)
    import threading
    k22 = []

    @c4a.effect(name="k22.pay", approval=True)
    def k22_pay(amount):
        k22.append(amount)
        return f"paid {amount}"

    @c4a.effect(name="k22.send")
    def k22_send(amount):
        k22.append(("send", amount))
        return f"sent {amount}"

    def outcome(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (c4a.ApprovalRequired, c4a.KeyConflict) as exc:
            return type(exc).__name__

    # A legacy record can have a spent "yes" without an intent. It must remain resumable.
    lg = fresh()
    with c4a.run("k22"):
        c4a.approve(k22_pay, amount=10)
        from call4agents.ledger import effect_key
        old_key = effect_key("k22", "k22.pay", {"__c4a_key__": "order-1"})
        assert lg.use_approval("k22", "k22.pay", {"amount": 10}, old_key)
    with c4a.run("k22"):
        other = outcome(k22_pay, 20, c4a_key="order-1")
        again = outcome(k22_pay, 10, c4a_key="order-1")
    check("K22 after a crash, a spent yes does not cover other arguments under the same key",
          other == "ApprovalRequired" and 20 not in k22, (other, k22))
    check("K22 the approved call can still finish after that crash", again == "paid 10" and k22 == [10], (again, k22))

    def race(fn, slow_args, a_args, b_args, setup=None):
        """Call A has passed its checks and waits just before recording its intent while B runs."""
        lg = fresh()
        k22.clear()
        claim = lg.claim_approved if fn is k22_pay else lg.claim
        ready, go, out = threading.Event(), threading.Event(), {}

        def slow(*args):
            payload = args[-1]
            if payload and payload["args"] == slow_args:
                ready.set()
                go.wait(10)
            return claim(*args)

        if fn is k22_pay:
            lg.claim_approved = slow
        else:
            lg.claim = slow
        with c4a.run("k22"):
            if setup:
                setup()

        def first():
            with c4a.run("k22"):
                out["a"] = outcome(fn, *a_args, c4a_key="order-2")

        t = threading.Thread(target=first)
        t.start()
        ready.wait(10)
        with c4a.run("k22"):
            out["b"] = outcome(fn, *b_args, c4a_key="order-2")
        go.set()
        t.join()
        return out

    out = race(k22_pay, {"amount": 10}, (10,), (20,), lambda: c4a.approve(k22_pay, amount=10))
    check("K22 two threads: the unapproved amount is refused and the approved one is paid",
          out == {"a": "paid 10", "b": "ApprovalRequired"} and k22 == [10], (out, k22))
    out = race(k22_send, {"amount": 1}, (1,), (2,))
    check("K22 two threads without approval: one runs, the other gets a conflict, not the winner's result",
          out == {"a": "KeyConflict", "b": "sent 2"} and k22 == [("send", 2)], (out, k22))

    # K23 `call4agents incident`: the numbers shown to people come from a real run
    from call4agents.examples.approval_incident import run_all
    inc = run_all()
    charges = {name: (len(r["without"]["charges"]), len(r["with"]["charges"])) for name, r in inc.items()}
    check("K23 incident demo: 2 charges without, 1 with; a changed call or a no pays nothing with the kit",
          charges == {"double_payment": (2, 1), "crash_inside": (2, 1), "changed_call": (1, 0), "ignored_no": (1, 0)}
          and inc["changed_call"]["without"]["charges"] == ["9999 mallory"]
          and inc["crash_inside"]["with"]["stopped"] == "EffectInDoubt"
          and inc["ignored_no"]["with"]["stopped"] == "ApprovalRequired", charges)

    # K24 a cancelled async action may already have reached the outside world: it is in doubt, never run again
    k24 = []
    k24_started = asyncio.Event()

    @c4a.effect(name="k24.refund")
    async def k24_refund(order):
        k24.append(order)
        k24_started.set()
        await asyncio.sleep(5)

    async def k24_run():
        with c4a.run("k24"):
            task = asyncio.create_task(k24_refund("A"))
            await asyncio.wait_for(k24_started.wait(), timeout=10)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            try:
                await k24_refund("A")
                return "ran"
            except c4a.EffectInDoubt as e:
                return str(e)

    said = asyncio.run(k24_run())
    check("K24 a cancelled action is in doubt at once, names the cancel, and does not run again",
          "CancelledError" in said and k24 == ["A"], (said, k24))

    k29()
    k30()
    k31()
    k32()
    k33()
    k34()

    # K7 the core imports only the standard library
    import ast
    std = set(sys.stdlib_module_names)
    bad = []
    for f in Path(c4a.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mods = [n.name for n in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else []
            bad += [m for m in mods if m.split(".")[0] not in std and not m.startswith("call4agents")]
    check("K7 the core uses only the Python standard library", not bad, bad)

    print(f"\n{len(FAILED)} kit check(s) failed" if FAILED else "\nall kit checks pass")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
