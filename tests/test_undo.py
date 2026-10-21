"""The undo path (offline, 0 LLM calls). Run: python -m tests.test_undo

U1 three finished actions are undone newest first, each undo gets the original arguments and result.
U2 calling undo again reverses nothing twice.
U3 an undo that raises stops the walk: newer undos stay done, older actions are untouched, the failed
   undo is in doubt; after a person resolves it, undo continues with the older ones.
U4 an action without undo= is listed, never silently skipped.
U5 an async undo is refused when decorated; an undo cannot have its own undo.
U6 a crash in the middle of undo: a NEW process continues and each action is reversed exactly once."""
import subprocess
import sys
import tempfile
from pathlib import Path

import call4agents as c4a
from call4agents import ops

ROOT = Path(__file__).resolve().parents[1]
FAILED = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def fresh():
    return c4a.init(str(Path(tempfile.mkdtemp(prefix="c4a-undo-")) / "l.sqlite"), secret=b"u" * 16)


def main():
    log = []

    def refund(amount, to, result):
        log.append(("refund", amount, to, result))

    def cancel(day):
        log.append(("cancel", day))

    @c4a.effect(name="u.charge", undo=refund)
    def charge(amount, to):
        log.append(("charge", amount, to))
        return f"ch_{amount}"

    @c4a.effect(name="u.book", undo=cancel)
    def book(day):
        log.append(("book", day))
        return "booked"

    # U1
    fresh()
    with c4a.run("trip"):
        charge(10, "alice")
        book("mon")
        charge(20, "bob")
        log.clear()
        out = c4a.undo()
    check("U1 undone newest first, with the original arguments and result",
          log == [("refund", 20, "bob", "ch_20"), ("cancel", "mon"), ("refund", 10, "alice", "ch_10")]
          and out["undone"] == ["u.charge", "u.book", "u.charge"], (log, out))

    # U2
    log.clear()
    with c4a.run("trip"):
        again = c4a.undo()
    check("U2 a second undo reverses nothing twice", log == [] and again["undone"] == ["u.charge", "u.book", "u.charge"], (log, again))

    # U3
    lg = fresh()
    boom = {"on": True}

    def flaky_cancel(day):
        if boom["on"]:
            raise TimeoutError("hotel API timeout")
        log.append(("cancel", day))

    @c4a.effect(name="u.book2", undo=flaky_cancel)
    def book2(day):
        return "booked"
    with c4a.run("trip3"):
        charge(10, "alice")
        book2("tue")
        charge(30, "carol")
        log.clear()
        try:
            c4a.undo()
            failed = None
        except c4a.UndoFailed as exc:
            failed = exc
        stopped = failed is not None and failed.tool == "u.book2" and log == [("refund", 30, "carol", "ch_30")]
        doubt = [a for a in ops.in_doubt(lg) if a["tool"] == "u.book2.undo"]
        boom["on"] = False
        try:
            c4a.undo()
            blocked = False
        except c4a.UndoFailed:
            blocked = True                    # still in doubt: nobody has checked the hotel yet
        ops.resolve(lg, doubt[0]["key"][:12] if doubt else "x", "not-done", "hotel shows the booking still active")
        log.clear()
        c4a.undo()
    check("U3 a failed undo stops the walk, is in doubt, blocks until resolved, then undo continues",
          stopped and len(doubt) == 1 and blocked and log == [("cancel", "tue"), ("refund", 10, "alice", "ch_10")],
          (stopped, len(doubt), blocked, log))

    # U4
    fresh()

    @c4a.effect(name="u.note")
    def note(text):
        return "noted"
    with c4a.run("trip4"):
        charge(5, "dan")
        note("x")
        out4 = c4a.undo()
    check("U4 an action without undo= is listed in no_undo, not silently skipped",
          out4 == {"undone": ["u.charge"], "no_undo": ["u.note"]}, out4)

    # U5
    async def aundo(x):
        pass
    try:
        c4a.effect(lambda x: x, name="u.a", undo=aundo)
        async_refused = False
    except TypeError:
        async_refused = True
    try:
        c4a.effect(lambda x: x, name="u.b.undo", undo=lambda x: None)
        nested_refused = False
    except TypeError:
        nested_refused = True
    check("U5 an async undo and an undo of an undo are refused when decorated", async_refused and nested_refused)

    # U6 crash in the middle of undo, continue in a new process
    work = Path(tempfile.mkdtemp(prefix="c4a-undo6-"))
    child = [sys.executable, "-m", "tests.c4a_undo_child", str(work)]
    subprocess.run(child + ["do"], cwd=ROOT, check=True, capture_output=True)
    crashed = subprocess.run(child + ["undo-crash"], cwd=ROOT, capture_output=True)
    subprocess.run(child + ["undo"], cwd=ROOT, check=True, capture_output=True)
    after_restart = (work / "refunds.txt").read_text().split()
    lg6 = c4a.init(str(work / "ledger.sqlite"), secret=b"u" * 16)
    doubt6 = [a for a in ops.in_doubt(lg6) if a["tool"] == "u6.charge.undo"]
    if doubt6:                                # a person checks the payment provider: refund 2 exists
        ops.resolve(lg6, doubt6[0]["key"][:12], "done", "refund for charge 2 is in the provider dashboard")
    subprocess.run(child + ["undo"], cwd=ROOT, check=True, capture_output=True)
    lines = (work / "refunds.txt").read_text().split()
    check("U6 crash in the middle of undo: the refund that crashed is in doubt (not repeated); after a person "
          "confirms it, a new process finishes; each refund happened exactly once",
          crashed.returncode == 17 and after_restart == ["3", "2"] and len(doubt6) == 1 and lines == ["3", "2", "1"],
          (crashed.returncode, after_restart, len(doubt6), lines))

    print(f"\n{len(FAILED)} undo check(s) failed" if FAILED else "\nall undo checks pass")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
