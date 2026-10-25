# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""LangGraph / LangChain adapter.

The run id is LangGraph's thread id, so a resumed thread finds its own effects and approvals.

    from call4agents.adapters import langgraph as c4a_lg

    @c4a_lg.effect(approval=True)       # inside any node or tool
    def pay(amount: int, to: str): ...

    # when the human says yes to the interrupt, bind the yes to the arguments they SAW:
    c4a_lg.approve(pay, config, amount=10, to="alice")
    graph.invoke(Command(resume=...), config)
"""

import functools
import inspect
from typing import Any, Callable, Dict, Optional

import call4agents as c4a


def thread_id(config: Optional[Dict[str, Any]] = None) -> str:
    if config is None:
        from langgraph.config import get_config   # lazy: only when LangGraph is in use
        config = get_config()
    return str(config["configurable"]["thread_id"])


def effect(fn: Optional[Callable] = None, **options: Any):
    """c4a.effect, with the run id taken from the current LangGraph thread."""
    def wrap(f: Callable) -> Callable:
        inner = c4a.effect(f, **options)
        if inspect.iscoroutinefunction(f):
            @functools.wraps(f)
            async def acall(*args: Any, **kwargs: Any) -> Any:
                with c4a.run(thread_id()):           # await INSIDE the run, or the run id is lost
                    return await inner(*args, **kwargs)
            acall.c4a_name, acall.c4a_fn = inner.c4a_name, f
            return acall

        @functools.wraps(f)
        def call(*args: Any, **kwargs: Any) -> Any:
            with c4a.run(thread_id()):
                return inner(*args, **kwargs)
        call.c4a_name, call.c4a_fn = inner.c4a_name, f
        return call
    return wrap(fn) if fn is not None else wrap


def approve(tool: Any, config: Dict[str, Any], **args: Any) -> str:
    """Record the human's yes for exactly these arguments, in this thread."""
    with c4a.run(thread_id(config)):
        return c4a.approve(tool, **args)


def deny(tool: Any, config: Dict[str, Any], **args: Any) -> str:
    """Record the human's no for exactly these arguments, in this thread (cancels an earlier yes)."""
    with c4a.run(thread_id(config)):
        return c4a.deny(tool, **args)


def pause(tool: Any, config: Optional[Dict[str, Any]] = None, *, ttl: float = 86400.0, **args: Any) -> str:
    """Experimental. An approval ticket for exactly this call, in this thread. Put it in the interrupt
    payload; the person's answer goes to c4a.decide(ticket, "approve" | "deny") before you resume. When the
    node runs again on resume, the same undecided ticket comes back, so one decision answers it.

        def ask(state, config):
            ticket = c4a_lg.pause(pay, config, **state["call"])
            interrupt({"call": state["call"], "ticket": ticket})
    """
    with c4a.run(thread_id(config)):
        return c4a.pause(tool, ttl=ttl, **args)
