# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""MCP server + Call4Agents, runnable offline:

    pip install call4agents mcp
    python -m call4agents.examples.mcp_tool

An MCP client that loses a response often sends the same tools/call again. Here the server receives
create_ticket twice for one client session. One ticket is created; the repeat gets the same answer.
"""
import asyncio
import tempfile
from pathlib import Path


def main(record: str = "", mode: str = "adapter") -> dict:
    from mcp.server.mcpserver import Context, MCPServer

    import call4agents as c4a
    from call4agents.adapters import mcp as c4a_mcp

    c4a.init(record or str(Path(tempfile.mkdtemp()) / "ledger.sqlite"))
    tickets = []
    server = MCPServer("helpdesk")

    def create_ticket(title: str, ctx: Context) -> str:
        tickets.append(title)
        return f"ticket #{len(tickets)}: {title}"
    # mode: "adapter" (the way to use it), "none" (no kit: the control), "plain" (c4a.effect without the adapter)
    if mode == "adapter":
        create_ticket = c4a_mcp.effect(create_ticket, run=lambda ctx: "client-7")   # stdio: name the client
    elif mode == "plain":
        create_ticket = c4a.effect(create_ticket)
    server.tool()(create_ticket)

    async def client_retry():
        first = await server.call_tool("create_ticket", {"title": "printer on fire"})
        second = await server.call_tool("create_ticket", {"title": "printer on fire"})   # lost response, resent
        return first, second
    first, second = asyncio.run(client_retry())
    return {"tickets_created": len(tickets), "same_answer": str(first) == str(second)}


if __name__ == "__main__":
    print(main())
