"""A small real OpenAI Agents SDK agent with a fake model, for `call4agents crashtest`. AGENT_GUARD=1 adds the one-line fix."""
import asyncio
import json
import os
import time

os.environ.setdefault("OPENAI_API_KEY", "not-used-fake-model")
from agents import Agent, ModelResponse, Runner, SQLiteSession, function_tool, set_tracing_disabled
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

import call4agents as c4a
from call4agents.adapters import openai_agents as c4a_oa

set_tracing_disabled(True)
c4a.init("record.sqlite", secret=b"crashtest-agent-0")


class FakeModel(Model):
    async def get_response(self, *a, **k):
        items = k.get("input", a[1] if len(a) > 1 else [])
        items = items if isinstance(items, list) else []
        done = any((i.get("type") if isinstance(i, dict) else getattr(i, "type", "")) == "function_call_output"
                   for i in items)
        if done:
            out = [ResponseOutputMessage(id="m", type="message", role="assistant", status="completed",
                                         content=[ResponseOutputText(type="output_text", text="done", annotations=[])])]
        else:
            out = [ResponseFunctionToolCall(id=f"fc{time.time_ns()}", call_id=f"c{time.time_ns()}", type="function_call",
                                            name="send_email", arguments=json.dumps({"to": "bob"}), status="completed")]
        return ModelResponse(output=out, usage=Usage(), response_id=None)

    def stream_response(self, *a, **k):
        raise NotImplementedError


def send_email(to: str) -> str:
    """Send an email."""
    with open("outbox.txt", "a") as f:
        f.write(f"email to {to}\n")
    return "sent"


tool = function_tool(c4a_oa.effect(send_email) if os.environ.get("AGENT_GUARD") == "1" else send_email)


async def go():
    session = SQLiteSession("session-1", "session.db")
    items = await session.get_items()
    if items and items[-1].get("role") == "assistant":
        return                                       # the job finished before this start
    agent = Agent(name="mailer", instructions="email", tools=[tool], model=FakeModel())
    await Runner.run(agent, "email bob", session=session, max_turns=6)


with c4a.run("session-1"):
    asyncio.run(go())
