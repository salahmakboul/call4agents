# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Three approval incidents, each played without and with Call4Agents.

    call4agents incident

Offline, no API keys, a few seconds. The agent loop here is a small plain-Python stand-in for what a
framework does: it saves its state to a file while it waits for a person, and resumes from that file. The
processes and the crashes are real (each step is a new Python process, a crash is os._exit). The bank is a
local file. `call4agents quickstart` shows the crash in the real frameworks.

1. The double payment: a person approves one payment, the process dies right after the bank charged, and the
   resume runs the approved call again.
2. The changed call: a person says yes to 10 for alice; the saved call is changed to 9999 for mallory before it runs.
3. The ignored no: a person says no, and a bug in the agent loop runs the call anyway.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _step(work: Path, kit: bool, phase: str, crash: str = "") -> None:
    """One process of the agent: `approve` or `deny` (a person decides), or `run` (the agent acts)."""
    import call4agents as c4a
    bank, state = work / "bank.txt", work / "state.json"

    def pay(amount: int, to: str) -> str:
        with open(bank, "a") as f:
            f.write(f"{amount} {to}\n")
        if crash == "during":
            os._exit(17)                           # the bank charged, and the process died inside the call
        return f"charged {amount} to {to}"

    if kit:
        c4a.init(str(work / "record.sqlite"), secret=b"incident-demo-only")
        pay = c4a.effect(pay, name="bank.pay", approval=True)
    call = json.loads(state.read_text())
    with c4a.run("order-42"):
        if kit and phase == "approve":
            c4a.approve(pay, **call)               # bound to the exact call the person saw
        elif kit and phase == "deny":
            c4a.deny(pay, **call)
        elif phase == "run":
            try:
                pay(**call)
            except (c4a.ApprovalRequired, c4a.EffectInDoubt) as exc:
                (work / "stopped.txt").write_text(type(exc).__name__)
            if crash == "after":
                os._exit(17)                       # the call returned, and the process died before saving progress


def _play(incident: str, kit: bool) -> dict:
    work = Path(tempfile.mkdtemp(prefix="c4a-incident-"))
    (work / "state.json").write_text(json.dumps({"amount": 10, "to": "alice"}))
    src = str(Path(__file__).resolve().parents[2])

    def step(phase: str, crash: str = "") -> None:
        env = {**os.environ, "PYTHONPATH": src + os.pathsep + os.environ.get("PYTHONPATH", "")}
        subprocess.run([sys.executable, "-m", "call4agents.examples.approval_incident", "--step", str(work),
                        "1" if kit else "0", phase, crash], env=env, check=False)

    if incident == "ignored_no":
        step("deny")
        step("run")                                # the bug: the agent loop runs it anyway
    else:
        step("approve")
        if incident == "changed_call":
            (work / "state.json").write_text(json.dumps({"amount": 9999, "to": "mallory"}))
            step("run")
        else:
            step("run", "after" if incident == "double_payment" else "during")
            step("run")                            # a new process resumes from the saved state
    bank = work / "bank.txt"
    charges = bank.read_text().split("\n")[:-1] if bank.exists() else []
    stopped = work / "stopped.txt"
    return {"charges": charges, "stopped": stopped.read_text() if stopped.exists() else None}


def run_all() -> dict:
    return {name: {"without": _play(name, False), "with": _play(name, True)}
            for name in ("double_payment", "crash_inside", "changed_call", "ignored_no")}


def _line(label: str, r: dict) -> str:
    money = ", ".join(c.replace(" ", " to ") for c in r["charges"]) or "nothing"
    why = {"ApprovalRequired": " (refused: no yes for this exact call)",
           "EffectInDoubt": " (stopped: in doubt, a person must check)"}.get(r["stopped"], "")
    return f"  {label:22} charges: {len(r['charges'])}  paid: {money}{why}"


def main() -> dict:
    r = run_all()
    print("Incident 1. The double payment")
    print('  A person approves pay(10, alice). The bank charges. The process dies before saving. It resumes.')
    print(_line("without Call4Agents", r["double_payment"]["without"]))
    print(_line("with Call4Agents", r["double_payment"]["with"]))
    print("  The same, but the process dies inside the call, so nobody knows if it finished:")
    print(_line("without Call4Agents", r["crash_inside"]["without"]))
    print(_line("with Call4Agents", r["crash_inside"]["with"]))
    print()
    print("Incident 2. The changed call")
    print("  A person approves pay(10, alice). The saved call is changed to pay(9999, mallory) before it runs.")
    print(_line("without Call4Agents", r["changed_call"]["without"]))
    print(_line("with Call4Agents", r["changed_call"]["with"]))
    print()
    print("Incident 3. The ignored no")
    print("  A person says no to pay(10, alice). A bug in the agent loop runs the call anyway.")
    print(_line("without Call4Agents", r["ignored_no"]["without"]))
    print(_line("with Call4Agents", r["ignored_no"]["with"]))
    print()
    print("The processes and crashes are real; the bank and the agent loop are small local stand-ins.")
    return r


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--step":
        _step(Path(sys.argv[2]), sys.argv[3] == "1", sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else "")
    else:
        main()
