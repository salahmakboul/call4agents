# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Pydantic AI adapter.

    from call4agents.adapters import pydantic_ai as c4a_pai

    @agent.tool                      # Pydantic AI's decorator goes on top
    @c4a_pai.effect                  # once per conversation, tool and exact arguments
    def send_email(ctx: RunContext[Deps], to: str, body: str) -> str: ...

The run id is the run's `conversation_id`, which Pydantic AI keeps when you resume with the saved
message history (or pass conversation_id= yourself). `ctx` is left out of the key. An active
`with c4a.run(...)` wins over the conversation id.
"""

from typing import Any, Callable, Dict, Optional

from call4agents.adapters._common import wrap_tool


def _conversation(ctx: Any) -> Optional[str]:
    return getattr(ctx, "conversation_id", None)


def effect(fn: Optional[Callable] = None, **options: Any):
    """c4a.effect for a Pydantic AI tool (with or without a RunContext first parameter)."""
    def wrap(f: Callable) -> Callable:
        return wrap_tool(f, ("RunContext",), _conversation, "Pydantic AI", dict(options))
    return wrap(fn) if fn is not None else wrap


def tickets(result: Any, tools: Any, *, ttl: float = 86400.0) -> Dict[str, str]:
    """Experimental. For a run that ended with DeferredToolRequests: one approval ticket per waiting call,
    {tool_call_id: ticket}. Show each ticket with its call; send the person's decisions to results().
    `tools`: the agent's tools wrapped with c4a_pai.effect(approval=True)."""
    import call4agents as c4a
    by_name = {getattr(t, "c4a_fn", t).__name__: t for t in tools}
    run_id = c4a._RUN.get() or getattr(result, "conversation_id", None)
    if not run_id:
        raise RuntimeError("no run id: wrap this in `with c4a.run(<id>):` or pass the AgentRunResult")
    out = {}
    for call in result.output.approvals:
        tool = by_name.get(call.tool_name)
        if tool is None:
            raise ValueError(f"{call.tool_name!r} is not in tools=: pass the tools wrapped with c4a_pai.effect")
        name, args = c4a._tool_and_args(tool, call.args_as_dict())
        out[call.tool_call_id] = c4a.current_ledger().issue_ticket(str(run_id), name, args, ttl,
                                                                   extra={"call_id": call.tool_call_id})
    return out


def results(decisions: Dict[str, str], *, by: str = "") -> Any:
    """Experimental. {ticket: "approve" | "deny"} -> DeferredToolResults for the next run. The tool call ids
    come from THIS record, not from the client, and each ticket is decided once (c4a.decide)."""
    import call4agents as c4a
    from pydantic_ai import DeferredToolResults
    approvals = {}
    for ticket, decision in decisions.items():
        info = c4a.decide(ticket, decision, by=by)
        approvals[info["call_id"]] = decision == "approve"
    return DeferredToolResults(approvals=approvals)
