"""Reproduce the TLC-predicted double effect with reconcile= and two live callers (real library, offline)."""
import sys
import tempfile
import threading
import time
import warnings
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import call4agents as c4a  # noqa: E402


def scenario_slow_effect():
    """No crash, no error: one caller's effect is slower than `wait`; the other reconciles and runs it again."""
    work = Path(tempfile.mkdtemp(prefix="c4a-race1-"))
    lg = c4a.init(str(work / "r.sqlite"), secret=b"s" * 16)
    bank = []

    def pay(amount):
        time.sleep(0.5)
        bank.append(amount)
        return "paid"

    def check(amount):
        return "paid" if bank else None          # truthful: the bank really has no payment yet

    pay = c4a.effect(pay, name="t.pay", reconcile=check, wait=0.1)
    out = []

    def caller(delay):
        time.sleep(delay)
        with c4a.run("run-1"):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                try:
                    out.append(pay(10))
                except Exception as exc:
                    out.append(type(exc).__name__)
                out.extend(str(x.message)[:60] for x in w)

    ts = [threading.Thread(target=caller, args=(d,)) for d in (0.0, 0.05)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    kinds = [e["kind"] for e in lg.entries()]
    return bank, out, kinds


def scenario_error_then_two_callers():
    """The first try raises an error before anything happened (tool_error). Two callers then retry at the
    same time, wait=10 s (longer than the effect), reconcile is truthful. Both reconcile, both get None."""
    work = Path(tempfile.mkdtemp(prefix="c4a-race2-"))
    lg = c4a.init(str(work / "r.sqlite"), secret=b"s" * 16)
    bank, first = [], {"n": 0}
    both_in = threading.Barrier(2, timeout=5)

    def pay(amount):
        first["n"] += 1
        if first["n"] == 1:
            raise ConnectionError("refused before sending")   # not in retry_on: the kit records tool_error
        time.sleep(0.2)
        bank.append(amount)
        return "paid"

    def check(amount):
        found = "paid" if bank else None
        both_in.wait()                          # both callers are inside reconcile at the same moment
        return found

    pay = c4a.effect(pay, name="t.pay2", reconcile=check, wait=10.0)
    with c4a.run("run-2"):
        try:
            pay(10)
        except ConnectionError:
            pass
    out = []

    def caller():
        with c4a.run("run-2"):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                try:
                    out.append(pay(10))
                except Exception as exc:
                    out.append(type(exc).__name__)
                out.extend(str(x.message)[:60] for x in w)

    ts = [threading.Thread(target=caller) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    kinds = [(e["kind"], (e["payload"] or {}).get("attempt")) for e in lg.entries()]
    return bank, out, kinds


if __name__ == "__main__":
    b, o, k = scenario_slow_effect()
    print("S1 slow effect:   bank =", b, "| callers:", o)
    print("   record:", k)
    b2, o2, k2 = scenario_error_then_two_callers()
    print("S2 error + 2 callers: bank =", b2, "| callers:", o2)
    print("   record:", k2)
