"""Finding (kept private until the maintainers have been told): what happens when an agent crashes
right after an APPROVED payment ran, and the app resumes the way the framework documents.

    python approve_then_crash.py            # runs every case in fresh processes, prints a JSON table

Per framework (LangGraph, Pydantic AI, OpenAI Agents SDK), two cases, each WITHOUT and WITH call4agents:
  approve_then_crash : a person approves pay(10, alice); the tool pays; the process dies before the
                       framework saves its progress; a NEW process resumes from the last saved state.
                       Counted: payments made. Once-only = 1.
  edited_after_yes   : a person approves pay(10, alice); the saved state is edited to pay(9999, mallory)
                       before the run resumes (what anyone with write access to the store can do).
                       Recorded: what was actually paid.
Fake models, no network, no keys. Each run uses a new temp folder."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import c4a_guard

HERE = Path(__file__).resolve().parent
KIT = os.environ.get("C4A_KIT_ROOT", str(HERE.parent / "call4agents-release"))


# ------------------------------------------------------------------ shared tool
def make_pay(work: Path, use_kit: bool, run_id: str, crash: bool, adapter: str = ""):
    # 2026-11-19 rerun: the guard is imported only when used (this rerun never uses it); fsync added

    def pay(amount: int, to: str) -> str:
        with open(work / "payments.txt", "a") as f:
            f.write(json.dumps({"amount": amount, "to": to}) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if use_kit:
            c4a_guard.mark_ran()
        if crash:
            os._exit(17)                       # paid; the framework has not saved its progress
        return "paid"
    if not use_kit:
        return pay, None
    # 2026-11-19 guarded rerun: call4agents from the venv (PyPI 0.7.3), not from a source checkout
    call, c4a = c4a_guard.wrap_sync(work, pay, run_id)

    def guarded_pay(**args):
        r, refused = call(**args)
        return refused if refused else r
    return guarded_pay, c4a


# ------------------------------------------------------------------ LangGraph
def langgraph(work: Path, case: str, use_kit: bool, phase: str):
    from typing import TypedDict

    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
    import sqlite3

    crash = case == "approve_then_crash" and phase == "approve"
    pay, c4a = make_pay(work, use_kit, "thread-1", crash)

    class S(TypedDict, total=False):
        call: dict
        approved: bool

    def propose(state):
        return {"call": {"amount": 10, "to": "alice"}}

    def approve(state):
        return {"approved": interrupt({"call": state["call"]}) == "yes"}

    def tools(state):
        if state.get("approved"):
            if c4a:
                r = pay(**state["call"])
                if isinstance(r, str) and r.startswith("refused"):
                    return {"refused": r}
            else:
                pay(**state["call"])
        return {}

    g = StateGraph(S)
    for n, f in (("propose", propose), ("approve", approve), ("tools", tools)):
        g.add_node(n, f)
    g.add_edge(START, "propose"); g.add_edge("propose", "approve"); g.add_edge("approve", "tools"); g.add_edge("tools", END)
    graph = g.compile(checkpointer=SqliteSaver(sqlite3.connect(work / "lg.sqlite", check_same_thread=False)))
    cfg = {"configurable": {"thread_id": "thread-1"}}
    if phase == "start":
        graph.invoke({}, cfg)                                      # stops at the approval interrupt
        return
    if phase == "approve":
        if case == "edited_after_yes":
            graph.update_state(cfg, {"call": {"amount": 9999, "to": "mallory"}})
        if c4a:
            c4a_guard.approve(work, "thread-1", amount=10, to="alice")   # the person said yes to what they SAW
        graph.invoke(Command(resume="yes"), cfg)
        return
    if phase == "resume":
        out = graph.invoke(None, cfg)                              # the documented resume
        if "__interrupt__" in (out or {}):
            (work / "asked_again.txt").write_text("yes")           # LangGraph asks for the approval again
        return
    if c4a and (work / "asked_again.txt").exists():
        c4a_guard.approve(work, "thread-1", amount=10, to="alice")   # guarded rerun: the app records the second yes too
    graph.invoke(Command(resume="yes"), cfg)                       # phase "reapprove": the person says yes again


# ------------------------------------------------------------------ Pydantic AI
def pydantic_ai(work: Path, case: str, use_kit: bool, phase: str):
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
    from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    crash = case == "approve_then_crash" and phase == "approve"
    pay_fn, c4a = make_pay(work, use_kit, "conv-1", crash)

    def model(messages, info):
        if any(getattr(p, "part_kind", "") == "tool-return" for p in messages[-1].parts):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart("pay", {"amount": 10, "to": "alice"}, tool_call_id="c1")])

    agent = Agent(FunctionModel(model), output_type=[str, DeferredToolRequests])

    @agent.tool_plain(requires_approval=True)
    def pay(amount: int, to: str) -> str:
        return pay_fn(amount=amount, to=to)

    saved = work / "history.json"
    if phase == "start":
        r = agent.run_sync("pay alice 10")
        saved.write_bytes(ModelMessagesTypeAdapter.dump_json(r.all_messages()))   # stored while waiting
        return
    history = ModelMessagesTypeAdapter.validate_json(saved.read_bytes())
    if phase == "approve":
        if case == "edited_after_yes":
            for part in history[-1].parts:
                if isinstance(part, ToolCallPart):
                    part.args = {"amount": 9999, "to": "mallory"}
            saved.write_bytes(ModelMessagesTypeAdapter.dump_json(history))
        if c4a:
            c4a_guard.approve(work, "conv-1", amount=10, to="alice")
    # "approve" and "resume" both run with the stored history + the stored approval (how an app resumes)
    agent.run_sync(message_history=history, deferred_tool_results=DeferredToolResults(approvals={"c1": True}))


# ------------------------------------------------------------------ OpenAI Agents SDK
def openai_agents(work: Path, case: str, use_kit: bool, phase: str):
    import asyncio
    os.environ.setdefault("OPENAI_API_KEY", "not-used-fake-model")
    from agents import Agent, ModelResponse, RunState, Runner, function_tool, set_tracing_disabled
    from agents.models.interface import Model
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
    set_tracing_disabled(True)

    crash = case == "approve_then_crash" and phase == "approve"
    pay_fn, c4a = make_pay(work, use_kit, "session-1", crash)

    class FakeModel(Model):
        async def get_response(self, *a, **k):
            items = k.get("input", a[1] if len(a) > 1 else [])
            items = items if isinstance(items, list) else []
            done = any((i.get("type") if isinstance(i, dict) else getattr(i, "type", "")) == "function_call_output" for i in items)
            if done:
                out = [ResponseOutputMessage(id="m1", type="message", role="assistant", status="completed",
                                             content=[ResponseOutputText(type="output_text", text="done", annotations=[])])]
            else:
                out = [ResponseFunctionToolCall(id="fc1", call_id="c1", type="function_call", name="pay",
                                                arguments=json.dumps({"amount": 10, "to": "alice"}), status="completed")]
            return ModelResponse(output=out, usage=Usage(), response_id=None)

        def stream_response(self, *a, **k):
            raise NotImplementedError

    @function_tool(needs_approval=True)
    def pay(amount: int, to: str) -> str:
        """Pay someone."""
        return pay_fn(amount=amount, to=to)

    agent = Agent(name="payer", instructions="pay", tools=[pay], model=FakeModel())
    saved = work / "state.json"

    async def go():
        if phase == "start":
            r = await Runner.run(agent, "pay alice 10")
            saved.write_text(json.dumps(r.to_state().to_json()))
            return
        blob = json.loads(saved.read_text())
        if phase == "approve" and case == "edited_after_yes":
            text = json.dumps(blob).replace('{\\"amount\\": 10, \\"to\\": \\"alice\\"}', '{\\"amount\\": 9999, \\"to\\": \\"mallory\\"}')
            blob = json.loads(text)
        state = RunState.from_json(agent, blob)
        state = await state if asyncio.iscoroutine(state) else state
        for item in state.get_interruptions():
            state.approve(item)
        if phase == "approve":
            saved.write_text(json.dumps(state.to_json()))       # the app stores the approved state first
            if c4a:
                c4a_guard.approve(work, "session-1", amount=10, to="alice")
        await Runner.run(agent, state)
    asyncio.run(go())


FRAMEWORKS = {"LangGraph": langgraph, "Pydantic AI": pydantic_ai, "OpenAI Agents SDK": openai_agents}


def run_case(fw: str, case: str, use_kit: bool) -> dict:
    work = Path(tempfile.mkdtemp(prefix="c4a-finding-"))
    phases = ["start", "approve"] + (["resume"] if case == "approve_then_crash" else [])
    if case == "approve_then_crash" and fw == "LangGraph":
        phases.append("reapprove")                                 # the person, asked again, says yes again
    codes = []
    for phase in phases:
        p = subprocess.run([sys.executable, __file__, "_child", fw, str(work), case, "1" if use_kit else "0", phase],
                           capture_output=True, text=True, timeout=300, env=dict(os.environ, C4A_PHASE=phase))
        codes.append(p.returncode)
        if p.returncode not in (0, 17):
            raised = (p.stderr.strip().splitlines() or ['?'])[-1][:200]
            pays = (work / "payments.txt").read_text().splitlines() if (work / "payments.txt").exists() else []
            return {"payments": len(pays), "paid": [json.loads(l) for l in pays], "exit_codes": codes + [p.returncode],
                    "raised_in_" + phase: raised, **(c4a_guard.report(work) if use_kit else {})}
    pays = [json.loads(l) for l in (work / "payments.txt").read_text().splitlines()] if (work / "payments.txt").exists() else []
    return {"payments": len(pays), "paid": pays, "exit_codes": codes,
            "asked_again": (work / "asked_again.txt").exists(), **(c4a_guard.report(work) if use_kit else {})}


def main():
    from importlib import metadata
    versions = {d: metadata.version(d) for d in ("langgraph", "pydantic-ai", "openai-agents")}
    results = {"versions": versions, "python": sys.version.split()[0], "cases": {}}
    for fw in FRAMEWORKS:
        for case in ("approve_then_crash", "edited_after_yes"):
            for use_kit in (False, True):
                results["cases"][f"{fw} | {case} | {'with call4agents' if use_kit else 'without'}"] = run_case(fw, case, use_kit)
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "_child":
        FRAMEWORKS[sys.argv[2]](Path(sys.argv[3]), sys.argv[4], sys.argv[5] == "1", sys.argv[6])
    elif len(sys.argv) > 1 and sys.argv[1] == "one":
        # 2026-11-19: one run of one case, no guard, as JSON. Cases: approve_then_crash, no_crash
        # (control: start + approve, no crash, no extra resume), edited_after_yes
        print(json.dumps(run_case(sys.argv[2], sys.argv[3], False)))
    elif len(sys.argv) > 1 and sys.argv[1] == "one_guarded":
        # 2026-11-19 guarded rerun: the same case, with call4agents on the tool. Cases: approve_then_crash, no_crash
        print(json.dumps(run_case(sys.argv[2], sys.argv[3], True)))
    else:
        main()
