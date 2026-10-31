"""call4agents audit. Run: python -m tests.test_audit

A1 the export says who approved what, when, for which arguments, and what ran; a no is there too.
A2 a clean export verifies, through the command and through the function.
A3 changing any single value in the file, at any place, is detected.
A4 removing, adding, repeating or reordering an entry is detected.
A5 another secret is refused, and a re-chained forgery without the secret is refused.
A6 a missing secret is an error, and no new secret file appears.
A7 --run exports one run only, and results and approval macs are left out."""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

import call4agents as c4a
from call4agents.audit import _link, build, find_secret, main, verify
from call4agents.ledger import LedgerError

FAILED = []
SECRET = b"audit-secret-0123"


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {str(detail)[:300]}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def refused(doc, secret=SECRET):
    try:
        verify(doc, secret)
        return False
    except (LedgerError, AttributeError, TypeError, KeyError):
        return True


def places(value, path=()):
    """Every leaf in the document, as a path."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from places(v, path + (k,))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from places(v, path + (i,))
    else:
        yield path


def changed(doc, path):
    out = copy.deepcopy(doc)
    node = out
    for p in path[:-1]:
        node = node[p]
    old = node[path[-1]]
    node[path[-1]] = old + 1 if isinstance(old, (int, float)) and not isinstance(old, bool) else f"{old}x"
    return out


def main_test():
    folder = tempfile.mkdtemp()
    record = str(Path(folder) / "r.sqlite")
    lg = c4a.init(record, secret=SECRET)

    @c4a.effect(approval=True, name="m.pay")
    def pay(amount):
        return f"secret result {amount}"

    with c4a.run("r1"):
        c4a.approve(pay, amount=10)
        pay(10)
        ticket = c4a.pause(pay, amount=20)
        c4a.decide(ticket, "approve", by="sam")
        pay(20)
        c4a.deny(pay, amount=30)
    with c4a.run("r2"):
        c4a.approve(pay, amount=5)

    doc = build(record, SECRET)
    rows = {a["args"]["amount"]: a for a in doc["actions"]}
    check("A1 what ran, with exact arguments", sorted(rows) == [10, 20] and rows[10]["tool"] == "m.pay" and rows[10]["state"] == "done", rows)
    check("A1 who approved, and when", rows[20]["approved_by"] == "sam" and rows[20]["approved_at"] and rows[10]["approved_at"], rows)
    check("A1 no name was given, so none is shown", rows[10]["approved_by"] is None, rows)
    check("A1 a no is listed", [d["args"] for d in doc["decisions"] if d["decision"] == "no"] == [{"amount": 30}], doc["decisions"])

    check("A2 a clean export verifies", not refused(doc))
    out = str(Path(folder) / "out.json")
    os.environ["C4A_SECRET"] = SECRET.decode()
    check("A2 the commands work", main(["audit", record, out]) == 0 and main(["audit-verify", out]) == 0)

    leaves = list(places(doc))
    missed = [p for p in leaves if not refused(changed(doc, p))]
    check(f"A3 every single value changed is detected ({len(leaves)} places)", not missed and len(leaves) > 100, missed[:5])

    for name, fix in (("removed", lambda d: d["entries"].pop(3)), ("last removed", lambda d: d["entries"].pop()),
                      ("repeated", lambda d: d["entries"].append(copy.deepcopy(d["entries"][0]))),
                      ("reordered", lambda d: d["entries"].reverse())):
        bad = copy.deepcopy(doc)
        fix(bad)
        check(f"A4 an entry {name} is detected", refused(bad))
        bad["count"] = len(bad["entries"])
        check(f"A4 an entry {name}, with the count fixed, is detected", refused(bad))

    check("A5 another secret is refused", refused(doc, b"another-secret-012"))
    forged = copy.deepcopy(doc)
    forged["actions"][0]["args"] = {"amount": 9999}
    forged["entries"][3]["payload"]["args"] = {"amount": 9999}
    head = "c4a-audit-1"
    for e in forged["entries"]:
        head = _link(head, {k: v for k, v in e.items() if k != "hash"})
        e["hash"] = head
    forged["head"] = head
    check("A5 a re-chained forgery without the secret is refused", refused(forged))

    os.environ.pop("C4A_SECRET")
    bare = tempfile.mkdtemp()
    try:
        find_secret(str(Path(bare) / "r.sqlite"))
        check("A6 a missing secret is an error", False)
    except LedgerError:
        check("A6 a missing secret is an error", True)
    check("A6 no secret file appears", not list(Path(bare).iterdir()))
    check("A6 the command says so", main(["audit-verify", out]) == 1)

    one = build(record, SECRET, run="r2")
    check("A7 --run exports one run", {e["run_id"] for e in one["entries"]} == {"r2"} and not refused(one), one["count"])
    text = json.dumps(doc)
    check("A7 results and approval macs are left out", "secret result" not in text and '"mac"' not in text)
    try:
        build(record, SECRET, run="nope")
        check("A7 an unknown run is an error", False)
    except LedgerError:
        check("A7 an unknown run is an error", True)
    lg.close() if hasattr(lg, "close") else None

    if FAILED:
        print(f"{len(FAILED)} audit check(s) failed")
        return 1
    print("all audit checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main_test())
