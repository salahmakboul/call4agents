# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""MCP server adapter (the official `mcp` Python SDK).

    from call4agents.adapters import mcp as c4a_mcp

    @server.tool()                   # the SDK's decorator goes on top
    @c4a_mcp.effect                  # once per MCP session, tool and exact arguments
    def create_ticket(title: str, ctx: Context) -> str: ...

Why: an MCP client that loses a response often sends the same tools/call again. With this adapter
the repeat returns the recorded result instead of creating a second ticket.

The run id comes from an active `with c4a.run(...)`, else from the `Mcp-Session-Id` header of the
streamable-HTTP session. On stdio there is no session id: pass run=lambda ctx: "<id>" (for example
a client or user id) or enter `c4a.run(...)` yourself. The Context parameter is left out of the key.
"""

from typing import Any, Callable, Optional

from call4agents.adapters._common import context_param, wrap_tool


def _session(ctx: Any) -> Optional[str]:
    try:      # outside a real request (an in-process call), even reading ctx.headers raises
        headers = getattr(ctx, "headers", None)
        if headers:
            for k, v in dict(headers).items():
                if str(k).lower() == "mcp-session-id" and v:
                    return f"mcp:{v}"
    except Exception:  # noqa: BLE001 - a missing request context means: no session id
        return None
    return None


def effect(fn: Optional[Callable] = None, **options: Any):
    """c4a.effect for an MCP server tool. The Context parameter may be anywhere in the signature."""
    def wrap(f: Callable) -> Callable:
        return wrap_tool(f, ("Context",), _session, "MCP", dict(options))
    return wrap(fn) if fn is not None else wrap


__all__ = ["effect", "context_param"]
