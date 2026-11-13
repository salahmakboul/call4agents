# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Trace check: the real library, checked against the invariants of the TLA+ model. Run: python -m tests.test_formal_trace

formal/Call4Agents.tla proves its invariants for a MODEL of the code. This module links the model to the code:
it runs the real call4agents through crash and resume scenarios (real processes killed with os._exit before
and after every write to the record, and inside the action, like tests/test_crash_matrix.py), and checks every
record and the fake provider's payments against the same invariants, written in Python:

T1 AtMostOnce          every action is paid at most once.
T2 NoUnapprovedEffect  every tool_intent was written after an effect_approved that spent a "yes" (approval_used)
                       for exactly its arguments on this action, with no "no" for those arguments between the
                       "yes" and the intent.
T3 OneYesOneAction     each "yes" is spent once (one approval_used), by one action.
T4 DoneIsTrue          a recorded result (tool_done) means the provider has the payment.
T5 AppendOnly          the record after each phase starts with the record of the phase before, unchanged.
T6 two threads, one slow call that is still running, reconcile= truthful: paid once (Fix 17 owner check).
T7 the checker itself sees a broken record (a control: a double payment, an intent after a "no", two
   effects on one "yes", a result without payment, a changed row) - otherwise T1-T5 would prove nothing.
Offline, 0 LLM calls. Uses no part of TLA+ or Java.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

FAILED = []
ROOT = Path(__file__).resolve().parents[1]
TOOL = "trace.pay"
SECRET = b"t" * 16
KINDS = ("approval_reconcile", "approval_retry_on", "plain_reconcile")


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


# ------------------------------------------------------------------ the invariants, on a real record
def rows(path):
    if not Path(path).exists():
        return []
    db = sqlite3.connect(path)
    try:
        out = []
        for seq, kind, key, payload in db.execute("SELECT seq, kind, key, payload FROM entries ORDER BY seq"):
            out.append({"seq": seq, "kind": kind, "key": key, "payload": json.loads(payload) if payload else None})
        return out
    finally:
        db.close()


def violations(entries, payments, mac_of, label_of_base, approval):
    """Every invariant broken by this record and these payments, as text. Empty = all hold."""
    bad = []
    for label, n in Counter(payments).items():                                   # T1
        if n > 1:
            bad.append(f"AtMostOnce: {label} paid {n} times")
    by_seq = {e["seq"]: e for e in entries}
    if approval:                                                                  # T2
        for e in entries:
            if e["kind"] != "tool_intent":
                continue
            base = e["key"].rsplit("#", 1)[0]
            mac = mac_of(e["payload"]["args"])
            ok = False
            for x in entries:
                if x["kind"] != "effect_approved" or x["key"] != base or x["seq"] >= e["seq"]:
                    continue
                s = x["payload"]["approval"]
                yes = by_seq.get(s)
                if yes is None or yes["kind"] != "approval" or yes["payload"].get("mac") != mac:
                    continue
                used = any(u["kind"] == "approval_used" and u["key"].endswith("\x00" + str(s))
                           and u["payload"].get("effect") == base and u["seq"] < x["seq"] for u in entries)
                no_between = any(d["kind"] == "denial" and s < d["seq"] < e["seq"] and d["payload"].get("mac") == mac
                                 for d in entries)
                ok = ok or (used and not no_between)
            if not ok:
                bad.append(f"NoUnapprovedEffect: intent {e['key'][-8:]} (seq {e['seq']}) has no live yes")
    spent = Counter(u["key"] for u in entries if u["kind"] == "approval_used")   # T3
    bad += [f"OneYesOneAction: approval {k.rsplit(chr(0), 1)[-1]} spent {n} times" for k, n in spent.items() if n > 1]
    actions = {}
    for x in entries:
        if x["kind"] == "effect_approved":
            actions.setdefault(x["payload"]["approval"], set()).add(x["key"])
    bad += [f"OneYesOneAction: approval {s} admitted {len(ks)} actions" for s, ks in actions.items() if len(ks) > 1]
    for d in entries:                                                             # T4
        if d["kind"] == "tool_done" and label_of_base(d["key"]) not in payments:
            bad.append(f"DoneIsTrue: result for {label_of_base(d['key'])} without a payment")
    return bad


def prefix_ok(before, after):                                                     # T5
    return after[:len(before)] == before


# ------------------------------------------------------------------ one process of a scenario
def child(work, kind, step, crash_at, when):
    """step: approve | deny | resume. crash_at counts writes to the record: dies just before or after it."""
    import call4agents as c4a
    work, crash_at = Path(work), int(crash_at)
    lg = c4a.init(str(work / "record.sqlite"), secret=SECRET)
    writes = {"n": 0}
    insert = lg._insert

    def counted(*args, **kwargs):
        writes["n"] += 1
        if writes["n"] == crash_at and when == "before":
            os._exit(9)
        out = insert(*args, **kwargs)
        if writes["n"] == crash_at and when == "after":
            os._exit(9)
        return out

    lg._insert = counted
    bank = work / "bank.txt"

    def paid():
        return bank.read_text().split() if bank.exists() else []

    def pay(amount):
        if when == "refuse" and step != "resume":
            raise ConnectionRefusedError("refused before sending")         # in retry_on: nothing happened
        with open(bank, "a") as f:
            f.write(f"order-{amount}\n")
        if when == "inside":
            os._exit(9)
        if when == "raise_after" and step != "resume":
            raise TimeoutError("timeout after the provider took it")        # not in retry_on: in doubt
        return "paid"

    def found(amount):
        return "paid" if f"order-{amount}" in paid() else None              # truthful

    opts = {"name": TOOL, "approval": kind.startswith("approval")}
    if kind.endswith("reconcile"):
        opts.update(reconcile=found, wait=0.05)
    else:
        opts.update(retry_on=(ConnectionRefusedError,), wait=0.05)
    pay = c4a.effect(pay, **opts)
    with c4a.run("run-1"):
        if step == "approve":
            c4a.approve(pay, amount=10)
        if step == "deny":
            c4a.deny(pay, amount=10)
        try:
            print("RESULT", pay(10, c4a_key="order-10"))
        except Exception as exc:                                             # refused or in doubt: fine here
            print("RESULT", type(exc).__name__)


def start(work, kind, step, crash_at=0, when="none"):
    return subprocess.run([sys.executable, "-m", "tests.test_formal_trace", "--child", str(work), kind, step,
                           str(crash_at), when], capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})


def helpers():
    import call4agents as c4a
    from call4agents.ledger import effect_key
    lg = c4a.Ledger(str(Path(tempfile.mkdtemp(prefix="c4a-trace-mac-")) / "m.sqlite"), SECRET)
    base = effect_key("run-1", TOOL, {"__c4a_key__": "order-10"})

    def mac_of(args):
        return lg.sign(TOOL, args)

    def label_of_base(k):
        return "order-10" if k == base else k
    return mac_of, label_of_base


def scenarios():
    out = []
    for kind in KINDS:
        cases = [(i, w) for i in range(1, 7) for w in ("before", "after")] + [(1, "inside"), (0, "raise_after")]
        if kind == "approval_retry_on":
            cases.append((0, "refuse"))
        for at, when in cases:
            for second in ("resume", "deny") if kind.startswith("approval") else ("resume",):
                out.append((kind, at, when, second))
    return out


def main():
    mac_of, label_of_base = helpers()
    total, broken = 0, []
    for kind, at, when, second in scenarios():
        work = Path(tempfile.mkdtemp(prefix="c4a-trace-"))
        record = work / "record.sqlite"
        start(work, kind, "approve", at, when)
        snaps = [rows(record)]
        start(work, kind, second)
        snaps.append(rows(record))
        start(work, kind, "resume")
        snaps.append(rows(record))
        payments = (work / "bank.txt").read_text().split() if (work / "bank.txt").exists() else []
        bad = violations(snaps[-1], payments, mac_of, label_of_base, kind.startswith("approval"))
        bad += [f"AppendOnly: phase {i + 1} changed the record" for i in range(2) if not prefix_ok(snaps[i], snaps[i + 1])]
        total += 1
        if bad:
            broken.append({"kind": kind, "at": at, "when": when, "second": second, "bad": bad[:3]})
    check(f"T1-T5 every record and payment list keeps the model's invariants ({total} crash/resume scenarios, "
          f"{3 * total} processes)", not broken, broken[:3])
    check("T6 a call still running is not reconciled by a second caller (paid once)", live_owner() == 1)
    control()
    if FAILED:
        print(f"{len(FAILED)} trace check(s) failed")
        return 1
    print("all trace checks pass")
    return 0


def live_owner():
    """Two threads of one process, the first call slower than `wait`, a truthful reconcile (0.7.1 paid twice)."""
    import call4agents as c4a
    work = Path(tempfile.mkdtemp(prefix="c4a-trace-live-"))
    c4a.init(str(work / "r.sqlite"), secret=SECRET)
    bank = []

    def slow_pay(amount):
        time.sleep(0.6)
        bank.append(amount)
        return "paid"

    pay = c4a.effect(slow_pay, name="trace.slow", reconcile=lambda amount: "paid" if bank else None, wait=0.05)

    def caller(delay):
        time.sleep(delay)
        with c4a.run("run-live"):
            try:
                pay(10)
            except Exception:  # noqa: BLE001 - the second caller is expected to get EffectInDoubt
                pass
    ts = [threading.Thread(target=caller, args=(d,)) for d in (0.0, 0.15)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return len(bank)


def control():
    """T7: the checker must see each kind of broken record, or a pass above means nothing."""
    mac_of, label_of_base = helpers()
    from call4agents.ledger import effect_key
    base = effect_key("run-1", TOOL, {"__c4a_key__": "order-10"})
    mac = mac_of({"amount": 10})
    used = "run-1\x00" + TOOL + "\x00"

    def e(seq, kind, key, payload):
        return {"seq": seq, "kind": kind, "key": key, "payload": payload}
    good = [e(1, "approval", "a", {"mac": mac}), e(2, "approval_used", used + "1", {"effect": base}),
            e(3, "effect_approved", base, {"approval": 1}), e(4, "tool_intent", base + "#1", {"args": {"amount": 10}}),
            e(5, "tool_done", base, {"result": "paid"})]
    check("T7 control: a good record has no violation", not violations(good, ["order-10"], mac_of, label_of_base, True),
          violations(good, ["order-10"], mac_of, label_of_base, True))
    broken = {
        "AtMostOnce": (good, ["order-10", "order-10"]),
        "NoUnapprovedEffect": (good[:3] + [e(4, "denial", "a", {"mac": mac}), e(5, "tool_intent", base + "#2",
                                                                                 {"args": {"amount": 10}})], []),
        "OneYesOneAction": (good + [e(6, "effect_approved", "other", {"approval": 1})], ["order-10"]),
        "DoneIsTrue": (good, []),
    }
    for name, (entries, payments) in broken.items():
        found = violations(entries, payments, mac_of, label_of_base, True)
        check(f"T7 control: the checker sees {name}", any(v.startswith(name) for v in found), found)
    changed = [dict(good[0], payload={"mac": "x"})] + good[1:]
    check("T7 control: the checker sees a changed row (AppendOnly)", not prefix_ok(good, changed))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(*sys.argv[2:7])
    else:
        sys.exit(main())
