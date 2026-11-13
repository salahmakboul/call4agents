# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Google ADK + Call4Agents, runnable offline (a scripted model, no API key, no network):

    pip install call4agents google-adk
    python -m call4agents.examples.google_adk_tool

The agent emails bob. The same message is then sent again in the same session, the way an app
retries after a timeout or a crash. Without the kit the email goes out twice; with it, once.
"""
import asyncio
import tempfile
from pathlib import Path

APP, USER, SESSION = "mailer-app", "user-1", "session-1"


def build(tools: list):
    """A real ADK agent and runner with a scripted model: it asks for send_email(to="bob", body="hi")
    after each user text, and answers "done" once a tool result is back."""
    from google.adk.agents import LlmAgent
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    class ScriptedModel(BaseLlm):
        model: str = "scripted"

        async def generate_content_async(self, llm_request, stream=False):
            last = llm_request.contents[-1]
            if any(p.function_response for p in (last.parts or [])):
                part = types.Part(text="done")
            else:
                part = types.Part(function_call=types.FunctionCall(name="send_email", args={"to": "bob", "body": "hi"}))
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    agent = LlmAgent(name="mailer", model=ScriptedModel(), instruction="Send the email.", tools=tools)
    service = InMemorySessionService()
    asyncio.run(service.create_session(app_name=APP, user_id=USER, session_id=SESSION))
    return Runner(app_name=APP, agent=agent, session_service=service), service


def send(runner, message) -> list:
    """One run in the session: a text, or a Content (such as a confirmation answer). Returns the events."""
    from google.genai import types
    if isinstance(message, str):
        message = types.Content(role="user", parts=[types.Part(text=message)])

    async def go():
        return [e async for e in runner.run_async(user_id=USER, session_id=SESSION, new_message=message)]
    return asyncio.run(go())


def main(record: str = "", mode: str = "adapter") -> dict:
    from google.adk.tools import ToolContext

    import call4agents as c4a
    from call4agents.adapters import google_adk as c4a_adk

    c4a.init(record or str(Path(tempfile.mkdtemp()) / "ledger.sqlite"))
    outbox = []

    def send_email(to: str, body: str, tool_context: ToolContext) -> str:
        """Send an email."""
        outbox.append(to)
        return f"sent to {to}"
    # mode: "adapter" (the way to use it), "none" (no kit: the control), "plain" (c4a.effect without the adapter)
    if mode == "adapter":
        send_email = c4a_adk.effect(send_email)   # once per session, tool and exact arguments
    elif mode == "plain":
        send_email = c4a.effect(send_email)

    runner, _ = build([send_email])
    send(runner, "email bob")
    send(runner, "email bob")                      # the retry: same session, a new invocation
    return {"emails_sent": len(outbox)}


if __name__ == "__main__":
    print(main())
