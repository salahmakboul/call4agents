"""Approval tickets (experimental). Run: python -m tests.test_tickets [--require-frameworks]

Offline, 0 LLM calls. T1-T8 need nothing but the kit; T9-T10 use the real Pydantic AI and are skipped
without it, unless --require-frameworks (CI) makes a missing framework a failure.

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
    (pydantic-ai#6452 shape), and a denied call does not run either."""
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

    frameworks("--require-frameworks" in sys.argv)
    if FAILED:
        print(f"\n{len(FAILED)} ticket check(s) failed")
        return 1
    print("\nall ticket checks pass")
    return 0


def frameworks(required):
    try:
        from pydantic_ai import Agent, DeferredToolRequests, RunContext
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
        from pydantic_ai.models.function import FunctionModel
    except ImportError:
        check("T9/T10 Pydantic AI installed", not required, "pydantic-ai is required here")
        if not required:
            print("  skip  T9/T10 (pydantic-ai not installed)")
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


if __name__ == "__main__":
    sys.exit(main())
