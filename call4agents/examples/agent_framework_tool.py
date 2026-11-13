# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Microsoft Agent Framework + Call4Agents, runnable offline (a scripted chat client, no API key):

    pip install call4agents agent-framework-core
    python -m call4agents.examples.agent_framework_tool

The agent emails bob. The same message is then sent again in the same session, the way an app
retries after a timeout or a crash. Without the kit the email goes out twice; with it, once.
"""
import asyncio
import json
import tempfile
import time
from pathlib import Path

SESSION = "session-1"


def build(tools: list):
    """A real Agent Framework agent with a scripted chat client: it asks for send_email(to="bob",
    body="hi") after each user message, and answers "done" once a tool result is back."""
    from agent_framework import Agent, BaseChatClient, ChatResponse, Content, FunctionInvocationLayer, Message

    class ScriptedClient(FunctionInvocationLayer, BaseChatClient):
        def _inner_get_response(self, *, messages, stream, options, **kwargs):
            async def answer():
                if any(c.type == "function_result" for c in (messages[-1].contents or [])):
                    return ChatResponse(messages=Message(role="assistant", contents=["done"]))
                call = Content.from_function_call(call_id=f"call_{time.time_ns()}", name="send_email",
                                                  arguments=json.dumps({"to": "bob", "body": "hi"}))
                return ChatResponse(messages=Message(role="assistant", contents=[call]))
            if stream:
                raise NotImplementedError("this example uses non-streaming runs")
            return answer()

    agent = Agent(client=ScriptedClient(), name="mailer", instructions="Send the email.", tools=tools)
    return agent, agent.create_session(session_id=SESSION)


def send(agent, session, message):
    """One run in the session: a text, or a list of contents (such as approval responses)."""
    from agent_framework import Message
    if not isinstance(message, str):
        message = Message(role="user", contents=list(message))
    return asyncio.run(agent.run(message, session=session))


def main(record: str = "", mode: str = "adapter") -> dict:
    from agent_framework import FunctionInvocationContext, tool

    import call4agents as c4a
    from call4agents.adapters import agent_framework as c4a_maf

    c4a.init(record or str(Path(tempfile.mkdtemp()) / "ledger.sqlite"))
    outbox = []

    def send_email(to: str, body: str, ctx: FunctionInvocationContext) -> str:
        """Send an email."""
        outbox.append(to)
        return f"sent to {to}"
    # mode: "adapter" (the way to use it), "none" (no kit: the control), "plain" (c4a.effect without the adapter)
    if mode == "adapter":
        send_email = c4a_maf.effect(send_email)   # once per session, tool and exact arguments
    elif mode == "plain":
        send_email = c4a.effect(send_email)

    agent, session = build([tool(send_email)])
    send(agent, session, "email bob")
    send(agent, session, "email bob")              # the retry: same session
    return {"emails_sent": len(outbox)}


if __name__ == "__main__":
    print(main())
