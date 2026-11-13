# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""CrewAI, LlamaIndex and smolagents adapters with the REAL frameworks (offline, scripted models).
Run: python -m tests.test_adapters_more [--require-frameworks]
A framework that is not installed is skipped (and says so); --require-frameworks makes that a failure.

For each framework the example runs several ways:
  none    - no kit: the retry or resume sends again (the control: proves the test can see a duplicate)
  plain   - c4a.effect without the adapter (why the adapter exists, or, where it works, said so)
  adapter - the adapter: exactly one email
  approval=True - no yes: refused; a yes for other arguments: refused; a yes for these arguments: one email
No network: every connection to a host other than this machine is refused and counted (must be 0)."""
import importlib
import importlib.metadata
import importlib.util
import os
import socket
import sys
import tempfile
from pathlib import Path

for _name, _value in (("CREWAI_DISABLE_TELEMETRY", "true"), ("OTEL_SDK_DISABLED", "true"),
                      ("CREWAI_TRACING_ENABLED", "false"), ("CREWAI_DISABLE_TRACKING", "true"),
                      ("HF_HUB_OFFLINE", "1"), ("TOKENIZERS_PARALLELISM", "false")):
    os.environ.setdefault(_name, _value)

FAILED = []
NETWORK = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {str(detail)[:300]}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def has(module):
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def block_network():
    """Refuse (and count) any connection that leaves this machine. Loopback stays open: asyncio on
    Windows talks to itself through a local socket pair."""
    real_connect, real_connect_ex, real_lookup = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def local(sock, address):
        if getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX:
            return True
        host = address[0] if isinstance(address, tuple) else address
        return isinstance(host, str) and (host in ("localhost", "::1") or host.startswith("127."))

    def connect(self, address):
        if not local(self, address):
            NETWORK.append(str(address))
            raise OSError(f"network blocked by the test: {address}")
        return real_connect(self, address)

    def connect_ex(self, address):
        if not local(self, address):
            NETWORK.append(str(address))
            return 111
        return real_connect_ex(self, address)

    def lookup(host, *args, **kwargs):
        if host not in (None, "localhost", "::1") and not str(host).startswith("127."):
            NETWORK.append(f"lookup {host}")
            raise socket.gaierror(f"name lookup blocked by the test: {host}")
        return real_lookup(host, *args, **kwargs)
    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, lookup


def outcome(main, mode):
    record = str(Path(tempfile.mkdtemp(prefix="c4a-am-")) / "ledger.sqlite")
    try:
        return main(record, mode)
    except Exception as exc:  # noqa: BLE001 - the type is reported
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def errors_of(result):
    return " | ".join(str(e) for e in result.get("tool_errors", []))


def common(label, run):
    """The checks every framework gets: control, adapter, and the three approval cases."""
    none, adapter = run("none"), run("adapter")
    check(f"{label}: without the kit the retry/resume sends again (control)", none.get("emails_sent", 0) >= 2, none)
    check(f"{label}: with the adapter, exactly one email", adapter.get("emails_sent") == 1 and "error" not in adapter, adapter)
    a_none, a_other, a_exact = run("approval_none"), run("approval_other"), run("approval_exact")
    check(f"{label}: approval=True, no yes: refused (0 emails, ApprovalRequired)",
          a_none.get("emails_sent") == 0 and "ApprovalRequired" in errors_of(a_none), a_none)
    check(f"{label}: approval=True, a yes for other arguments: refused",
          a_other.get("emails_sent") == 0 and "ApprovalRequired" in errors_of(a_other), a_other)
    check(f"{label}: approval=True, a yes for exactly these arguments: one email",
          a_exact.get("emails_sent") == 1 and "error" not in a_exact, a_exact)
    return none


def crewai_checks():
    from call4agents.examples import crewai_tool as ex
    run = lambda mode: outcome(ex.main, mode)  # noqa: E731
    none = common("CrewAI", run)
    print(f"        (CrewAI without the kit: {none.get('emails_sent')} emails)")
    plain, in_run = run("plain"), run("plain_in_run")
    check("CrewAI: plain c4a.effect outside a run is refused (no run id; CrewAI passes no context object)",
          plain.get("emails_sent") == 0 and "no current run" in errors_of(plain), plain)
    check("CrewAI: plain c4a.effect inside `with c4a.run` also gives one (said plainly: the adapter adds the crew id)",
          in_run.get("emails_sent") == 1, in_run)

    import call4agents as c4a
    from call4agents.adapters import crewai as c4a_crew
    c4a.init(str(Path(tempfile.mkdtemp()) / "l.sqlite"))
    notes = []

    @c4a_crew.effect
    def note(context: str) -> str:          # in CrewAI a parameter named context is text the model wrote
        notes.append(context)
        return "ok"
    try:
        note("outside any crew")
        text = "no error"
    except RuntimeError as exc:
        text = str(exc)
    check("CrewAI: outside a crew and without c4a.run, a clear error naming the fix", "could not find a run id" in text
          and "c4a.run" in text, text)
    with c4a.run("job-1"):
        note("a")
        note("b")
        note("a")
    check("CrewAI: a parameter named `context` stays in the key (a and b both run, a once)", notes == ["a", "b"], notes)


def llamaindex_checks():
    from call4agents.examples import llamaindex_tool as ex
    run = lambda mode: outcome(ex.main, mode)  # noqa: E731
    none = common("LlamaIndex", run)
    print(f"        (LlamaIndex without the kit: {none.get('emails_sent')} emails)")
    plain, no_ctx, no_run = run("plain"), run("plain_no_ctx"), run("adapter_no_run")
    check("LlamaIndex: plain c4a.effect on a tool with a Context parameter is refused (Context cannot be in a key)",
          plain.get("emails_sent") == 0 and "TypeError: Context is not a type with a stable" in errors_of(plain), plain)
    check("LlamaIndex: plain c4a.effect on a tool WITHOUT a Context parameter gives one (said plainly)",
          no_ctx.get("emails_sent") == 1, no_ctx)
    check("LlamaIndex: the adapter without c4a.run refuses with a clear error (no silent shared run)",
          no_run.get("emails_sent") == 0 and "could not find a run id" in errors_of(no_run), no_run)

    import call4agents as c4a
    from call4agents.adapters import llamaindex as c4a_li
    c4a.init(str(Path(tempfile.mkdtemp()) / "l.sqlite"))
    seen = []

    @c4a_li.effect
    def lookup(ctx: str, x: int) -> str:     # named ctx but not a Context: the model's text, part of the key
        seen.append(ctx)
        return "ok"
    with c4a.run("job-1"):
        lookup("a", 1)
        lookup("b", 1)
    check("LlamaIndex: a parameter named ctx that is not a Context stays in the key", seen == ["a", "b"], seen)
    ids = []

    from llama_index.core.workflow import Context

    @c4a_li.effect(run=lambda ctx: "job-from-run-arg")
    def touch(ctx: Context, x: int) -> str:
        ids.append(c4a.current_run())
        return "ok"
    touch(object(), 1)
    check("LlamaIndex: run=lambda ctx: <id> gives the run id when no c4a.run is active", ids == ["job-from-run-arg"], ids)


def smolagents_checks():
    from call4agents.examples import smolagents_tool as ex
    run = lambda mode: outcome(ex.main, mode)  # noqa: E731
    none = common("smolagents", run)
    print(f"        (smolagents without the kit: {none.get('emails_sent')} emails)")
    plain, in_run = run("plain"), run("plain_in_run")
    check("smolagents: plain c4a.effect outside a run is refused (no run id; smolagents passes no context object)",
          plain.get("emails_sent") == 0 and "no current run" in errors_of(plain), plain)
    check("smolagents: plain c4a.effect inside `with c4a.run` also gives one in a ToolCallingAgent (said plainly)",
          in_run.get("emails_sent") == 1, in_run)
    code_none, lost, given = run("code_none"), run("code_adapter_run_lost"), run("code_adapter_run_arg")
    check("smolagents CodeAgent: without the kit the retry sends again (control)", code_none.get("emails_sent", 0) >= 2, code_none)
    check("smolagents CodeAgent: its worker thread loses `with c4a.run`; the adapter refuses and names the fix",
          lost.get("emails_sent") == 0 and "worker thread" in errors_of(lost), lost)
    check("smolagents CodeAgent: with run= the retry sends once", given.get("emails_sent") == 1, given)


def crash_checks(label, file):
    """`call4agents crashtest` on a real agent: killed at every step and restarted (separate processes)."""
    from call4agents.crashtest import crashtest, problems
    path = str(Path(__file__).resolve().parent / "crashtest_agents" / file)
    os.environ["AGENT_GUARD"] = "0"
    without = crashtest(path, timeout=600)   # one CrewAI start passed 120 s here under load
    os.environ["AGENT_GUARD"] = "1"
    guarded = crashtest(path, timeout=600)
    os.environ.pop("AGENT_GUARD")
    print(f"        {label} crashtest: {without.get('tested')} crash points without the guard, "
          f"{guarded.get('tested')} with it")
    seen_without = without if "error" in without else problems(without)
    seen_guarded = guarded if "error" in guarded else problems(guarded)
    check(f"{label} crashtest: the repeat is found without the guard",
          "send_email(to='bob')" in seen_without.get("twice", []), seen_without)
    check(f"{label} crashtest: nothing repeats with the guard, every crash point reached",
          "error" not in guarded and not seen_guarded["twice"] and guarded["reached"] == guarded["tested"], seen_guarded)


def main():
    """Flags: --require-frameworks, --no-crashtest (skip the slow part), --only-crashtest,
    --framework CrewAI|LlamaIndex|smolagents (only that one)."""
    block_network()
    require = "--require-frameworks" in sys.argv
    only = sys.argv[sys.argv.index("--framework") + 1] if "--framework" in sys.argv else None
    ran = 0
    for label, module, checks, crash_file in (("CrewAI", "crewai", crewai_checks, "crew_agent.py"),
                                              ("LlamaIndex", "llama_index.core.agent.workflow", llamaindex_checks,
                                               "li_agent.py"),
                                              ("smolagents", "smolagents", smolagents_checks, "smol_agent.py")):
        if only and label != only:
            continue
        if not has(module.split(".")[0]) or not has(module):
            print(f"  skip  {label}: not installed")
            if require:
                check(f"{label} is installed (--require-frameworks)", False)
            continue
        ran += 1
        print(f"  {label} {importlib.metadata.version({'CrewAI': 'crewai', 'LlamaIndex': 'llama-index-core', 'smolagents': 'smolagents'}[label])}")
        if "--only-crashtest" not in sys.argv:
            checks()
        if "--no-crashtest" not in sys.argv:
            crash_checks(label, crash_file)
    check("no connection left this machine during the tests", not NETWORK, NETWORK[:5])
    print(f"\n{len(FAILED)} adapter check(s) failed" if FAILED else f"\nall adapter checks pass ({ran} framework(s) installed)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
