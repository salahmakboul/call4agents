"""Microsoft Agent Framework (Python): tool approval + workflow checkpoints + a crash right after the approved tool.

Fake chat client (no keys, no network), built like MockBaseChatClient in their tests (FunctionInvocationLayer +
BaseChatClient). One tool, pay(amount, to), with approval_mode="always_require"; its side effect is one line
appended to payments.txt. The agent is the start executor of a workflow with FileCheckpointStorage. Every phase
is a separate OS process:

  1. start    run("Pay alice 10."); the workflow pauses with a function_approval_request (request_info) and has
              written checkpoints
  2. approve  a new process restores the latest checkpoint the documented way (run(checkpoint_id=...)), reads the
              pending request, answers it with to_function_approval_response(approved=True); the tool pays, then
              the process dies with os._exit right after the payment, before the workflow stores anything
  3. resume   a new process restores the latest checkpoint again (run(checkpoint_id=...)). If the workflow asks for
              approval again, the script says yes again (a person may not know the first yes paid) and records it.

Case "crash, resume from the checkpoint before the yes": as above, but 3 restores the checkpoint written before the
yes was delivered (the one that still holds the pending request).
Case "edited before the yes": between 1 and 2 the pending call in the checkpoint files is changed to pay(99, "mallo")
(same-length byte edit inside the stored state; needs write access to the checkpoint directory). No crash. The
person is shown the edited call, so this is not a bypass; it is a control for the next case.
Case "crash, then edited after the yes": 2 with the crash, then the same edit on every checkpoint, then 3.

    python maf_approve_crash.py            # 5 runs per case, prints JSON
    GUARD=1 python maf_approve_crash.py    # same, with a tool that checks for an earlier identical payment first
"""
import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROMPT = "Pay alice 10."


def build(work: Path):
    from agent_framework import (Agent, BaseChatClient, ChatResponse, Content, FileCheckpointStorage,
                                 FunctionInvocationLayer, Message, WorkflowBuilder, tool)

    class FakeClient(FunctionInvocationLayer, BaseChatClient):
        def _inner_get_response(self, *, messages, stream, options, **kwargs):
            async def _get():
                paid = any(c.type == "function_result" for m in messages for c in (m.contents or []))
                if paid:
                    return ChatResponse(messages=Message(role="assistant", contents=["Paid."]))
                call = Content.from_function_call(call_id="call_1", name="pay",
                                                  arguments=json.dumps({"amount": 10, "to": "alice"}))
                return ChatResponse(messages=Message(role="assistant", contents=[call]))
            if stream:
                raise NotImplementedError("the test uses non-streaming runs")
            return _get()

    @tool(approval_mode="always_require")
    def pay(amount: int, to: str) -> str:
        """Pay an amount to a person."""
        line = f"{amount} {to}"
        if os.environ.get("GUARD") == "1" and (work / "payments.txt").exists()                 and line in (work / "payments.txt").read_text().splitlines():
            return f"paid {amount} to {to}"   # GUARD=1: ask the "provider" first; same arguments = already paid
        with open(work / "payments.txt", "a") as f:
            f.write(f"{amount} {to}\n")
            f.flush()
            os.fsync(f.fileno())
        if os.environ.get("KILL_AFTER_PAY") == "1":
            os._exit(17)                       # crash after the side effect, before the workflow stores anything
        return f"paid {amount} to {to}"

    agent = Agent(client=FakeClient(), name="payer", instructions="Pay when asked.", tools=[pay])
    storage = FileCheckpointStorage(work / "ckpt")
    wf = WorkflowBuilder(start_executor=agent, checkpoint_storage=storage, name="payflow").build()
    return wf, storage


def approvals(result):
    out = []
    for ev in result.get_request_info_events():
        d = ev.data
        if getattr(d, "type", None) == "function_approval_request":
            out.append((ev.request_id, d))
    return out


async def latest(storage):
    """The newest checkpoint, or with BACK=k the k-th one before it (BACK=1: the one written before the yes)."""
    cps = await storage.list_checkpoints(workflow_name="payflow")
    cps.sort(key=lambda c: c.timestamp)
    return cps[-1 - int(os.environ.get("BACK", "0"))].checkpoint_id, len(cps)


async def phase(work: Path, name: str) -> dict:
    wf, storage = build(work)
    if name == "start":
        r = await wf.run(PROMPT)
        return {"asked": len(approvals(r)), "checkpoints": (await latest(storage))[1]}
    cid, n = await latest(storage)
    r = await wf.run(checkpoint_id=cid)
    pending = approvals(r)
    out = {"checkpoints_before": n, "asked": len(pending),
           "pending_args": [p[1].function_call.parse_arguments() for p in pending]}
    if pending:                                # approve phase: the first yes; resume phase: a yes given again
        r2 = await wf.run(responses={rid: d.to_function_approval_response(approved=True) for rid, d in pending})
        out["asked_again_after_yes"] = len(approvals(r2))
        out["outputs"] = [str(o)[:80] for o in r2.get_outputs()]
    return out


def edit_checkpoints(work: Path) -> int:
    """Change pay(10, alice) to pay(99, mallo) in every stored checkpoint (same-length byte edits)."""
    n = 0
    for f in (work / "ckpt").glob("*.json"):
        raw = f.read_text(encoding="utf-8")
        data = json.loads(raw)

        def walk(x):
            nonlocal n
            if isinstance(x, dict):
                return {k: walk(v) for k, v in x.items()}
            if isinstance(x, list):
                return [walk(v) for v in x]
            if isinstance(x, str):
                if '"alice"' in x and '"amount"' in x:
                    n += 1
                    return x.replace('"amount": 10', '"amount": 99').replace('"alice"', '"mallo"')
                if len(x) > 40:
                    try:
                        b = base64.b64decode(x, validate=True)
                    except Exception:  # noqa: BLE001
                        return x
                    if b'alice' in b and b'amount' in b:
                        b2 = b.replace(b'"amount": 10', b'"amount": 99').replace(b'"alice"', b'"mallo"')
                        if b2 != b:
                            n += 1
                            return base64.b64encode(b2).decode()
            return x
        new = walk(data)
        if new != data:
            f.write_text(json.dumps(new), encoding="utf-8")
    return n


def run(work, name, kill=False, back=0):
    env = dict(os.environ, KILL_AFTER_PAY="1" if kill else "0", BACK=str(back))
    p = subprocess.run([sys.executable, __file__, "--phase", str(work), name], capture_output=True, text=True,
                       env=env, timeout=180)
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        out = {"error": (p.stderr or p.stdout)[-600:]}
    out["exit"] = p.returncode
    return out


def payments(work):
    f = Path(work) / "payments.txt"
    return f.read_text().splitlines() if f.exists() else []


def one(case):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        w = Path(d)
        r = {"start": run(w, "start")}
        if case == "control: no crash":
            r["approve"] = run(w, "approve")
        elif case == "crash, resume from the checkpoint before the yes":
            r["approve"] = run(w, "approve", kill=True)
            r["resume"] = run(w, "resume", back=1)
        elif case == "edited before the yes":
            r["fields_edited"] = edit_checkpoints(w)
            r["approve"] = run(w, "approve")
        elif case == "crash, then edited after the yes":
            r["approve"] = run(w, "approve", kill=True)
            r["fields_edited"] = edit_checkpoints(w)
            r["resume"] = run(w, "resume")
        else:
            r["approve"] = run(w, "approve", kill=True)
            r["resume"] = run(w, "resume")
        r["payments"] = payments(w)
        return r


if __name__ == "__main__":
    if sys.argv[1:2] == ["--phase"]:
        print(json.dumps(asyncio.run(phase(Path(sys.argv[2]), sys.argv[3]))))
        sys.exit(0)
    n = int(os.environ.get("N", "5"))
    import agent_framework
    res = {"agent_framework": agent_framework.__version__, "python": sys.version.split()[0], "runs_per_case": n,
           "cases": {}}
    for case in ("control: no crash", "crash after approved payment", "crash, resume from the checkpoint before the yes",
                 "edited before the yes",
                 "crash, then edited after the yes"):
        runs = [one(case) for _ in range(n)]
        res["cases"][case] = {"payments": [r["payments"] for r in runs], "runs": runs}
    print(json.dumps(res, indent=1))
