# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Pydantic AI + Call4Agents, runnable offline (a fake model, no API key):

    pip install call4agents pydantic-ai
    python -m call4agents.examples.pydantic_ai_tool

The agent emails bob. The run is then repeated with the same conversation_id, the way an app
resumes after a crash or a retry. The email is sent once.
"""
import os
import tempfile
from pathlib import Path


def main(record: str = "", mode: str = "adapter") -> dict:
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    from pydantic_ai import Agent, RunContext
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, UserPromptPart
    from pydantic_ai.models.function import FunctionModel

    import call4agents as c4a
    from call4agents.adapters import pydantic_ai as c4a_pai

    c4a.init(record or str(Path(tempfile.mkdtemp()) / "ledger.sqlite"))
    outbox = []

    def model(messages, info):                # calls the tool once per user prompt, then answers
        if any(isinstance(p, UserPromptPart) for p in messages[-1].parts):
            return ModelResponse(parts=[ToolCallPart("send_email", {"to": "bob", "body": "hi"})])
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(FunctionModel(model))

    def send_email(ctx: RunContext[None], to: str, body: str) -> str:
        outbox.append(to)
        return f"sent to {to}"
    # mode: "adapter" (the way to use it), "none" (no kit: the control), "plain" (c4a.effect without the adapter)
    if mode == "adapter":
        send_email = c4a_pai.effect(send_email)   # once per conversation, tool and exact arguments
    elif mode == "plain":
        send_email = c4a.effect(send_email)
    agent.tool(send_email)

    agent.run_sync("email bob", conversation_id="order-42")
    agent.run_sync("email bob", conversation_id="order-42")      # the retry / resume
    return {"emails_sent": len(outbox)}


if __name__ == "__main__":
    print(main())
