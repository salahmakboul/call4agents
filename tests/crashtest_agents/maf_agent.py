"""A small real Microsoft Agent Framework agent with a scripted chat client, for `call4agents crashtest`.
AGENT_GUARD=1 adds the one-line fix.

The session is saved to session.json in the current folder after each run (to_dict / from_dict). On a start that
finds the job unfinished, the app sends the user's message again in the same session."""
import asyncio
import json
import os
import time
from pathlib import Path

from agent_framework import (Agent, AgentSession, BaseChatClient, ChatResponse, Content, FunctionInvocationContext,
                             FunctionInvocationLayer, Message, tool)

import call4agents as c4a
from call4agents.adapters import agent_framework as c4a_maf

c4a.init("record.sqlite", secret=b"crashtest-agent-0")
store = Path("session.json")


class ScriptedClient(FunctionInvocationLayer, BaseChatClient):
    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        async def answer():
            if any(c.type == "function_result" for c in (messages[-1].contents or [])):
                return ChatResponse(messages=Message(role="assistant", contents=["done"]))
            call = Content.from_function_call(call_id=f"call_{time.time_ns()}", name="send_email",
                                              arguments=json.dumps({"to": "bob"}))
            return ChatResponse(messages=Message(role="assistant", contents=[call]))
        return answer()


guard = c4a_maf.effect if os.environ.get("AGENT_GUARD") == "1" else (lambda f: f)


@tool
@guard
def send_email(to: str, ctx: FunctionInvocationContext) -> str:
    """Send an email."""
    with open("outbox.txt", "a") as f:
        f.write(f"email to {to}\n")
    return "sent"


async def go():
    agent = Agent(client=ScriptedClient(), name="mailer", instructions="Send the email.", tools=[send_email])
    if store.exists():
        session = AgentSession.from_dict(json.loads(store.read_text()))
        if "done" in json.dumps(session.to_dict()):
            return                                   # the job finished before this start
    else:
        session = agent.create_session(session_id="session-1")
    await agent.run("email bob", session=session)
    store.write_text(json.dumps(session.to_dict()))


asyncio.run(go())
