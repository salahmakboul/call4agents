# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""LlamaIndex adapter (llama-index-core agents and workflows).

    from llama_index.core.tools import FunctionTool
    from llama_index.core.workflow import Context
    from call4agents.adapters import llamaindex as c4a_li

    @c4a_li.effect                   # once per run id, tool and exact arguments
    async def send_email(ctx: Context, to: str, body: str) -> str: ...

    tool = FunctionTool.from_defaults(send_email)
    with c4a.run("order-42"):        # your job id: the same when you resume a saved Context
        await agent.run(ctx=Context.from_dict(agent, saved))

The run id comes from an active `with c4a.run(...)`, else from run=lambda ctx: <id>. LlamaIndex has
no id that survives a resume: each run, also one resumed from a saved Context, gets a new workflow
run id, and one process cannot start a run with an id it used before. So this adapter does not fall
back to it; without a run id it raises a clear error. The Context parameter is found the way
LlamaIndex finds it (annotated Context or Context[...], anywhere in the signature) and left out of
the key: LlamaIndex passes a new Context object on each run.
"""

import inspect
from typing import Any, Callable, Optional, get_origin

from call4agents.adapters._common import wrap_plain_tool


def _context_param(f: Callable) -> Optional[str]:
    from llama_index.core.workflow import Context      # lazy: only when a tool is wrapped
    for p in inspect.signature(f).parameters.values():
        if p.annotation is Context or get_origin(p.annotation) is Context:
            return p.name
    return None


def _no_run() -> Optional[str]:
    return None


def effect(fn: Optional[Callable] = None, **options: Any):
    """c4a.effect for a LlamaIndex function tool (sync or async, with or without a Context parameter)."""
    def wrap(f: Callable) -> Callable:
        return wrap_plain_tool(f, _no_run, "LlamaIndex (its run id changes on a resume, so the job id must come "
                               "from you)", dict(options), context=_context_param(f))
    return wrap(fn) if fn is not None else wrap
