# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""python -m call4agents demo  - offline, no keys, standard library only.

A tiny stand-in for an agent framework: it runs steps and saves a checkpoint AFTER each step
(like LangGraph). We kill the process right after the email is sent, before the checkpoint,
then resume in a new process.
"""

import json
import shutil
import os
import subprocess
import sys
import tempfile
from pathlib import Path

STEPS = ["plan", "send_email", "reply"]


def framework_run(work: Path, use_ledger: bool, crash_at: str) -> None:
    """One process of the stand-in framework. Resumes from work/checkpoint.json if present."""
    import call4agents as c4a
    ckpt = work / "checkpoint.json"
    done = json.loads(ckpt.read_text())["done"] if ckpt.exists() else []
    outbox = work / "outbox.txt"
    c4a.init(str(work / ".c4a" / "ledger.sqlite"))

    def send_email(to: str, body: str) -> str:
        with open(outbox, "a") as f:
            f.write(f"to={to} body={body}\n")
        if crash_at == "during":
            os._exit(17)                          # crash inside the tool, after the email left
        return "sent"
    if use_ledger:
        send_email = c4a.effect(send_email)

    with c4a.run("thread-1"):
        for step in STEPS:
            if step in done:
                continue
            if step == "send_email":
                try:
                    send_email(to="bob", body="your invoice")
                except c4a.EffectInDoubt:
                    print("IN_DOUBT")             # refused to send again; a human or reconcile() decides
                if crash_at == "after":
                    os._exit(17)                  # crash after the tool returned, before the checkpoint
            done.append(step)
            ckpt.write_text(json.dumps({"done": done}))


def approval_run(work: Path, phase: str) -> None:
    import call4agents as c4a
    c4a.init(str(work / ".c4a" / "ledger.sqlite"))
    paid = work / "payments.txt"
    pending = work / "pending_call.json"

    @c4a.effect(approval=True)
    def pay(amount: int, to: str) -> str:
        with open(paid, "a") as f:
            f.write(f"{amount} -> {to}\n")
        return "paid"

    with c4a.run("thread-2"):
        if phase == "ask":
            call = {"amount": 10, "to": "alice"}
            pending.write_text(json.dumps(call))  # what the user is shown
            c4a.approve(pay, **call)              # the user says "yes" to exactly this
        else:
            call = json.loads(pending.read_text())
            try:
                pay(**call)
                print("PAID", call)
            except c4a.ApprovalRequired:
                print("REFUSED", call)


def child(args):
    work = Path(args[1])
    if args[0] == "framework":
        framework_run(work, args[2] == "1", args[3])
    else:
        approval_run(work, args[2])


def run_child(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "call4agents", "_child", *args], capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})


def count(path: Path) -> int:
    return len(path.read_text().splitlines()) if path.exists() else 0


def demo() -> dict:
    results = {}
    for label, use_ledger, crash_at in (("without the run record", "0", "after"),
                                        ("with the run record (crash after the tool)", "1", "after"),
                                        ("with the run record (crash inside the tool)", "1", "during")):
        work = Path(tempfile.mkdtemp(prefix="c4a-demo-"))
        run_child("framework", str(work), use_ledger, crash_at)       # dies after the email
        resumed = run_child("framework", str(work), use_ledger, "none")
        results[label] = {"emails": count(work / "outbox.txt"), "in_doubt": "IN_DOUBT" in resumed.stdout}
        shutil.rmtree(work, ignore_errors=True)
    work = Path(tempfile.mkdtemp(prefix="c4a-demo-"))
    run_child("approval", str(work), "ask")
    pending = work / "pending_call.json"
    pending.write_text(json.dumps({"amount": 9999, "to": "mallory"}))     # changed after the "yes"
    changed = run_child("approval", str(work), "pay").stdout.strip()
    pending.write_text(json.dumps({"amount": 10, "to": "alice"}))
    original = run_child("approval", str(work), "pay").stdout.strip()
    results["approval"] = {"changed_payment": changed, "approved_payment": original,
                           "payments": count(work / "payments.txt")}
    shutil.rmtree(work, ignore_errors=True)
    return results


def approval_ok(a: dict) -> bool:
    """The changed call refused, the approved one paid, one payment. Checking only the count passed
    with the approval binding removed."""
    return (a["changed_payment"].startswith("REFUSED") and a["approved_payment"].startswith("PAID")
            and a["payments"] == 1)


def doctor() -> str:
    """Versions for a bug report. Printed only: nothing is sent anywhere, no secret value is shown."""
    import platform
    import sqlite3
    from importlib import metadata

    def ver(dist: str) -> str:
        try:
            return metadata.version(dist)
        except metadata.PackageNotFoundError:
            return "not installed"
    import call4agents
    lines = [
        f"call4agents                  {ver('call4agents')} (module {call4agents.__version__})",
        f"python                       {platform.python_version()} ({platform.python_implementation()})",
        f"system                       {platform.system()} {platform.release()} {platform.machine()}",
        f"sqlite                       {sqlite3.sqlite_version}",
    ]
    for dist in ("langgraph", "langgraph-checkpoint-sqlite", "langchain-core", "pydantic-ai", "openai-agents",
                 "openai", "pydantic", "mcp"):
        lines.append(f"{dist:<28} {ver(dist)}")
    lines.append(f"C4A_SECRET                   {'set' if os.environ.get('C4A_SECRET') else 'not set'} (value never shown)")
    lines.append(f"C4A_OLD_SECRETS              {'set' if os.environ.get('C4A_OLD_SECRETS') else 'not set'}")
    return "\n".join(lines)


ISSUE_FORM = "https://github.com/salahmakboul/call4agents/issues/new"


def report_link() -> str:
    """A link to the issue form with the `doctor` field filled in. Printed only: nothing is sent,
    nothing is opened; you read it and decide."""
    from urllib.parse import urlencode
    return ISSUE_FORM + "?" + urlencode({"template": "feedback.yml", "doctor": doctor()})


def selftest() -> int:
    """The full offline checks from a repository checkout: kit, production, stress and idempotency suites."""
    root = Path(__file__).resolve().parents[1]
    if not (root / "tests" / "test_c4a_kit.py").exists() and (Path.cwd() / "tests" / "test_c4a_kit.py").exists():
        root = Path.cwd()                         # installed command, run from a checkout
    suites = ["tests.test_c4a_kit", "tests.test_c4a_production", "tests.stress_c4a_ledger", "tests.test_idempotency"]
    if not (root / "tests" / "test_c4a_kit.py").exists():
        print("selftest needs a repository checkout (the tests/ folder); running the demo checks only")
        r = demo()
        ok = [r[k]["emails"] for k in list(r)[:3]] == [2, 1, 1] and approval_ok(r["approval"])
        print("demo checks pass" if ok else "DEMO CHECKS FAILED")
        return 0 if ok else 1
    failed = []
    for s in suites:
        p = subprocess.run([sys.executable, "-m", s], cwd=root, capture_output=True, text=True)
        last = (p.stdout.strip().splitlines() or [""])[-1]
        print(f"  {'PASS' if p.returncode == 0 else 'FAIL'}  {s:28} {last}")
        if p.returncode:
            failed.append(s)
    print("selftest: all checks pass" if not failed else f"selftest: FAILED {failed}")
    return 1 if failed else 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "_child":
        child(sys.argv[2:])
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "incident":
        from call4agents.examples.approval_incident import main as incident
        incident()
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "crashtest":
        from call4agents.crashtest import main as crashtest
        return crashtest(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "scan":
        from call4agents.scan import main as scan
        return scan(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "quickstart":
        from call4agents.quickstart import main as quickstart
        return quickstart()
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        return selftest()
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        print(doctor())
        print("\nPaste this into your issue. call4agents sent nothing; it never sends anything.")
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        print("Tell us what happened (nothing is sent from here; this only prints a link).")
        print("The link opens the issue form with your `call4agents doctor` output filled in, so check it first:\n")
        print(doctor())
        print("\n" + report_link())
        return 0
    if len(sys.argv) > 1 and sys.argv[1] in ("status", "doubt", "resolve", "archive", "verify"):
        from call4agents.ops import main as ops_main
        return ops_main(sys.argv[1:])
    if len(sys.argv) > 2 and sys.argv[1] == "prove":
        from call4agents.proof import main as prove_main
        return prove_main(sys.argv[2])
    if len(sys.argv) > 3 and sys.argv[1] == "diff":
        from call4agents.replay import diff
        print(json.dumps(diff(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None), indent=1))
        return 0
    if len(sys.argv) < 2 or sys.argv[1] != "demo":
        print("usage: python -m call4agents quickstart | incident | demo | crashtest <script> | scan <path> | selftest | doctor | report | status|doubt|verify <record> | "
              "resolve <record> <key> done|not-done [note] | archive <record> <archive> <days> | "
              "prove <spec.json> | diff <a.sqlite> <b.sqlite> [run_id]")
        return 2
    r = demo()
    print("Crash right after the email is sent, then resume:")
    for label in list(r)[:3]:
        extra = " (refused to resend: in doubt)" if r[label]["in_doubt"] else ""
        print(f"  {label:45} emails sent: {r[label]['emails']}{extra}")
    a = r["approval"]
    print('"yes" to pay(10, alice), then the stored call is changed to pay(9999, mallory):')
    print(f"  changed call  -> {a['changed_payment']}")
    print(f"  approved call -> {a['approved_payment']}")
    print(f"  payments made: {a['payments']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
