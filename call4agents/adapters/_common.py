# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Shared plumbing for the framework adapters: leave the framework's context parameter out of the
key, and take the run id from that context when no `c4a.run(...)` is active."""

import functools
import inspect
from typing import Any, Callable, Optional, Tuple

import call4agents as c4a


def context_param(f: Callable, type_names: Tuple[str, ...]) -> Optional[str]:
    """Name of the parameter that holds the framework's context: the one annotated with one of
    `type_names` (anywhere in the signature), else one named ctx / context / run_context."""
    params = list(inspect.signature(f).parameters.values())
    for p in params:
        ann = p.annotation if isinstance(p.annotation, str) else getattr(p.annotation, "__name__", repr(p.annotation))
        if p.annotation is not inspect.Parameter.empty and any(t in str(ann) for t in type_names):
            return p.name
    for p in params:
        if p.name in ("ctx", "context", "run_context"):
            return p.name
    return None


def wrap_tool(f: Callable, type_names: Tuple[str, ...], run_from: Callable[[Any], Optional[str]],
              what: str, options: dict) -> Callable:
    """c4a.effect(f) with the context excluded from the key; the run id comes from an active
    c4a.run(...), else from run_from(ctx), else a clear error."""
    ctx_name = context_param(f, type_names)
    run_override = options.pop("run", None)
    inner = c4a.effect(f, exclude=(ctx_name,) if ctx_name else (), **options)

    def find_run(args: tuple, kwargs: dict) -> str:
        active = c4a._RUN.get()
        if active is not None:
            return active
        ctx = None
        if ctx_name:
            try:
                ctx = inspect.signature(f).bind_partial(*args, **kwargs).arguments.get(ctx_name)
            except TypeError:
                ctx = None
        rid = (run_override or run_from)(ctx) if (ctx is not None or run_override) else None
        if not rid:
            raise RuntimeError(f"call4agents could not find a run id for this {what} tool call: "
                               f"wrap the run in `with c4a.run(<id>):` or pass run=lambda ctx: <id>")
        return str(rid)

    if inspect.iscoroutinefunction(f):
        @functools.wraps(f)
        async def acall(*args: Any, **kwargs: Any) -> Any:
            with c4a.run(find_run(args, kwargs)):
                return await inner(*args, **kwargs)
        acall.c4a_name, acall.c4a_fn, acall.c4a_exclude = inner.c4a_name, f, inner.c4a_exclude
        return acall

    @functools.wraps(f)
    def call(*args: Any, **kwargs: Any) -> Any:
        with c4a.run(find_run(args, kwargs)):
            return inner(*args, **kwargs)
    call.c4a_name, call.c4a_fn, call.c4a_exclude = inner.c4a_name, f, inner.c4a_exclude
    return call
