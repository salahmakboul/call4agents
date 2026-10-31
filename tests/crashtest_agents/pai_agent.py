"""A small real Pydantic AI agent with a fake model, for `call4agents crashtest`. AGENT_GUARD=1 adds the one-line fix."""
import asyncio
import os
import time
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import call4agents as c4a
from call4agents.adapters import pydantic_ai as c4a_pai

c4a.init("record.sqlite", secret=b"crashtest-agent-0")
store = Path("history.json")


def model(messages, info):
    if any(getattr(p, "part_kind", "") == "tool-return" for p in messages[-1].parts):
        return ModelResponse(parts=[TextPart("done")])
    return ModelResponse(parts=[ToolCallPart("send_email", {"to": "bob"}, tool_call_id=f"c{time.time_ns()}")])


agent = Agent(FunctionModel(model))
guard = c4a_pai.effect if os.environ.get("AGENT_GUARD") == "1" else (lambda f: f)


@agent.tool_plain
@guard
def send_email(to: str) -> str:
    with open("outbox.txt", "a") as f:
        f.write(f"email to {to}\n")
    return "sent"


async def go():
    history = ModelMessagesTypeAdapter.validate_json(store.read_bytes()) if store.exists() else None
    if history and getattr(history[-1].parts[-1], "part_kind", "") == "text":
        return                                       # the job finished before this start
    async with agent.iter(None if history else "email bob", message_history=history) as run:
        async for _ in run:
            store.write_bytes(ModelMessagesTypeAdapter.dump_json(run.all_messages()))


with c4a.run("conversation-1"):
    asyncio.run(go())
