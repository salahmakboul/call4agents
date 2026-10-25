# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""OpenAI Agents SDK adapter.

    from call4agents.adapters import openai_agents as c4a_oa

    @function_tool                   # the SDK's decorator goes on top
    @c4a_oa.effect                   # once per run id, tool and exact arguments
    def send_email(ctx: RunContextWrapper[MyContext], to: str, body: str) -> str: ...

    with c4a.run(session_id):        # e.g. your SQLiteSession's id
        await Runner.run(agent, "...", session=session)

The run id comes from an active `with c4a.run(...)`. Without one, it is read from the run's context
object: a string, or a dict / object with `c4a_run_id`, `conversation_id`, `session_id` or
`thread_id`. The ctx parameter is left out of the key.
"""

import json
from typing import Any, Callable, Dict, Optional

from call4agents.adapters._common import wrap_tool

_FIELDS = ("c4a_run_id", "conversation_id", "session_id", "thread_id")


def _from_context(ctx: Any) -> Optional[str]:
    value = getattr(ctx, "context", None)
    if isinstance(value, str):
        return value
    for name in _FIELDS:
        found = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        if found:
            return str(found)
    return None


def effect(fn: Optional[Callable] = None, **options: Any):
    """c4a.effect for an OpenAI Agents SDK function tool (with or without a context first parameter)."""
    def wrap(f: Callable) -> Callable:
        return wrap_tool(f, ("RunContextWrapper", "ToolContext"), _from_context, "OpenAI Agents SDK", dict(options))
    return wrap(fn) if fn is not None else wrap


def tickets(result: Any, tools: Any, *, run_id: Optional[str] = None, ttl: float = 86400.0) -> Dict[str, str]:
    """Experimental. For a run that stopped on approvals (`result.interruptions`): one approval ticket per
    waiting call, {call_id: ticket}. Show each ticket with its call; give the person's answers to apply().
    `tools`: the agent's tools wrapped with c4a_oa.effect(approval=True). The run id is `run_id`, an active
    `with c4a.run(...)`, or the run's context (as for effect)."""
    import call4agents as c4a
    by_name = {getattr(t, "c4a_fn", t).__name__: t for t in tools}
    rid = run_id or c4a._RUN.get() or _from_context(getattr(result, "context_wrapper", None))
    if not rid:
        raise RuntimeError("no run id: pass run_id=, wrap this in `with c4a.run(<id>):`, or put one in the run context")
    out = {}
    for item in result.interruptions:
        tool = by_name.get(item.tool_name)
        if tool is None:
            raise ValueError(f"{item.tool_name!r} is not in tools=: pass the tools wrapped with c4a_oa.effect")
        name, args = c4a._tool_and_args(tool, json.loads(item.arguments or "{}"))
        out[item.call_id] = c4a.current_ledger().issue_ticket(str(rid), name, args, ttl, extra={"call_id": item.call_id})
    return out


def apply(state: Any, decisions: Dict[str, str], *, by: str = "") -> Any:
    """Experimental. {ticket: "approve" | "deny"} -> state.approve / state.reject for exactly the waiting call
    each ticket was issued for (the call id comes from THIS record). Returns the state for Runner.run(agent, state)."""
    import call4agents as c4a
    waiting = {item.call_id: item for item in state.get_interruptions()}
    for ticket, decision in decisions.items():
        info = c4a.decide(ticket, decision, by=by)
        item = waiting.get(info["call_id"])
        if item is None:
            raise ValueError(f"the run state has no waiting call {info['call_id']!r} for this ticket")
        if decision == "approve":
            state.approve(item)
        else:
            state.reject(item)
    return state
