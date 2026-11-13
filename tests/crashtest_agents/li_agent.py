"""A small real LlamaIndex FunctionAgent with LlamaIndex's own mock model, for `call4agents crashtest`.
AGENT_GUARD=1 adds the one-line fix. The app saves the Context before each tool call and resumes from it
after a restart; LlamaIndex gives the resumed run a new run id, so the job id comes from `with c4a.run(...)`."""
import asyncio
import json
import os
from pathlib import Path

from llama_index.core.agent.workflow import FunctionAgent, ToolCall
from llama_index.core.base.llms.types import ChatMessage, MessageRole, ToolCallBlock
from llama_index.core.llms.mock import MockFunctionCallingLLM
from llama_index.core.tools import FunctionTool
from llama_index.core.workflow import Context

import call4agents as c4a
from call4agents.adapters import llamaindex as c4a_li

c4a.init("record.sqlite", secret=b"crashtest-agent-0")
saved = Path("context.json")


def model(messages, **kwargs):
    if any(m.role == MessageRole.TOOL for m in messages):
        return ChatMessage(role=MessageRole.ASSISTANT, content="done")
    return ChatMessage(role=MessageRole.ASSISTANT, blocks=[
        ToolCallBlock(tool_call_id="call-1", tool_name="send_email", tool_kwargs={"to": "bob"})])


def send_email(to: str) -> str:
    """Send an email."""
    with open("outbox.txt", "a") as f:
        f.write(f"email to {to}\n")
    return "sent"


guard = c4a_li.effect if os.environ.get("AGENT_GUARD") == "1" else (lambda f: f)
agent = FunctionAgent(tools=[FunctionTool.from_defaults(guard(send_email), name="send_email")],
                      llm=MockFunctionCallingLLM(response_generator=model))


async def go():
    if saved.exists():
        handler = agent.run(ctx=Context.from_dict(agent, json.loads(saved.read_text())))   # the resume
    else:
        handler = agent.run("email bob")
    async for event in handler.stream_events():
        if isinstance(event, ToolCall):
            saved.write_text(json.dumps(handler.ctx.to_dict()))                           # checkpoint before the action
    await handler


if not Path("done.txt").exists():                    # the job finished before this start
    with c4a.run("job-1"):
        asyncio.run(go())
    Path("done.txt").write_text("done\n")
