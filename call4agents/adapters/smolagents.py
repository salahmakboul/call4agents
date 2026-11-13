# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""smolagents adapter.

    from smolagents import ToolCallingAgent, tool
    from call4agents.adapters import smolagents as c4a_smol

    @tool                            # smolagents' decorator goes on top
    @c4a_smol.effect                 # once per run id, tool and exact arguments
    def send_email(to: str, body: str) -> str: ...

    with c4a.run("order-42"):        # your job id: the same when the job is retried
        agent.run("email bob")

smolagents has no run, session or conversation id, so the run id comes from an active
`with c4a.run(...)`, else from run=lambda _: <id>; without one the call is refused with a clear
error. A smolagents tool gets no context object, so every parameter is part of the key. On a Tool
subclass, put it on `forward` (`self` is left out).

CodeAgent: by default it runs the model's code in a worker thread (for its time limit), and that
thread does not see `with c4a.run(...)`. Pass run=lambda _: <id>, or build the agent with
executor_kwargs={"timeout_seconds": None}. ToolCallingAgent keeps it (it copies the context into
its tool threads).
"""

from typing import Any, Callable, Optional

from call4agents.adapters._common import wrap_plain_tool

_WHY = ("smolagents (it has no run id of its own; in a CodeAgent the model's code runs in a worker thread "
        "that does not see `with c4a.run`: pass run=, or executor_kwargs={'timeout_seconds': None})")


def _no_run() -> Optional[str]:
    return None


def effect(fn: Optional[Callable] = None, **options: Any):
    """c4a.effect for a smolagents tool function (or a Tool's forward method)."""
    def wrap(f: Callable) -> Callable:
        return wrap_plain_tool(f, _no_run, _WHY, dict(options))
    return wrap(fn) if fn is not None else wrap


def _wrapped(tool: Any) -> Any:
    """The function this adapter wrapped, inside a smolagents Tool (its forward), or the function itself."""
    inner = getattr(tool, "forward", None)
    return inner if inner is not None and getattr(inner, "c4a_name", None) else tool


def approve(tool: Any, **args: Any) -> str:
    """Record the person's yes for exactly these arguments, in the current run (`with c4a.run(...)`).
    `tool` may be the smolagents Tool (from @tool or a Tool subclass) or the wrapped function."""
    import call4agents as c4a
    return c4a.approve(_wrapped(tool), **args)


def deny(tool: Any, **args: Any) -> str:
    """Record the person's no for exactly these arguments, in the current run."""
    import call4agents as c4a
    return c4a.deny(_wrapped(tool), **args)
