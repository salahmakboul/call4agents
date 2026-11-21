"""2026-11-19 guarded rerun: the one place the scripts in scripts_guarded/ touch call4agents.

call4agents comes from PyPI (pip install call4agents==0.7.3 in each c4a-rerun-1119-<fw>-venv); nothing is put on
sys.path. The guard is c4a.effect(pay, name="pay", approval=True), a record in <work>/c4a.sqlite (outside the
framework's saved state), and c4a.approve("pay", **args) when the person says yes. No reconcile=, no
idempotent_for=: a crash between the guard's intent and its result leaves the call in doubt.

Every guarded tool call appends one line to <work>/guard_log.jsonl saying how it ended:
  ran              the inner pay ran in this call (the bank file got a line)
  recorded_result  the guard returned the result it had recorded, the inner pay did not run
  EffectInDoubt / ApprovalRequired   the guard refused, the inner pay did not run
"""
import json
import os
import sqlite3
from pathlib import Path

SECRET = b"finding-secret-0123"
_RAN = []


def _log(work: Path, rec: dict) -> None:
    rec = dict(rec, pid=os.getpid(), phase=os.environ.get("C4A_PHASE", "?"))
    with open(Path(work) / "guard_log.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")


def setup(work: Path):
    import call4agents as c4a
    c4a.init(str(Path(work) / "c4a.sqlite"), secret=SECRET)
    info = Path(work) / "c4a_info.json"
    if not info.exists():
        info.write_text(json.dumps({"version": c4a.__version__, "file": c4a.__file__}))
    return c4a


def mark_ran() -> None:
    """Called by the inner pay right after it wrote to the bank file (before any crash)."""
    _RAN.append(1)


def wrap_sync(work: Path, pay_fn, run_id: str):
    c4a = setup(work)
    guarded = c4a.effect(pay_fn, name="pay", approval=True)

    def call(**args):
        with c4a.run(run_id):
            _RAN.clear()
            try:
                r = guarded(**args)
            except (c4a.ApprovalRequired, c4a.EffectInDoubt) as exc:
                _log(work, {"outcome": type(exc).__name__, "msg": str(exc)[:200]})
                return None, f"refused: {type(exc).__name__}"
            _log(work, {"outcome": "ran" if _RAN else "recorded_result", "result": repr(r)[:100]})
            return r, None
    return call, c4a


def wrap_async(work: Path, pay_fn, run_id: str):
    c4a = setup(work)
    guarded = c4a.effect(pay_fn, name="pay", approval=True)

    async def call(*a, **args):
        with c4a.run(run_id):
            _RAN.clear()
            try:
                r = await guarded(*a, **args)
            except (c4a.ApprovalRequired, c4a.EffectInDoubt) as exc:
                _log(work, {"outcome": type(exc).__name__, "msg": str(exc)[:200]})
                return None, f"refused: {type(exc).__name__}"
            _log(work, {"outcome": "ran" if _RAN else "recorded_result", "result": repr(r)[:100]})
            return r, None
    return call, c4a


def approve(work: Path, run_id: str, **args) -> None:
    c4a = setup(work)
    with c4a.run(run_id):
        c4a.approve("pay", **args)
    _log(work, {"outcome": "approve_recorded", "args": args})


def report(work: Path) -> dict:
    """What the guard saw, read from the files after the run: the outcome lines and the record's entry kinds."""
    work = Path(work)
    log = [json.loads(l) for l in (work / "guard_log.jsonl").read_text().splitlines()] \
        if (work / "guard_log.jsonl").exists() else []
    kinds = []
    if (work / "c4a.sqlite").exists():
        con = sqlite3.connect(work / "c4a.sqlite")
        kinds = [r[0] for r in con.execute("SELECT kind FROM entries ORDER BY seq")]
        con.close()
    info = json.loads((work / "c4a_info.json").read_text()) if (work / "c4a_info.json").exists() else None
    calls = [r["outcome"] for r in log if r["outcome"] != "approve_recorded"]
    return {"call4agents": info, "guard_log": log, "guard_calls": calls, "record_kinds": kinds}
