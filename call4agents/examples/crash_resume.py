# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Crash-and-resume example inside real frameworks, with a FAKE model (no keys, no network).

A tool sends an email; the process dies right after the tool returned, before the framework
saved its checkpoint; a new process resumes the way each framework documents. We count the
emails with and without call4agents. Used by `python -m call4agents quickstart`.

    python -m call4agents.examples.crash_resume <framework> <workdir> <use_kit 0|1> <crash|resume>
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path


def make_send(work: Path, use_kit: bool, run_id: str):
    """The side effect: one line in outbox.txt. With the kit: once per (run, tool, arguments)."""
    def send_email(to: str) -> str:
        with open(work / "outbox.txt", "a") as f:
            f.write(f"email to {to}\n")
        return "sent"
    if not use_kit:
        return send_email
    import call4agents as c4a
    c4a.init(str(work / ".c4a" / "ledger.sqlite"))
    once = c4a.effect(send_email)

    def send(to: str) -> str:
        with c4a.run(run_id):
            return once(to=to)
    return send


def crash_if_asked():
    if os.environ.get("C4A_EXAMPLE_CRASH") == "1":
        os._exit(17)          # after the email, before the framework saves its checkpoint


def langgraph(work: Path, use_kit: bool, resume: bool) -> None:
    import sqlite3
    from typing import TypedDict
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, START, StateGraph

    send = make_send(work, use_kit, "thread-1")

    class S(TypedDict, total=False):
        n: int

    def send_node(s):
        send("bob")
        crash_if_asked()
        return {"n": 2}
    g = StateGraph(S)
    g.add_node("plan", lambda s: {"n": 1})
    g.add_node("send", send_node)
    g.add_edge(START, "plan")
    g.add_edge("plan", "send")
    g.add_edge("send", END)
    graph = g.compile(checkpointer=SqliteSaver(sqlite3.connect(work / "lg.db", check_same_thread=False)))
    graph.invoke(None if resume else {}, {"configurable": {"thread_id": "thread-1"}})


def pydantic_ai(work: Path, use_kit: bool, resume: bool) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    send = make_send(work, use_kit, "conversation-1")
    store = work / "history.json"

    def model(messages, info):
        if any(getattr(p, "part_kind", "") == "tool-return" for p in messages[-1].parts):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart("send_email", {"to": "bob"}, tool_call_id=f"c{time.time_ns()}")])
    agent = Agent(FunctionModel(model))

    @agent.tool_plain
    def send_email(to: str) -> str:
        out = send(to)
        crash_if_asked()
        return out

    async def go():
        history = ModelMessagesTypeAdapter.validate_json(store.read_bytes()) if resume and store.exists() else None
        async with agent.iter(None if history else "email bob", message_history=history) as run:
            async for _ in run:
                store.write_bytes(ModelMessagesTypeAdapter.dump_json(run.all_messages()))   # app-side checkpoint
    asyncio.run(go())


def openai_agents(work: Path, use_kit: bool, resume: bool) -> None:
    os.environ.setdefault("OPENAI_API_KEY", "not-used-fake-model")
    from agents import Agent, ModelResponse, Runner, SQLiteSession, function_tool, set_tracing_disabled
    from agents.models.interface import Model
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
    set_tracing_disabled(True)
    send = make_send(work, use_kit, "session-1")

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
                out = [ResponseFunctionToolCall(id=f"fc{time.time_ns()}", call_id=f"c{time.time_ns()}",
                                                type="function_call", name="send_email",
                                                arguments=json.dumps({"to": "bob"}), status="completed")]
            return ModelResponse(output=out, usage=Usage(), response_id=None)

        def stream_response(self, *a, **k):
            raise NotImplementedError

    @function_tool
    def send_email(to: str) -> str:
        """Send an email."""
        out = send(to)
        crash_if_asked()
        return out
    agent = Agent(name="mailer", instructions="email", tools=[send_email], model=FakeModel())
    session = SQLiteSession("session-1", str(work / "session.db"))
    asyncio.run(Runner.run(agent, "email bob", session=session, max_turns=6))


FRAMEWORKS = {"langgraph": langgraph, "pydantic_ai": pydantic_ai, "openai_agents": openai_agents}
MODULES = {"langgraph": "langgraph.checkpoint.sqlite", "pydantic_ai": "pydantic_ai", "openai_agents": "agents.models.interface"}

if __name__ == "__main__":
    name, work, use_kit, phase = sys.argv[1], Path(sys.argv[2]), sys.argv[3] == "1", sys.argv[4]
    FRAMEWORKS[name](work, use_kit, phase == "resume")
