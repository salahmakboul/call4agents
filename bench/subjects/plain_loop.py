# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""A small hand-written agent loop with no framework: a baseline, not a recommendation.

It keeps its state in state.json and saves after each step, the simple way: a "yes" sets an approved
flag, a resume runs the waiting call. It shows that the failure modes exist without any framework. It
is written plainly on purpose; a careful hand-written loop can avoid some of them.
"""
from typing import Any, Dict, Optional

from bench.subject import Env, Subject, kit_run as _run, next_move


class PlainLoop(Subject):
    name = "plain"
    title = "Plain Python loop"
    packages = ()
    default_config = "a while loop, state saved to a JSON file after each step; a tool error goes to the caller"
    kit_config = "the bank call wrapped in @c4a.effect(approval=..., reconcile=bank lookup)"

    def not_applicable(self, scenario: str, kit: bool) -> Optional[str]:
        if scenario == "S6" and not kit:
            return ("the yes is only the resume message; the loop keeps no record of decisions, so a later "
                    "no is never seen by it")
        return None

    def _tool(self, env: Env, approval: bool):
        def charge(amount: int, to: str) -> str:
            return env.pay(amount, to)
        if not env.kit:
            guarded = charge
        else:
            import call4agents as c4a
            env.kit_init()
            guarded = c4a.effect(charge, name="bench.pay", approval=approval, reconcile=env.reconcile())

        def pay(amount: int, to: str) -> str:
            result = guarded(amount=amount, to=to)
            env.after_action()
            return result
        return pay, guarded

    def _loop(self, env: Env, approval: bool) -> Dict[str, Any]:
        pay, _ = self._tool(env, approval)
        with _run(env):
            while True:
                state = env.load("state.json")
                if not next_move(state["outputs"], env.repeat):
                    state["finished"] = True
                    env.save("state.json", state)
                    return {"finished": True}
                state["pending"] = state.get("pending") or dict(env.call)
                if approval and not state.get("approved"):
                    state["waiting"] = True
                    env.save("state.json", state)
                    return {"waiting": state["pending"]}
                out = pay(**state["pending"])        # a tool error stops the loop; the caller decides
                state["outputs"].append(out)
                state["pending"] = None
                env.save("state.json", state)

    def start(self, env: Env) -> Any:
        env.save("state.json", {"outputs": [], "pending": None})
        return self._loop(env, approval=False)

    def resume(self, env: Env) -> Any:
        if env.load("state.json", {}).get("finished"):
            return {"finished": True}
        return self._loop(env, approval=False)

    def start_approval(self, env: Env) -> Dict[str, Any]:
        env.save("state.json", {"outputs": [], "pending": None, "approved": False})
        return self._loop(env, approval=True)["waiting"]

    def decide(self, env: Env, shown: Dict[str, Any], decision: str) -> Dict[str, Any]:
        if env.kit:
            import call4agents as c4a
            _, guarded = self._tool(env, approval=True)
            with _run(env):
                (c4a.approve if decision == "yes" else c4a.deny)(guarded, **shown)
        return {"resume": decision}

    def resume_approved(self, env: Env, message: Dict[str, Any]) -> Any:
        state = env.load("state.json")
        if message.get("resume") == "yes":
            state["approved"] = True               # the simple way: one flag, kept for the rest of the run
        else:
            state["finished"] = True
        env.save("state.json", state)
        if state.get("finished"):
            return {"finished": True}
        return self._loop(env, approval=True)

    def tamper(self, env: Env, new_args: Dict[str, Any]) -> None:
        state = env.load("state.json")
        state["pending"] = dict(new_args)
        env.save("state.json", state)
