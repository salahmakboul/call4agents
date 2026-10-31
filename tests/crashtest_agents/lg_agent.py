"""A small real LangGraph agent for `call4agents crashtest`. AGENT_GUARD=1 adds the one-line fix."""
import os
import sqlite3
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

import call4agents as c4a
from call4agents.adapters import langgraph as c4a_lg

c4a.init("record.sqlite", secret=b"crashtest-agent-0")


def send_email(to: str) -> str:
    with open("outbox.txt", "a") as f:
        f.write(f"email to {to}\n")
    return "sent"


if os.environ.get("AGENT_GUARD") == "1":
    send_email = c4a_lg.effect(send_email)


class S(TypedDict, total=False):
    n: int


def send_node(state):
    send_email("bob")
    return {"n": 2}


g = StateGraph(S)
g.add_node("plan", lambda s: {"n": 1})
g.add_node("send", send_node)
g.add_edge(START, "plan")
g.add_edge("plan", "send")
g.add_edge("send", END)
graph = g.compile(checkpointer=SqliteSaver(sqlite3.connect("graph.db", check_same_thread=False)))
config = {"configurable": {"thread_id": "thread-1"}}
state = graph.get_state(config)
graph.invoke(None if state.next or state.values else {}, config)
