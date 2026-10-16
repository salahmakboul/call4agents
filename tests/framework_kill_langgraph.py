"""Optional suite: the LangGraph kill tests WITH the call4agents adapter,
inside the repository. Skips (exit 0) when langgraph / langchain are not installed.
Run in an environment that has them:  python -m tests.framework_kill_langgraph
  A2 crash : the effect, then the process dies before the checkpoint; resume in a new process
  A2 retry : the effect, then a timeout; RetryPolicy(retry_on=TimeoutError) runs the node again
  A1 one-node : the model is asked again on resume (approved 10, the model now says 12)
  A6 HITL middleware : LangChain HumanInTheLoopMiddleware, saved state edited to 9999 -> mallory
Run from the repository root:  python -m tests.framework_kill_langgraph"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

import tempfile
ROOT = Path(os.environ.get("C4A_ROOT", Path(__file__).resolve().parents[1]))
W = Path(os.environ.get("C4A_FK_WORK") or tempfile.mkdtemp(prefix="c4a-fk-"))
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
sys.path.insert(0, os.environ.get("C4A_ROOT", str(Path(__file__).resolve().parents[1])))   # C4A_ROOT: mutation copies
try:
    import langgraph  # noqa: F401
    import langchain  # noqa: F401
except ImportError:
    if __name__ == "__main__":
        print("SKIPPED: langgraph / langchain not installed (optional framework suite)")
        sys.exit(0)
    raise
import call4agents as c4a  # noqa: E402
from call4agents.adapters import langgraph as c4a_lg  # noqa: E402


def outbox(name):
    p = W / f"{name}.log"
    return len(p.read_text().splitlines()) if p.exists() else 0


def a2(mode):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import RetryPolicy
    c4a.init(str(W / f"a2_{mode}.sqlite"))
    tries = {"k": 0}

    @c4a_lg.effect
    def send_email(to: str) -> str:
        with open(W / f"a2_{mode}.log", "a") as f:
            f.write("sent\n")
        return "sent"

    class S(TypedDict, total=False):
        n: int

    def node(s):
        send_email("bob")
        if mode == "crash" and os.environ.get("CRASH") == "1":
            os._exit(17)                      # after the effect, before LangGraph's checkpoint
        if mode == "retry":
            tries["k"] += 1
            if tries["k"] == 1:
                raise TimeoutError("timed out after the effect")
        return {"n": 1}
    g = StateGraph(S)
    g.add_node("plan", lambda s: {"n": 0})
    g.add_node("send", node, retry_policy=RetryPolicy(max_attempts=3, initial_interval=0.01, retry_on=TimeoutError))
    g.add_edge(START, "plan")
    g.add_edge("plan", "send")
    g.add_edge("send", END)
    graph = g.compile(checkpointer=SqliteSaver(sqlite3.connect(W / f"a2_{mode}_ckpt.db", check_same_thread=False)))
    cfg = {"configurable": {"thread_id": f"a2-{mode}"}}
    graph.invoke(None if os.environ.get("RESUME") == "1" else {}, cfg)


def a1_one_node(phase):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
    c4a.init(str(W / "a1.sqlite"))
    amount = 10 if phase == "1" else 12      # the model answers differently after the restart

    @c4a_lg.effect(approval=True)
    def pay(amount: int, to: str) -> str:
        with open(W / "a1.log", "a") as f:
            f.write(f"{amount}->{to}\n")
        return "paid"

    class S(TypedDict, total=False):
        x: int

    def step(s):
        args = {"amount": amount, "to": "alice"}
        if interrupt({"approve": args}) == "yes":
            try:
                pay(**args)
            except c4a.ApprovalRequired:
                with open(W / "a1_refused.log", "a") as f:
                    f.write(json.dumps(args) + "\n")
        return {}
    g = StateGraph(S)
    g.add_node("step", step)
    g.add_edge(START, "step")
    g.add_edge("step", END)
    graph = g.compile(checkpointer=SqliteSaver(sqlite3.connect(W / "a1_ckpt.db", check_same_thread=False)))
    cfg = {"configurable": {"thread_id": "a1"}}
    if phase == "1":
        out = graph.invoke({}, cfg)
        shown = out["__interrupt__"][0].value["approve"]
        c4a_lg.approve(pay, cfg, **shown)    # the human's yes, bound to what they SAW
    else:
        graph.invoke(Command(resume="yes"), cfg)


def a6_hitl():
    from langchain.agents import create_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    c4a.init(str(W / "a6.sqlite"))
    paid, refused = [], []

    @c4a_lg.effect(approval=True)
    def pay_effect(amount: int, to: str) -> str:
        paid.append((amount, to))
        return "paid"

    def pay(amount: int, to: str) -> str:
        """Pay someone."""
        try:
            return pay_effect(amount=amount, to=to)
        except c4a.ApprovalRequired:
            refused.append((amount, to))
            return "refused: not the approved arguments"

    class Fake(GenericFakeChatModel):
        def bind_tools(self, tools, **kw):
            return self
    model = Fake(messages=iter([AIMessage(content="", tool_calls=[{"name": "pay", "args": {"amount": 10, "to": "alice"},
                                                                    "id": "c1"}]), AIMessage(content="done")]))
    agent = create_agent(model, tools=[pay], checkpointer=InMemorySaver(),
                         middleware=[HumanInTheLoopMiddleware(interrupt_on={"pay": True})])
    cfg = {"configurable": {"thread_id": "a6"}}
    out = agent.invoke({"messages": [{"role": "user", "content": "pay"}]}, cfg)
    shown = out["__interrupt__"][0].value["action_requests"][0]["args"]
    c4a_lg.approve(pay_effect, cfg, **shown)
    last = agent.get_state(cfg).values["messages"][-1]
    agent.update_state(cfg, {"messages": [AIMessage(content="", id=last.id, tool_calls=[
        {"name": "pay", "args": {"amount": 9999, "to": "mallory"}, "id": "c1"}])]})
    agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), cfg)
    return {"paid": paid, "refused": refused}


def run(*args, env=None):
    return subprocess.run([sys.executable, __file__, *args], capture_output=True, text=True,
                          env={**os.environ, "C4A_FK_WORK": str(W), "PYTHONPATH": str(ROOT), **(env or {})})


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "a2":
        a2(sys.argv[2])
    elif mode == "a1":
        a1_one_node(sys.argv[2])
    else:
        W.mkdir(parents=True, exist_ok=True)
        run("a2", "crash", env={"CRASH": "1"})
        p = run("a2", "crash", env={"RESUME": "1"})
        res = {"A2 crash+resume emails (was 2)": outbox("a2_crash"),
               "resume error": (p.stderr.strip().splitlines() or [""])[-1][:120] if p.returncode else ""}
        run("a2", "retry")
        res["A2 timeout retry emails (was 2)"] = outbox("a2_retry")
        run("a1", "1")
        run("a1", "2")
        res["A1 one-node executed (was 12)"] = (W / "a1.log").read_text().split() if (W / "a1.log").exists() else []
        res["A1 one-node refused"] = (W / "a1_refused.log").read_text().split("\n")[0] if (W / "a1_refused.log").exists() else ""
        res["A6 HITL middleware (was paid 9999)"] = a6_hitl()
        print(json.dumps(res, indent=1))
        a6 = res["A6 HITL middleware (was paid 9999)"]
        ok = (res["A2 crash+resume emails (was 2)"] == 1 and res["A2 timeout retry emails (was 2)"] == 1
              and res["A1 one-node executed (was 12)"] == [] and bool(res["A1 one-node refused"])
              and a6["paid"] == [] and [list(x) for x in a6["refused"]] == [[9999, "mallory"]])
        print("framework kill checks pass" if ok else "FRAMEWORK KILL CHECKS FAILED")
        sys.exit(0 if ok else 1)
