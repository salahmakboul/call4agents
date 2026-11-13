# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""LlamaIndex + Call4Agents, runnable offline (LlamaIndex's own MockFunctionCallingLLM, no API key):

    pip install call4agents llama-index-core
    python -m call4agents.examples.llamaindex_tool

A FunctionAgent emails bob. The app saves the agent's Context each time a tool is about to run (a
checkpoint before each action). The run then dies after the email (here: the next model call fails)
and the app resumes from the saved Context. LlamaIndex re-runs the steps that were in progress, so
without the kit bob gets the email again (measured: twice more, three in all). With the adapter and
`with c4a.run(<job id>)` around both runs he gets one.
"""
import asyncio
import json
import tempfile
from pathlib import Path

MODES = ("none", "plain", "plain_no_ctx", "adapter", "adapter_no_run", "approval_none", "approval_other",
         "approval_exact")


def main(record: str = "", mode: str = "adapter") -> dict:
    from llama_index.core.agent.workflow import FunctionAgent, ToolCall
    from llama_index.core.base.llms.types import ChatMessage, MessageRole, ToolCallBlock
    from llama_index.core.llms.mock import MockFunctionCallingLLM
    from llama_index.core.tools import FunctionTool
    from llama_index.core.workflow import Context

    import call4agents as c4a
    from call4agents.adapters import llamaindex as c4a_li

    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    c4a.init(record or str(Path(tempfile.mkdtemp()) / "ledger.sqlite"))
    outbox, errors, failed = [], [], []

    def model(messages, **kwargs):            # asks for the tool; the call after the tool fails once
        if not any(m.role == MessageRole.TOOL for m in messages):
            return ChatMessage(role=MessageRole.ASSISTANT, blocks=[ToolCallBlock(
                tool_call_id="call-1", tool_name="send_email", tool_kwargs={"to": "bob", "body": "hi"})])
        if not failed:
            failed.append(1)
            raise RuntimeError("provider timeout")
        return ChatMessage(role=MessageRole.ASSISTANT, content="done")

    async def send_email(ctx: Context, to: str, body: str) -> str:
        """Send an email."""
        sender = await ctx.store.get("sender", default="shop")
        outbox.append((sender, to))
        return f"sent to {to}"

    def send_email_no_ctx(to: str, body: str) -> str:
        """Send an email."""
        outbox.append(("shop", to))
        return f"sent to {to}"
    send_email_no_ctx.__name__ = "send_email"
    # mode: "adapter" (the way to use it), "none" (no kit: the control), "plain" (c4a.effect without the
    # adapter), "plain_no_ctx" (c4a.effect on a tool without a Context parameter), "adapter_no_run"
    # (the adapter without c4a.run), "approval_*" (approval=True)
    fn = send_email_no_ctx if mode == "plain_no_ctx" else send_email
    if mode in ("adapter", "adapter_no_run"):
        fn = c4a_li.effect(fn)
    elif mode in ("plain", "plain_no_ctx"):
        fn = c4a.effect(fn)
    elif mode.startswith("approval"):
        fn = c4a_li.effect(approval=True)(fn)
    tool = FunctionTool.from_defaults(_errors_seen(fn, errors), name="send_email")
    agent = FunctionAgent(tools=[tool], llm=MockFunctionCallingLLM(response_generator=model))
    if mode == "approval_other":
        with c4a.run("job-42"):
            c4a.approve(fn, to="eve", body="hi")              # a yes for other arguments
    elif mode == "approval_exact":
        with c4a.run("job-42"):
            c4a.approve(fn, to="bob", body="hi")

    async def job():
        saved = None
        handler = agent.run("email bob")
        try:
            async for event in handler.stream_events():
                if isinstance(event, ToolCall):
                    saved = json.dumps(handler.ctx.to_dict())   # checkpoint before the action
            await handler
        except Exception as exc:  # noqa: BLE001 - the run dies; the app resumes from its checkpoint
            errors.append(f"run: {type(exc).__name__}")
        if saved is not None and failed:
            try:
                await agent.run(ctx=Context.from_dict(agent, json.loads(saved)))   # the resume
            except Exception as exc:  # noqa: BLE001 - reported to the test
                errors.append(f"resume: {type(exc).__name__}")

    if mode == "adapter_no_run":
        asyncio.run(job())
    else:
        with c4a.run("job-42"):                                # the job id, the same for the resume
            asyncio.run(job())
    return {"emails_sent": len(outbox), "tool_errors": errors}


def _errors_seen(f, errors):
    """LlamaIndex turns a tool's exception into text for the model; keep the type so a test can see why."""
    import functools
    import inspect

    if inspect.iscoroutinefunction(f):
        @functools.wraps(f)
        async def acall(*args, **kwargs):
            try:
                return await f(*args, **kwargs)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}"[:200])
                raise
        return acall

    @functools.wraps(f)
    def call(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}"[:200])
            raise
    return call


if __name__ == "__main__":
    print(main())
