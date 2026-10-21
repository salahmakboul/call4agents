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

from typing import Any, Callable, Optional

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
