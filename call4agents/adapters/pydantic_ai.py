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

from typing import Any, Callable, Optional

from call4agents.adapters._common import wrap_tool


def _conversation(ctx: Any) -> Optional[str]:
    return getattr(ctx, "conversation_id", None)


def effect(fn: Optional[Callable] = None, **options: Any):
    """c4a.effect for a Pydantic AI tool (with or without a RunContext first parameter)."""
    def wrap(f: Callable) -> Callable:
        return wrap_tool(f, ("RunContext",), _conversation, "Pydantic AI", dict(options))
    return wrap(fn) if fn is not None else wrap
