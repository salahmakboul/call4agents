# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Microsoft Agent Framework (Python) adapter.

    from call4agents.adapters import agent_framework as c4a_maf

    @tool
    @c4a_maf.effect                  # once per session, tool and exact arguments
    def send_email(to: str, body: str, ctx: FunctionInvocationContext) -> str: ...

    session = agent.create_session(session_id="order-42")
    await agent.run("...", session=session)

The run id is the AgentSession's `session_id`: it is kept when the session is saved and loaded again
(to_dict / from_dict) and in workflow checkpoints, so a retried or restored run finds its effects. The
FunctionInvocationContext parameter is left out of the key. An active `with c4a.run(...)` wins.
Without session=, Agent Framework may make a new session for each run: then each run is a new run id.

Tool approval (`@tool(approval_mode="always_require")`): wrap the tool with
`c4a_maf.effect(approval=True)` and turn the person's answer into the response with `respond(...)`. It
records the yes (or no) in call4agents for exactly the arguments in the approval request, so a call
changed after the yes is refused when it runs.
"""

import inspect
import typing
from typing import Any, Callable, Dict, Optional

import call4agents as c4a
from call4agents.adapters._common import wrap_tool


def run_id(session: Any) -> str:
    """The run id for an AgentSession (or a FunctionInvocationContext). A string is used as it is."""
    if isinstance(session, str):
        return session
    session = getattr(session, "session", None) or session
    return str(session.session_id)


def _from_context(ctx: Any) -> Optional[str]:
    session = getattr(ctx, "session", None)
    return run_id(session) if getattr(session, "session_id", None) else None


def _context_name(f: Callable) -> Optional[str]:
    """The parameter Agent Framework fills with the FunctionInvocationContext (found by its type)."""
    try:
        hints = typing.get_type_hints(f)
    except Exception:  # noqa: BLE001 - unresolved names: fall back to the raw annotations, as the framework does
        hints = {}
    for name, p in inspect.signature(f).parameters.items():
        ann = hints.get(name, p.annotation)
        if any(getattr(a, "__name__", a) == "FunctionInvocationContext" for a in (typing.get_args(ann) or (ann,))):
            return name
    return None


def effect(fn: Optional[Callable] = None, **options: Any):
    """c4a.effect for an Agent Framework function tool (with or without a FunctionInvocationContext)."""
    def wrap(f: Callable) -> Callable:
        opts = dict(options)
        # only the parameter the framework fills is left out: a model argument named `ctx` stays in the key
        guarded = wrap_tool(f, (), _from_context, "Agent Framework", opts,
                            _context_name(f))
        guarded.c4a_needs_yes = bool(options.get("approval"))   # respond() records a yes only for these
        return guarded
    return wrap(fn) if fn is not None else wrap


def _tools_by_name(tools: Any) -> Dict[str, Any]:
    out = {}
    for t in tools:
        fn = getattr(t, "func", None) or t   # a FunctionTool from @tool, or the wrapped function itself
        out[getattr(t, "name", None) or getattr(fn, "__name__", "")] = fn
    return out


def respond(request: Any, approved: bool, *, tools: Any, session: Any = None) -> Any:
    """The person's answer to a function_approval_request, as the response Content to send back.
    First records c4a.approve (or c4a.deny) for exactly the arguments in that request, in this session.
    `tools`: the agent's tools, wrapped with c4a_maf.effect(approval=True)."""
    if getattr(request, "type", None) != "function_approval_request":
        raise ValueError("respond() takes a function_approval_request from result.user_input_requests")
    call = request.function_call
    tool = _tools_by_name(tools).get(call.name)
    if tool is None:
        raise ValueError(f"{call.name!r} is not in tools=: pass the tools wrapped with c4a_maf.effect")
    if not getattr(tool, "c4a_needs_yes", False):
        raise ValueError(f"{call.name!r} must be wrapped with c4a_maf.effect(approval=True): "
                         "otherwise the yes is not bound to the arguments")
    rid = c4a._RUN.get() or (run_id(session) if session is not None else None)
    if not rid:
        raise RuntimeError("no run id: pass session= (the AgentSession) or wrap this in `with c4a.run(<id>):`")
    with c4a.run(rid):
        (c4a.approve if approved else c4a.deny)(tool, **dict(call.parse_arguments() or {}))
    return request.to_function_approval_response(approved=bool(approved))
