# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""CrewAI adapter.

    from crewai.tools import tool
    from call4agents.adapters import crewai as c4a_crew

    @tool("send_email")              # CrewAI's decorator goes on top
    @c4a_crew.effect                 # once per crew run, tool and exact arguments
    def send_email(to: str, body: str) -> str:
        "Send an email."

The run id is the crew's `id`. CrewAI keeps it when it retries a failed task (the agent's
max_retry_limit) and when you resume with Crew.from_checkpoint(...) or kickoff(from_checkpoint=...).
A new Crew(...) gets a new id, and CrewAI does not let you set it: to make a fresh start of the same
job find its effects, wrap kickoff in `with c4a.run("<your job id>"):` or pass run=lambda _: "<id>".
An active `with c4a.run(...)` wins over the crew id. CrewAI gives a function tool no context object,
so every parameter is part of the key. On a BaseTool subclass, put it on `_run` (`self` is left out).
"""

from typing import Any, Callable, Optional

from call4agents.adapters._common import wrap_plain_tool


def run_id(crew: Any) -> str:
    """The run id the adapter uses for this crew (to approve a call before kickoff)."""
    return f"crewai:{crew.id}"


def _crew() -> Optional[str]:
    from crewai.utilities.crew.crew_context import get_crew_context   # lazy: only when CrewAI is in use
    found = get_crew_context()
    return f"crewai:{found.id}" if found is not None and getattr(found, "id", None) else None


def effect(fn: Optional[Callable] = None, **options: Any):
    """c4a.effect for a CrewAI tool function (or a BaseTool's _run method)."""
    def wrap(f: Callable) -> Callable:
        return wrap_plain_tool(f, _crew, "CrewAI", dict(options))
    return wrap(fn) if fn is not None else wrap


def _wrapped(tool: Any) -> Any:
    """The function this adapter wrapped: inside a Tool from @tool (.func), a BaseTool (._run), or itself."""
    for attr in ("func", "_run"):
        inner = getattr(tool, attr, None)
        if inner is not None and getattr(inner, "c4a_name", None):
            return inner
    return tool


def approve(tool: Any, crew: Any, **args: Any) -> str:
    """Record the person's yes for exactly these arguments, in this crew's run. `tool` may be the
    CrewAI Tool (from @tool), a BaseTool, or the wrapped function."""
    import call4agents as c4a
    with c4a.run(run_id(crew)):
        return c4a.approve(_wrapped(tool), **args)


def deny(tool: Any, crew: Any, **args: Any) -> str:
    """Record the person's no for exactly these arguments, in this crew's run."""
    import call4agents as c4a
    with c4a.run(run_id(crew)):
        return c4a.deny(_wrapped(tool), **args)
