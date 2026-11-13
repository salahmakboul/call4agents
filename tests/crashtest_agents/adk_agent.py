"""A small real Google ADK agent with a scripted model, for `call4agents crashtest`. AGENT_GUARD=1 adds the one-line fix.

The session lives in SQLite in the current folder. On a start that finds the job unfinished, the app sends the
user's message again in the same session (what a client does after a timeout or a crash)."""
import asyncio
import os

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.adk.tools import ToolContext
from google.genai import types

import call4agents as c4a
from call4agents.adapters import google_adk as c4a_adk

c4a.init("record.sqlite", secret=b"crashtest-agent-0")
APP, USER, SESSION = "mailer", "user-1", "session-1"


class ScriptedModel(BaseLlm):
    model: str = "scripted"

    async def generate_content_async(self, llm_request, stream=False):
        if any(p.function_response for p in (llm_request.contents[-1].parts or [])):
            part = types.Part(text="done")
        else:
            part = types.Part(function_call=types.FunctionCall(name="send_email", args={"to": "bob"}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


guard = c4a_adk.effect if os.environ.get("AGENT_GUARD") == "1" else (lambda f: f)


@guard
def send_email(to: str, tool_context: ToolContext) -> str:
    """Send an email."""
    with open("outbox.txt", "a") as f:
        f.write(f"email to {to}\n")
    return "sent"


async def go():
    service = SqliteSessionService(db_path="sessions.db")
    session = await service.get_session(app_name=APP, user_id=USER, session_id=SESSION)
    if session is None:
        session = await service.create_session(app_name=APP, user_id=USER, session_id=SESSION)
    texts = [p.text for e in session.events if e.author != "user" for p in ((e.content.parts if e.content else None) or [])
             if p.text]
    if texts:
        return                                       # the job finished before this start
    agent = LlmAgent(name="mailer", model=ScriptedModel(), instruction="Send the email.", tools=[send_email])
    runner = Runner(app_name=APP, agent=agent, session_service=service)
    message = types.Content(role="user", parts=[types.Part(text="email bob")])
    async for _ in runner.run_async(user_id=USER, session_id=SESSION, new_message=message):
        pass


asyncio.run(go())
