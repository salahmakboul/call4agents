"""Approval tickets (experimental). Run: python -m tests.test_tickets [--require-frameworks]

Offline, 0 LLM calls. T1-T8 and T11 need nothing but the kit; T9-T13 use the real Pydantic AI, OpenAI Agents
SDK and LangGraph and are skipped without them, unless --require-frameworks (CI) makes that a failure.

T1 a ticket decided "approve" lets exactly that call run, once; the decision records who decided.
T2 a ticket with a changed signature or the wrong format is refused.
T3 a pause edited in the record (its approval copied from another pause, or its expiry moved) is refused.
T4 a ticket issued by another record is refused, even with the same secret.
T5 a ticket is decided once: a second "approve", or a later "deny", is refused.
T6 an expired ticket is refused.
T7 a ticket decided "deny": if the framework runs the tool anyway, it does not run (langchain#40491 shape).
T8 approved for the shown arguments, called with other ones: it does not run (pydantic-ai#6968 shape).
T9 Pydantic AI: tickets + results; the approved call runs once.
T10 Pydantic AI: the client changes the call in the history before resuming: it does not run
    (pydantic-ai#6452 shape), and a denied call does not run either.
T11 the same undecided call paused again gets the same ticket (a LangGraph node re-runs on resume).
T12 OpenAI Agents SDK: tickets + apply; the approved call runs once; a call edited in the stored state, or a
    denied one, does not run.
T13 LangGraph: pause in the node, ticket in the interrupt; the approved call runs once and the re-run node gets
    the same ticket; a call edited in the saved state, or a denied one, does not run."""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import call4agents as c4a

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def fresh():
    path = Path(tempfile.mkdtemp(prefix="c4a-tickets-")) / "l.sqlite"
    return c4a.init(str(path), secret=b"t" * 16), path


def guarded(name, fn):
    """Run one group of checks; an exception fails that group but never hides the groups after it
    (a mutation that breaks one group must not skip the framework checks)."""
    try:
        fn()
    except Exception as exc:
        check(f"{name} ran to the end", False, f"{type(exc).__name__}: {exc}")


def refused(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        return "accepted"
    except c4a.TicketRefused as exc:
        return str(exc)


def main():
    paid = []

    @c4a.effect(name="t.pay", approval=True)
    def pay(amount, to):
        paid.append((amount, to))
        return "paid"

    # T1
    lg, path = fresh()
    with c4a.run("r1"):
        t = c4a.pause(pay, amount=10, to="alice")
    info = c4a.decide(t, "approve", by="sam")
    with c4a.run("r1"):
        first, second = pay(10, "alice"), pay(10, "alice")
    by = [e["payload"].get("by") for e in lg.entries("r1") if e["kind"] == "approval"]
    check("T1 the approved call runs once", paid == [(10, "alice")] and first == second == "paid", paid)
    check("T1 the decision names the call and who decided",
          info["tool"] == "t.pay" and info["args"] == {"amount": 10, "to": "alice"} and by == ["sam"], (info, by))

    # T2
    lg, path = fresh()
    with c4a.run("r2"):
        t = c4a.pause(pay, amount=10, to="alice")
    flipped = t[:-1] + ("0" if t[-1] != "0" else "1")
    check("T2 a changed signature is refused", "signature" in refused(c4a.decide, flipped, "approve"))
    check("T2 something that is not a ticket is refused", "not a Call4Agents ticket" in refused(c4a.decide, "abc", "approve"))

    # T3
    lg, path = fresh()
    with c4a.run("r3"):
        good = c4a.pause(pay, amount=10, to="alice")
        evil = c4a.pause(pay, amount=9999, to="mallory")
    db = sqlite3.connect(path)
    rows = {r[0]: r[1] for r in db.execute("SELECT key, payload FROM entries WHERE kind = 'pause'")}
    good_key = "ticket\x00" + good.split(".")[1]
    evil_key = "ticket\x00" + evil.split(".")[1]
    import json
    g, e = json.loads(rows[good_key]), json.loads(rows[evil_key])
    g2 = dict(g, mac=e["mac"], args=e["args"])                      # the "alice" pause now carries mallory's call
    db.execute("UPDATE entries SET payload = ? WHERE key = ?", (json.dumps(g2), good_key))
    db.commit()
    check("T3 a pause edited to another call is refused", "signature" in refused(c4a.decide, good, "approve"))
    e2 = dict(e, exp=e["exp"] + 10 ** 6)                            # expiry moved
    db.execute("UPDATE entries SET payload = ? WHERE key = ?", (json.dumps(e2), evil_key))
    db.commit()
    check("T3 a pause with its expiry moved is refused", "signature" in refused(c4a.decide, evil, "approve"))
    with c4a.run("r3"):
        try:
            pay(9999, "mallory")
            ran = True
        except c4a.ApprovalRequired:
            ran = False
    check("T3 nothing was approved by the edits", not ran and (9999, "mallory") not in paid)

    # T4
    lg, path = fresh()
    with c4a.run("r4"):
        t = c4a.pause(pay, amount=10, to="alice")
    fresh()                                                          # another record, same secret
    check("T4 a ticket from another record is refused", "never issued" in refused(c4a.decide, t, "approve"))

    # T5
    lg, path = fresh()
    with c4a.run("r5"):
        t = c4a.pause(pay, amount=10, to="alice")
    c4a.decide(t, "approve")
    check("T5 a second decision is refused", "already decided" in refused(c4a.decide, t, "approve"))
    check("T5 a later deny on a decided ticket is refused", "already decided" in refused(c4a.decide, t, "deny"))

    # T6
    lg, path = fresh()
    with c4a.run("r6"):
        t = c4a.pause(pay, ttl=0.05, amount=10, to="alice")
    time.sleep(0.2)
    check("T6 an expired ticket is refused", "expired" in refused(c4a.decide, t, "approve"))

    # T7
    lg, path = fresh()
    paid.clear()
    with c4a.run("r7"):
        t = c4a.pause(pay, amount=10, to="alice")
    c4a.decide(t, "deny", by="sam")
    with c4a.run("r7"):
        try:
            pay(10, "alice")                                         # the framework runs it anyway
            ran = True
        except c4a.ApprovalRequired:
            ran = False
    check("T7 a denied call does not run even if the framework calls it", not ran and paid == [])

    # T8
    lg, path = fresh()
    with c4a.run("r8"):
        t = c4a.pause(pay, amount=10, to="alice")
    c4a.decide(t, "approve")
    with c4a.run("r8"):
        try:
            pay(10, "alice-rewritten")                               # what runs is not what was shown
            ran = True
        except c4a.ApprovalRequired:
            ran = False
    check("T8 a call with other arguments than the approved ones does not run", not ran and paid == [])

    def t11():
        lg, path = fresh()
        with c4a.run("r11"):
            t1 = c4a.pause(pay, amount=10, to="alice")
            t2 = c4a.pause(pay, amount=10, to="alice")         # the same call paused again (a node re-runs)
            other = c4a.pause(pay, amount=11, to="alice")
        c4a.decide(t1, "approve")
        with c4a.run("r11"):
            t3 = c4a.pause(pay, amount=10, to="alice")         # approved, not used yet: still the same ticket
            pay(10, "alice")                                   # the action uses the "yes"
            t4 = c4a.pause(pay, amount=10, to="alice")         # asked again later: a new ticket
        c4a.decide(t4, "deny")
        with c4a.run("r11"):
            t5 = c4a.pause(pay, amount=10, to="alice")         # after a "no": a new ticket
        pauses = sum(1 for e in lg.entries("r11") if e["kind"] == "pause")
        check("T11 the same undecided call paused again gets the same ticket", t1 == t2 and other != t1, (t1, t2))
        check("T11 approved but not used yet: the same ticket again", t3 == t1, (t1, t3))
        check("T11 after the action ran, or after a no, a new pause gets a new ticket",
              len({t1, t4, t5}) == 3 and pauses == 4, pauses)

    guarded("T11", t11)
    frameworks("--require-frameworks" in sys.argv)
    if FAILED:
        print(f"\n{len(FAILED)} ticket check(s) failed")
        return 1
    print("\nall ticket checks pass")
    return 0


def frameworks(required):
    try:
        import agents  # noqa: F401  (the OpenAI Agents SDK)
        import langgraph.checkpoint.sqlite  # noqa: F401
        from pydantic_ai import Agent, DeferredToolRequests, RunContext
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
        from pydantic_ai.models.function import FunctionModel
    except ImportError:
        check("T9-T13 frameworks installed", not required,
              "pydantic-ai, openai-agents and langgraph-checkpoint-sqlite are required here")
        if not required:
            print("  skip  T9-T13 (pydantic-ai, openai-agents or langgraph-checkpoint-sqlite not installed)")
        return
    from call4agents.adapters import pydantic_ai as c4a_pai

    def model(messages, info):
        if any(getattr(p, "part_kind", "") == "tool-return" for p in messages[-1].parts):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart("pay", {"amount": 10, "to": "alice"}, tool_call_id="c1")])

    def build(paid):
        agent = Agent(FunctionModel(model), output_type=[str, DeferredToolRequests])

        @agent.tool(requires_approval=True)
        @c4a_pai.effect(approval=True)
        def pay(ctx: RunContext, amount: int, to: str) -> str:
            paid.append((amount, to))
            return "paid"
        return agent, pay

    # T9
    fresh()
    paid = []
    agent, pay = build(paid)
    first = agent.run_sync("pay alice 10", conversation_id="conv-9")
    tickets = c4a_pai.tickets(first, [pay])
    results = c4a_pai.results({tickets["c1"]: "approve"}, by="sam")
    agent.run_sync(message_history=first.all_messages(), deferred_tool_results=results, conversation_id="conv-9")
    check("T9 Pydantic AI: the approved call runs once", paid == [(10, "alice")], paid)

    # T10
    fresh()
    paid = []
    agent, pay = build(paid)
    first = agent.run_sync("pay alice 10", conversation_id="conv-10")
    tickets = c4a_pai.tickets(first, [pay])
    history = first.all_messages()
    for part in history[-1].parts:                                  # the client edits the call before resuming
        if isinstance(part, ToolCallPart):
            part.args = {"amount": 9999, "to": "mallory"}
    results = c4a_pai.results({tickets["c1"]: "approve"}, by="sam")
    try:
        agent.run_sync(message_history=history, deferred_tool_results=results, conversation_id="conv-10")
    except Exception:
        pass                                                         # ApprovalRequired surfaces from the tool
    check("T10 Pydantic AI: a call changed by the client does not run", paid == [], paid)

    fresh()
    paid = []
    agent, pay = build(paid)
    first = agent.run_sync("pay alice 10", conversation_id="conv-11")
    tickets = c4a_pai.tickets(first, [pay])
    results = c4a_pai.results({tickets["c1"]: "deny"}, by="sam")
    agent.run_sync(message_history=first.all_messages(), deferred_tool_results=results, conversation_id="conv-11")
    check("T10 Pydantic AI: a denied call does not run", paid == [], paid)

    guarded("T12", run_openai_agents)
    guarded("T13", run_langgraph)


def run_openai_agents():
    import asyncio
    import json
    import os
    os.environ.setdefault("OPENAI_API_KEY", "not-used-fake-model")
    from agents import Agent, ModelResponse, RunContextWrapper, RunState, Runner, function_tool, set_tracing_disabled
    from agents.models.interface import Model
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
    from call4agents.adapters import openai_agents as c4a_oa
    set_tracing_disabled(True)

    class FakeModel(Model):
        async def get_response(self, *a, **k):
            items = k.get("input", a[1] if len(a) > 1 else [])
            items = items if isinstance(items, list) else []
            if any((i.get("type") if isinstance(i, dict) else getattr(i, "type", "")) == "function_call_output"
                   for i in items):
                out = [ResponseOutputMessage(id="m", type="message", role="assistant", status="completed",
                                             content=[ResponseOutputText(type="output_text", text="done", annotations=[])])]
            else:
                out = [ResponseFunctionToolCall(id="fc1", call_id="c1", type="function_call", name="pay",
                                                arguments=json.dumps({"amount": 10, "to": "alice"}), status="completed")]
            return ModelResponse(output=out, usage=Usage(), response_id=None)

        def stream_response(self, *a, **k):
            raise NotImplementedError

    def build(paid):
        def pay(ctx: RunContextWrapper[dict], amount: int, to: str) -> str:
            """Pay someone."""
            paid.append((amount, to))
            return "paid"
        pay_fn = c4a_oa.effect(pay, approval=True)             # keep this one for tickets()
        agent = Agent(name="payer", instructions="pay", model=FakeModel(),
                      tools=[function_tool(pay_fn, needs_approval=True)])
        return agent, pay_fn

    def edit_call(blob, new_id=False):
        """The client edits the stored call: every pay arguments object becomes 9999 to mallory, and with
        new_id the call id changes too (so the SDK sees a new call, not a changed one)."""
        if isinstance(blob, dict):
            return {k: edit_call(v, new_id) for k, v in blob.items()}
        if isinstance(blob, list):
            return [edit_call(v, new_id) for v in blob]
        if new_id and blob == "c1":
            return "c2"
        if isinstance(blob, str) and blob.startswith("{") and '"alice"' in blob:
            try:
                value = json.loads(blob)
            except ValueError:
                return blob
            if value == {"amount": 10, "to": "alice"}:
                return json.dumps({"amount": 9999, "to": "mallory"})
        return blob

    async def flow(decision, tamper):
        paid = []
        agent, pay_fn = build(paid)
        first = await Runner.run(agent, "pay alice 10", context={"session_id": f"sess-{decision}-{tamper}"})
        tickets = c4a_oa.tickets(first, [pay_fn])
        blob = first.to_state().to_json()
        if tamper:
            blob = edit_call(blob, new_id=tamper == "args+id")
        stopped = None
        try:
            state = RunState.from_json(agent, blob)
            state = await state if asyncio.iscoroutine(state) else state
            c4a_oa.apply(state, {tickets["c1"]: decision}, by="sam")
            await Runner.run(agent, state)
        except Exception as exc:                                 # who stopped it, if anyone
            stopped = type(exc).__name__
        return paid, stopped

    fresh()
    paid, stopped = asyncio.run(flow("approve", None))
    check("T12 OpenAI Agents SDK: the approved call runs once", paid == [(10, "alice")] and stopped is None, (paid, stopped))
    fresh()
    paid, stopped = asyncio.run(flow("approve", "args"))
    check("T12 OpenAI Agents SDK: arguments edited under the same call id do not run (the SDK itself refuses)",
          paid == [] and stopped == "ModelBehaviorError", (paid, stopped))
    fresh()
    paid, stopped = asyncio.run(flow("approve", "args+id"))
    check("T12 OpenAI Agents SDK: a call replaced with a new id does not run (the ticket names the old call)",
          paid == [] and stopped == "ValueError", (paid, stopped))
    fresh()
    paid, stopped = asyncio.run(flow("deny", None))
    check("T12 OpenAI Agents SDK: a denied call does not run", paid == [], (paid, stopped))


def run_langgraph():
    import sqlite3 as sqlite
    from typing import TypedDict
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
    from call4agents.adapters import langgraph as c4a_lg

    def flow(decision, tamper):
        lg, path = fresh()
        paid = []

        @c4a_lg.effect(approval=True)
        def pay(amount: int, to: str) -> str:
            paid.append((amount, to))
            return "paid"

        class S(TypedDict, total=False):
            call: dict
            ticket: str
            result: str

        def propose(state):
            return {"call": {"amount": 10, "to": "alice"}}

        def ask(state, config):
            ticket = c4a_lg.pause(pay, config, **state["call"])
            interrupt({"call": state["call"], "ticket": ticket})
            return {"ticket": ticket}

        def tools(state):
            try:
                return {"result": pay(**state["call"])}
            except c4a.ApprovalRequired:
                return {"result": "refused"}

        g = StateGraph(S)
        for n, f in (("propose", propose), ("ask", ask), ("tools", tools)):
            g.add_node(n, f)
        g.add_edge(START, "propose")
        g.add_edge("propose", "ask")
        g.add_edge("ask", "tools")
        g.add_edge("tools", END)
        graph = g.compile(checkpointer=SqliteSaver(sqlite.connect(path.parent / "lg.sqlite", check_same_thread=False)))
        cfg = {"configurable": {"thread_id": f"thread-{decision}-{tamper}"}}
        out = graph.invoke({}, cfg)
        ticket = out["__interrupt__"][0].value["ticket"]
        if tamper:                                               # the saved state is edited after the pause
            graph.update_state(cfg, {"call": {"amount": 9999, "to": "mallory"}})
        c4a.decide(ticket, decision, by="sam")
        graph.invoke(Command(resume="decided"), cfg)
        pauses = sum(1 for e in lg.entries(cfg["configurable"]["thread_id"]) if e["kind"] == "pause")
        return paid, pauses

    paid, pauses = flow("approve", False)
    check("T13 LangGraph: the approved call runs once", paid == [(10, "alice")], paid)
    check("T13 LangGraph: the node re-ran on resume and got the same ticket back", pauses == 1, pauses)
    paid, _ = flow("approve", True)
    check("T13 LangGraph: a call edited in the saved state does not run", paid == [], paid)
    paid, _ = flow("deny", False)
    check("T13 LangGraph: a denied call does not run", paid == [], paid)


if __name__ == "__main__":
    sys.exit(main())
