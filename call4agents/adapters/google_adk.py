# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Google ADK adapter.

    from call4agents.adapters import google_adk as c4a_adk

    @c4a_adk.effect                  # once per session, tool and exact arguments
    def send_email(to: str, body: str, tool_context: ToolContext) -> str: ...

    agent = LlmAgent(..., tools=[send_email])

The run id is the ADK session (app name, user id, session id). A resumed invocation, and a message sent
again after a timeout, run in the same session; the invocation id is new for each new message, so it
would let a re-sent message act again. The tool context is left out of the key. An active
`with c4a.run(...)` wins over the session.

ADK tool confirmation (`FunctionTool(..., require_confirmation=True)`): wrap the tool with
`c4a_adk.effect(approval=True)` and build the person's answer with `confirm(...)`. It records the yes
(or no) in call4agents for exactly the arguments shown in the `adk_request_confirmation` call, so a
call changed after the yes is refused when it runs.
"""

import inspect
import json
from typing import Any, Callable, Dict, Optional

import call4agents as c4a
from call4agents.adapters._common import wrap_tool

REQUEST = "adk_request_confirmation"


def run_id(session: Any) -> str:
    """The run id for an ADK session (a Session, or a tool / callback context). A string is used as it is."""
    if isinstance(session, str):
        return session
    session = getattr(session, "session", session)
    return "adk:" + json.dumps([session.app_name, session.user_id, session.id], separators=(",", ":"))


def _from_context(ctx: Any) -> Optional[str]:
    session = getattr(ctx, "session", None)
    return run_id(session) if getattr(session, "id", None) else None


def _context_name(f: Callable) -> Optional[str]:
    """The parameter ADK fills with the tool context, found the way FunctionTool finds it."""
    from google.adk.utils.context_utils import find_context_parameter
    found = find_context_parameter(f)
    return found or ("tool_context" if "tool_context" in inspect.signature(f).parameters else None)


def effect(fn: Optional[Callable] = None, **options: Any):
    """c4a.effect for an ADK function tool (with or without a ToolContext parameter)."""
    def wrap(f: Callable) -> Callable:
        opts = dict(options)
        # only the parameter ADK fills is left out: a model argument named `context` stays in the key
        guarded = wrap_tool(f, (), _from_context, "Google ADK", opts, _context_name(f))
        guarded.c4a_needs_yes = bool(options.get("approval"))   # confirm() records a yes only for these
        return guarded
    return wrap(fn) if fn is not None else wrap


def _tools_by_name(tools: Any) -> Dict[str, Any]:
    out = {}
    for t in tools:
        fn = getattr(t, "func", t)           # an ADK FunctionTool, or the wrapped function itself
        out[getattr(t, "name", None) or getattr(fn, "__name__", "")] = fn
    return out


def confirm(call: Any, confirmed: bool, *, tools: Any, session: Any = None) -> Any:
    """The person's answer to an `adk_request_confirmation` call, as the Content to send back to ADK.
    First records c4a.approve (or c4a.deny) for exactly the arguments in that call, in this session.
    `tools`: the agent's tools, wrapped with c4a_adk.effect(approval=True)."""
    from google.genai import types
    if getattr(call, "name", None) != REQUEST:
        raise ValueError(f"confirm() takes the {REQUEST!r} function call ADK asked with")
    original = (call.args or {}).get("originalFunctionCall") or {}
    tool = _tools_by_name(tools).get(original.get("name"))
    if tool is None:
        raise ValueError(f"{original.get('name')!r} is not in tools=: pass the tools wrapped with c4a_adk.effect")
    if not getattr(tool, "c4a_needs_yes", False):
        raise ValueError(f"{original.get('name')!r} must be wrapped with c4a_adk.effect(approval=True): "
                         "otherwise the yes is not bound to the arguments")
    rid = c4a._RUN.get() or (run_id(session) if session is not None else None)
    if not rid:
        raise RuntimeError("no run id: pass session= (the ADK session) or wrap this in `with c4a.run(<id>):`")
    with c4a.run(rid):
        (c4a.approve if confirmed else c4a.deny)(tool, **dict(original.get("args") or {}))
    return types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
        id=call.id, name=REQUEST, response={"confirmed": bool(confirmed)}))])
