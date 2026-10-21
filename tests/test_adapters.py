"""Framework adapters with the REAL frameworks (offline, fake models). Run: python -m tests.test_adapters
Each framework is skipped (and says so) if it is not installed; CI installs all of them.

For each framework, the same example runs three ways:
  none    - no kit: the retry sends again (the control: proves the test can see a duplicate)
  plain   - c4a.effect without the adapter: refused (the framework context cannot be part of a key)
  adapter - the adapter: exactly one email / ticket"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

FAILED = []
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def has(module):
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def outcome(main, mode):
    record = str(Path(tempfile.mkdtemp(prefix="c4a-ad-")) / "ledger.sqlite")
    try:
        return main(record, mode)
    except Exception as exc:  # noqa: BLE001 - "plain" is expected to fail; the type is reported
        return {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}


def main():
    cases = [("Pydantic AI", "pydantic_ai", "call4agents.examples.pydantic_ai_tool", "emails_sent"),
             ("OpenAI Agents SDK", "agents.models.interface", "call4agents.examples.openai_agents_tool", "emails_sent"),
             ("MCP", "mcp.server.mcpserver", "call4agents.examples.mcp_tool", "tickets_created")]
    ran = 0
    for label, module, example, field in cases:
        if not has(module.split(".")[0]) or not has(module):
            print(f"  skip  {label}: not installed")
            continue
        ran += 1
        mod = importlib.import_module(example)
        none, plain, adapter = (outcome(mod.main, m) for m in ("none", "plain", "adapter"))
        check(f"{label}: without the kit the retry acts twice (control)", none.get(field) == 2, none)
        plain_failed = "error" in plain or plain.get(field) != 1
        check(f"{label}: plain c4a.effect does not give one action here (why the adapter exists)", plain_failed, plain)
        check(f"{label}: with the adapter, exactly one", adapter.get(field) == 1 and "error" not in adapter, adapter)
        if label == "MCP":
            check("MCP: the repeat gets the same answer as the first call", adapter.get("same_answer") is True, adapter)

    if has("mcp.server.mcpserver"):         # no run id at all must be a clear error, not a shared run
        import asyncio

        from mcp.server.mcpserver import Context, MCPServer

        import call4agents as c4a
        from call4agents.adapters import mcp as c4a_mcp
        c4a.init(str(Path(tempfile.mkdtemp()) / "l.sqlite"))
        server = MCPServer("t")

        def touch(x: int, ctx: Context) -> str:
            return "ok"
        server.tool()(c4a_mcp.effect(touch))
        try:
            text = str(asyncio.run(server.call_tool("touch", {"x": 1})))
        except Exception as exc:  # noqa: BLE001 - MCP wraps the tool's error; the cause carries our message
            chain, e = [], exc
            while e is not None:
                chain.append(str(e))
                e = e.__cause__ or e.__context__
            text = " | ".join(chain)
        check("MCP: no session id and no run= gives a clear error naming the fix", "could not find a run id" in text, text[:200])
    print(f"\n{len(FAILED)} adapter check(s) failed" if FAILED else f"\nall adapter checks pass ({ran} framework(s) installed)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
