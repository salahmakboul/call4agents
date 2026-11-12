"""Adapters for Google ADK and Microsoft Agent Framework, with the REAL frameworks, offline (scripted
models, no API key, no network). Run: python -m tests.test_adapters_more2 [--require-frameworks]

A framework that is not installed is skipped (and says so); --require-frameworks (CI) makes that a failure.
For each framework:
  none / plain / adapter   the example sends the same message twice in one session (a retry):
                           no kit = 2 (the control), plain c4a.effect != 1, the adapter = exactly 1
  approval                 effect(approval=True): no yes -> refused; a yes for other arguments -> refused;
                           a yes for the exact arguments -> exactly 1 (and a retry stays 1)
  the framework's own approval, through the glue (ADK confirm(), Agent Framework respond()):
                           yes -> 1; the call changed after the yes -> refused (and, as a control, the same
                           change WITHOUT the kit sends to the changed address); a no after a yes, then the
                           old yes delivered -> refused; a tool without approval=True -> refused by the glue
  crashtest                the real agent is killed at every step and restarted: without the guard a send
                           repeats, with the guard nothing repeats
Every socket connection to a non-loopback address fails the run (checked at the end)."""
import importlib.util
import json
import logging
import os
import socket
import sys
import tempfile
from pathlib import Path

FAILED = []
HERE = Path(__file__).resolve().parent
NETWORK = []
for _k in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"):
    os.environ.pop(_k, None)
_connect = socket.socket.connect


def _local_only(self, address):
    if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost"):
        return _connect(self, address)
    NETWORK.append(repr(address))
    raise OSError(f"test_adapters_more2: network call refused: {address!r}")


socket.socket.connect = _local_only
for _name in ("google_adk", "google", "agent_framework"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)   # tool errors are expected here; keep the output short


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def has(module):
    try:
        return importlib.util.find_spec(module.split(".")[0]) is not None and importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def fresh():
    import call4agents as c4a
    c4a.init(str(Path(tempfile.mkdtemp(prefix="c4a-more2-")) / "ledger.sqlite"))


def outcome(main, mode):
    record = str(Path(tempfile.mkdtemp(prefix="c4a-more2-")) / "ledger.sqlite")
    try:
        return main(record, mode)
    except Exception as exc:  # noqa: BLE001 - "plain" is expected to fail; the type is reported
        return {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}


def caught(fn):
    """Run fn; return the exception it raised (or None). Follows the cause chain to a call4agents error."""
    try:
        fn()
        return None
    except Exception as exc:  # noqa: BLE001
        return exc


def swap(value, old="bob", new="eve"):
    """The stored call, changed after the yes: every `old` becomes `new` (strings inside dicts and lists)."""
    if isinstance(value, dict):
        return {k: swap(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [swap(v, old, new) for v in value]
    return value.replace(old, new) if isinstance(value, str) else value


def basic(label, example):
    mod = __import__(example, fromlist=["main"])
    none, plain, adapter = (outcome(mod.main, m) for m in ("none", "plain", "adapter"))
    check(f"{label}: without the kit the retry sends twice (control)", none.get("emails_sent") == 2, none)
    check(f"{label}: plain c4a.effect does not give one send here (why the adapter exists)",
          "error" in plain or plain.get("emails_sent") != 1, plain)
    check(f"{label}: with the adapter, exactly one send", adapter.get("emails_sent") == 1 and "error" not in adapter,
          adapter)


# ---------------------------------------------------------------- Google ADK
def adk_checks():
    from google.adk.tools import FunctionTool, ToolContext

    import call4agents as c4a
    from call4agents.adapters import google_adk as c4a_adk
    from call4agents.examples.google_adk_tool import APP, SESSION, USER, build, send

    basic("ADK", "call4agents.examples.google_adk_tool")
    outbox = []

    def send_email(to: str, body: str, tool_context: ToolContext) -> str:
        """Send an email."""
        outbox.append(to)
        return f"sent to {to}"
    guarded = c4a_adk.effect(approval=True)(send_email)

    def session_of(service):
        return service.sessions[APP][USER][SESSION]

    # c4a approvals with ADK's normal tool call (no ADK confirmation)
    fresh()
    outbox.clear()
    runner, service = build([guarded])
    err = caught(lambda: send(runner, "email bob"))
    check("ADK approval: no yes -> refused, nothing sent", isinstance(err, c4a.ApprovalRequired) and outbox == [],
          (repr(err)[:120], outbox))
    fresh()
    runner, service = build([guarded])
    with c4a.run(c4a_adk.run_id(session_of(service))):
        c4a.approve(guarded, to="eve", body="hi")
    err = caught(lambda: send(runner, "email bob"))
    check("ADK approval: a yes for other arguments -> refused", isinstance(err, c4a.ApprovalRequired) and outbox == [],
          (repr(err)[:120], outbox))
    fresh()
    runner, service = build([guarded])
    with c4a.run(c4a_adk.run_id(session_of(service))):
        c4a.approve(guarded, to="bob", body="hi")
    send(runner, "email bob")
    send(runner, "email bob")
    check("ADK approval: a yes for the exact arguments -> one send, and the retry does not send again",
          outbox == ["bob"], outbox)

    # ADK's own tool confirmation, bound to c4a by confirm()
    def asked(events):
        return [fc for e in events for fc in e.get_function_calls() if fc.name == c4a_adk.REQUEST]

    def confirmed_flow(tool, edit=False, change_mind=False, with_kit=True):
        fresh()
        outbox.clear()
        ftool = FunctionTool(tool, require_confirmation=True)
        runner, service = build([ftool])
        calls = asked(send(runner, "email bob"))
        if len(calls) != 1 or outbox:
            return {"error": f"expected one confirmation request and no send, got {len(calls)} / {outbox}"}
        call, session = calls[0], session_of(service)
        if with_kit:
            answer = c4a_adk.confirm(call, True, tools=[ftool], session=session)
            if change_mind:                  # the person says no after all; the old yes is what reaches ADK
                c4a_adk.confirm(call, False, tools=[ftool], session=session)
        else:
            from google.genai import types
            answer = types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
                id=call.id, name=c4a_adk.REQUEST, response={"confirmed": True}))])
        if edit:                             # the stored call (the model's call and ADK's copy) changed after the yes
            for event in session.events:
                for fc in event.get_function_calls():
                    fc.args = swap(fc.args)
        err = caught(lambda: send(runner, answer))
        return {"sent": list(outbox), "error": repr(err)[:120] if err else None,
                "refused": isinstance(err, c4a.ApprovalRequired)}

    r = confirmed_flow(guarded)
    check("ADK confirmation + confirm(): yes -> exactly one send", r.get("sent") == ["bob"] and not r.get("error"), r)
    r = confirmed_flow(guarded, edit=True)
    check("ADK confirmation + confirm(): the call changed after the yes -> refused, nothing sent",
          r.get("refused") and r.get("sent") == [], r)
    r = confirmed_flow(send_email, edit=True, with_kit=False)
    check("ADK confirmation without the kit: the changed call is sent (control: ADK alone does not see it)",
          r.get("sent") == ["eve"], r)
    r = confirmed_flow(guarded, change_mind=True)
    check("ADK confirmation + confirm(): a no after the yes, then the old yes reaches ADK -> refused",
          r.get("refused") and r.get("sent") == [], r)
    fresh()
    ftool = FunctionTool(c4a_adk.effect(send_email), require_confirmation=True)
    runner, service = build([ftool])
    call = asked(send(runner, "email bob"))[0]
    err = caught(lambda: c4a_adk.confirm(call, True, tools=[ftool], session=session_of(service)))
    check("ADK confirm(): a tool without approval=True is refused (its yes would bind nothing)",
          isinstance(err, ValueError) and "approval=True" in str(err), repr(err))


# ---------------------------------------------------------------- Microsoft Agent Framework
def maf_checks():
    from agent_framework import AgentSession, Content, FunctionInvocationContext, tool

    import call4agents as c4a
    from call4agents.adapters import agent_framework as c4a_maf
    from call4agents.examples.agent_framework_tool import build, send

    basic("Agent Framework", "call4agents.examples.agent_framework_tool")
    outbox, errors = [], []

    def send_email(to: str, body: str, ctx: FunctionInvocationContext) -> str:
        """Send an email."""
        outbox.append(to)
        return f"sent to {to}"
    guarded = c4a_maf.effect(approval=True)(send_email)

    def watched(fn):                         # Agent Framework hands tool errors to the model: keep their type
        def call(*a, **k):
            try:
                return fn(*a, **k)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
                raise
        import functools
        return functools.update_wrapper(call, fn)

    def refused():
        return any(isinstance(e, c4a.ApprovalRequired) for e in errors)

    fresh()
    outbox.clear()
    errors.clear()
    agent, session = build([tool(watched(guarded))])
    send(agent, session, "email bob")
    check("Agent Framework approval: no yes -> refused, nothing sent", refused() and outbox == [], (errors, outbox))
    fresh()
    errors.clear()
    agent, session = build([tool(watched(guarded))])
    with c4a.run(c4a_maf.run_id(session)):
        c4a.approve(guarded, to="eve", body="hi")
    send(agent, session, "email bob")
    check("Agent Framework approval: a yes for other arguments -> refused", refused() and outbox == [], (errors, outbox))
    fresh()
    errors.clear()
    agent, session = build([tool(watched(guarded))])
    with c4a.run(c4a_maf.run_id(session)):
        c4a.approve(guarded, to="bob", body="hi")
    send(agent, session, "email bob")
    send(agent, session, "email bob")
    check("Agent Framework approval: a yes for the exact arguments -> one send, and the retry does not send again",
          outbox == ["bob"] and not errors, (errors, outbox))

    # Agent Framework's own approval_mode, bound to c4a by respond()
    def approval_flow(fn, edit=False, change_mind=False, with_kit=True):
        fresh()
        outbox.clear()
        errors.clear()
        ftool = tool(watched(fn), approval_mode="always_require")
        agent, session = build([ftool])
        requests = send(agent, session, "email bob").user_input_requests
        if len(requests) != 1 or outbox:
            return {"error": f"expected one approval request and no send, got {len(requests)} / {outbox}"}
        request = requests[0]
        if with_kit:
            answer = c4a_maf.respond(request, True, tools=[ftool], session=session)
            if change_mind:
                c4a_maf.respond(request, False, tools=[ftool], session=session)
        else:
            answer = request.to_function_approval_response(approved=True)
        if edit:                             # the stored session and the queued answer changed after the yes
            session = AgentSession.from_dict(swap(session.to_dict()))
            answer = Content.from_dict(swap(answer.to_dict()))
        send(agent, session, [answer])
        return {"sent": list(outbox), "errors": [repr(e)[:100] for e in errors], "refused": refused()}

    r = approval_flow(guarded)
    check("Agent Framework approval_mode + respond(): yes -> exactly one send", r.get("sent") == ["bob"]
          and not r.get("errors"), r)
    r = approval_flow(guarded, edit=True)
    check("Agent Framework approval_mode + respond(): the call changed after the yes -> refused, nothing sent",
          r.get("refused") and r.get("sent") == [], r)
    r = approval_flow(send_email, edit=True, with_kit=False)
    check("Agent Framework approval_mode without the kit: the changed call is sent (control)", r.get("sent") == ["eve"], r)
    r = approval_flow(guarded, change_mind=True)
    check("Agent Framework approval_mode + respond(): a no after the yes, then the old yes is delivered -> refused",
          r.get("refused") and r.get("sent") == [], r)
    fresh()
    ftool = tool(c4a_maf.effect(send_email), approval_mode="always_require")
    agent, session = build([ftool])
    request = send(agent, session, "email bob").user_input_requests[0]
    err = caught(lambda: c4a_maf.respond(request, True, tools=[ftool], session=session))
    check("Agent Framework respond(): a tool without approval=True is refused (its yes would bind nothing)",
          isinstance(err, ValueError) and "approval=True" in str(err), repr(err))


# ---------------------------------------------------------------- crashtest on the real agents
def crash_checks(label, file):
    from call4agents.crashtest import crashtest, problems
    path = str(HERE / "crashtest_agents" / file)
    os.environ["AGENT_GUARD"] = "0"
    without = crashtest(path)
    os.environ["AGENT_GUARD"] = "1"
    guarded = crashtest(path)
    os.environ.pop("AGENT_GUARD")
    print(f"        {label} crashtest: {without.get('tested')} crash points without the guard, "
          f"{guarded.get('tested')} with it")
    check(f"{label} crashtest: the repeat is found without the guard",
          "send_email(to='bob')" in problems(without)["twice"], problems(without) if "error" not in without else without)
    check(f"{label} crashtest: nothing repeats with the guard, every crash point reached",
          "error" not in guarded and not problems(guarded)["twice"] and guarded["reached"] == guarded["tested"],
          guarded if "error" in guarded else problems(guarded))


def main():
    require = "--require-frameworks" in sys.argv
    ran = 0
    for label, module, run, crash_file in (("Google ADK", "google.adk.runners", adk_checks, "adk_agent.py"),
                                           ("Agent Framework", "agent_framework", maf_checks, "maf_agent.py")):
        if not has(module):
            if require:
                check(f"{label} is installed", False)
            else:
                print(f"  skip  {label}: not installed")
            continue
        ran += 1
        run()
        if "--no-crashtest" not in sys.argv:
            crash_checks(label, crash_file)
    check("no network call was attempted", not NETWORK, NETWORK[:3])
    print(f"\n{len(FAILED)} check(s) failed" if FAILED else f"\nall checks pass ({ran} framework(s) installed)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
