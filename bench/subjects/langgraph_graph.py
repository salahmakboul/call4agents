# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""LangGraph: a StateGraph with a SqliteSaver checkpointer, resumed by thread id.

Default (as documented): a `model` node (scripted) proposes the call, a `tool` node runs it, and for runs that
need a yes an `approve` node calls interrupt() before the tool; the person's answer comes back with
Command(resume=...). Resume after a crash or an error: graph.invoke(None, config). No retry policy (the
default); an error reaches the caller, who invokes again.
"""
import sqlite3
from typing import Any, Dict, Optional, TypedDict

from bench.subject import Env, Subject


class State(TypedDict, total=False):
    pending: Optional[Dict[str, Any]]
    outputs: list
    denied: bool


class LangGraphGraph(Subject):
    name = "langgraph"
    title = "LangGraph"
    packages = ("langgraph", "langgraph-checkpoint-sqlite")
    requires = ("langgraph", "langgraph.checkpoint.sqlite")
    default_config = ("StateGraph + SqliteSaver, resume with invoke(None, config); approval with interrupt() in a "
                      "node before the tool and Command(resume=...); no RetryPolicy")
    kit_config = ("the bank call wrapped in call4agents.adapters.langgraph.effect(approval=..., reconcile=bank "
                  "lookup); the yes and no also recorded with c4a_lg.approve / c4a_lg.deny")

    def not_applicable(self, scenario: str, kit: bool) -> Optional[str]:
        if scenario == "S6" and not kit:
            return ("the yes is the resume value itself (Command(resume=...)); LangGraph keeps no record of a "
                    "decision, so a later no is never given to it")
        return None

    def _graph(self, env: Env, approval: bool):
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt

        from bench.subject import next_move

        def charge(amount: int, to: str) -> str:
            return env.pay(amount, to)
        if env.kit:
            from call4agents.adapters import langgraph as c4a_lg
            env.kit_init()
            guarded = c4a_lg.effect(charge, name="bench.pay", approval=approval, reconcile=env.reconcile())
        else:
            guarded = charge
        self.guarded = guarded

        def model(state: State) -> Dict[str, Any]:            # the scripted model
            if next_move(state.get("outputs", []), env.repeat):
                return {"pending": dict(env.call)}
            return {"pending": None}

        def approve(state: State) -> Dict[str, Any]:
            answer = interrupt({"call": state["pending"]})
            return {} if answer == "yes" else {"pending": None, "denied": True}

        def tool(state: State) -> Dict[str, Any]:
            result = guarded(**state["pending"])
            env.after_action()
            return {"outputs": state.get("outputs", []) + [result], "pending": None}

        g = StateGraph(State)
        g.add_node("model", model)
        g.add_node("tool", tool)
        g.add_edge(START, "model")
        if approval:
            g.add_node("approve", approve)
            g.add_conditional_edges("model", lambda s: "approve" if s.get("pending") else END, ["approve", END])
            g.add_conditional_edges("approve", lambda s: "tool" if s.get("pending") else END, ["tool", END])
        else:
            g.add_conditional_edges("model", lambda s: "tool" if s.get("pending") else END, ["tool", END])
        g.add_edge("tool", "model")
        db = sqlite3.connect(str(env.work / "checkpoints.sqlite"), check_same_thread=False, timeout=30)
        return g.compile(checkpointer=SqliteSaver(db)), {"configurable": {"thread_id": env.run_id}}

    @staticmethod
    def _waiting(out: Any) -> Optional[Dict[str, Any]]:
        found = out.get("__interrupt__") if isinstance(out, dict) else None
        return dict(found[0].value["call"]) if found else None

    def start(self, env: Env) -> Any:
        graph, cfg = self._graph(env, approval=False)
        graph.invoke({"outputs": []}, cfg)
        return {"finished": True}

    def resume(self, env: Env) -> Any:
        graph, cfg = self._graph(env, approval=False)
        if not graph.get_state(cfg).next:
            return {"finished": True}
        graph.invoke(None, cfg)
        return {"finished": True}

    def start_approval(self, env: Env) -> Dict[str, Any]:
        graph, cfg = self._graph(env, approval=True)
        return self._waiting(graph.invoke({"outputs": []}, cfg))

    def decide(self, env: Env, shown: Dict[str, Any], decision: str) -> Dict[str, Any]:
        if env.kit:
            from call4agents.adapters import langgraph as c4a_lg
            graph, cfg = self._graph(env, approval=True)
            (c4a_lg.approve if decision == "yes" else c4a_lg.deny)(self.guarded, cfg, **shown)
        return {"resume": decision}

    def resume_approved(self, env: Env, message: Dict[str, Any]) -> Any:
        from langgraph.types import Command
        graph, cfg = self._graph(env, approval=True)
        waiting = self._waiting(graph.invoke(Command(resume=message["resume"]), cfg))
        return {"waiting": waiting} if waiting else {"finished": True}

    def tamper(self, env: Env, new_args: Dict[str, Any]) -> None:
        graph, cfg = self._graph(env, approval=True)
        graph.update_state(cfg, {"pending": dict(new_args)})
