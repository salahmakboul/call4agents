# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""OpenAI Agents SDK + Call4Agents, runnable offline (a fake model, no API key):

    pip install call4agents openai-agents
    python -m call4agents.examples.openai_agents_tool

The agent emails bob. The same session runs again, the way an app retries after a timeout or
resumes after a crash. The email is sent once. The run id comes from the run's context.
"""
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path


def main(record: str = "", mode: str = "adapter") -> dict:
    os.environ.setdefault("OPENAI_API_KEY", "not-used-fake-model")
    from agents import Agent, ModelResponse, RunContextWrapper, Runner, function_tool, set_tracing_disabled
    from agents.models.interface import Model
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    import call4agents as c4a
    from call4agents.adapters import openai_agents as c4a_oa

    set_tracing_disabled(True)
    c4a.init(record or str(Path(tempfile.mkdtemp()) / "ledger.sqlite"))
    outbox = []

    class FakeModel(Model):                   # asks for the tool, then answers once it has run
        async def get_response(self, *a, **k):
            items = k.get("input", a[1] if len(a) > 1 else [])
            items = items if isinstance(items, list) else []
            done = any((i.get("type") if isinstance(i, dict) else getattr(i, "type", "")) == "function_call_output"
                       for i in items)
            if done:
                out = [ResponseOutputMessage(id="m", type="message", role="assistant", status="completed",
                                             content=[ResponseOutputText(type="output_text", text="done", annotations=[])])]
            else:
                out = [ResponseFunctionToolCall(id=f"fc{time.time_ns()}", call_id=f"c{time.time_ns()}",
                                                type="function_call", name="send_email",
                                                arguments=json.dumps({"to": "bob", "body": "hi"}), status="completed")]
            return ModelResponse(output=out, usage=Usage(), response_id=None)

        def stream_response(self, *a, **k):
            raise NotImplementedError

    def send_email(ctx: RunContextWrapper[dict], to: str, body: str) -> str:
        """Send an email."""
        outbox.append(to)
        return f"sent to {to}"
    # mode: "adapter" (the way to use it), "none" (no kit: the control), "plain" (c4a.effect without the adapter)
    if mode == "adapter":
        send_email = c4a_oa.effect(send_email)    # once per session, tool and exact arguments
    elif mode == "plain":
        send_email = c4a.effect(send_email)
    send_email = function_tool(send_email)

    agent = Agent(name="mailer", instructions="email", tools=[send_email], model=FakeModel())
    context = {"session_id": "session-1"}     # read by the adapter as the run id
    asyncio.run(Runner.run(agent, "email bob", context=context, max_turns=4))
    asyncio.run(Runner.run(agent, "email bob", context=context, max_turns=4))   # the retry / resume
    return {"emails_sent": len(outbox)}


if __name__ == "__main__":
    print(main())
