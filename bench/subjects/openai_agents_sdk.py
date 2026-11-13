# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""OpenAI Agents SDK: Runner.run with a scripted Model (no key, no network).

Default (as documented): a run without approval keeps its items in an SQLiteSession; after a crash the app
runs the same session again. A tool error goes to the model as text (the default failure_error_function),
and the scripted model asks once more. Approval: function_tool(needs_approval=True); the run stops with
result.interruptions; the app saves result.to_state().to_string(), the person's decision is applied with
state.approve(item) / state.reject(item) on the saved RunState, and Runner.run(agent, state) continues it.
"""
import asyncio
import json
import os
import uuid
from typing import Any, Dict, Optional

from bench.subject import Env, Subject, kit_run, next_move

PROMPT = "pay alice 50"


class OpenAIAgents(Subject):
    name = "openai_agents"
    title = "OpenAI Agents SDK"
    packages = ("openai-agents", "openai")
    requires = ("agents", "agents.run_state")
    default_config = ("Runner.run with an SQLiteSession, a crash resumed by running the session again; tool errors "
                      "go to the model (default failure_error_function); approval with needs_approval=True and a saved "
                      "RunState (state.approve / state.reject, then Runner.run(agent, state))")
    kit_config = ("the bank call wrapped in call4agents.adapters.openai_agents.effect(approval=..., reconcile=bank "
                  "lookup) inside c4a.run(run id); the yes and no also recorded with c4a.approve / c4a.deny")

    def not_applicable(self, scenario: str, kit: bool) -> Optional[str]:
        if scenario == "S8":
            return ("each run keeps its decisions in its own RunState, matched by call id; the SDK has no decision "
                    "message that could be handed to another run")
        return None

    def _agent(self, env: Env, approval: bool):
        os.environ.setdefault("OPENAI_API_KEY", "not-used-scripted-model")
        from agents import Agent, ModelResponse, function_tool, set_tracing_disabled
        from agents.models.interface import Model
        from agents.usage import Usage
        from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
        set_tracing_disabled(True)

        class Scripted(Model):
            async def get_response(self, *a: Any, **k: Any) -> Any:
                items = k.get("input", a[1] if len(a) > 1 else [])
                items = items if isinstance(items, list) else []
                outputs = []
                for i in items:
                    kind = i.get("type") if isinstance(i, dict) else getattr(i, "type", "")
                    if kind == "function_call_output":
                        outputs.append(i.get("output") if isinstance(i, dict) else getattr(i, "output", ""))
                if next_move(outputs, env.repeat):
                    cid = f"call_{uuid.uuid4().hex[:16]}"
                    out = [ResponseFunctionToolCall(id=f"fc_{cid}", call_id=cid, type="function_call", name="pay",
                                                    arguments=json.dumps(env.call), status="completed")]
                else:
                    out = [ResponseOutputMessage(id=f"msg_{uuid.uuid4().hex[:8]}", type="message", role="assistant",
                                                 status="completed",
                                                 content=[ResponseOutputText(type="output_text", text="done",
                                                                             annotations=[])])]
                return ModelResponse(output=out, usage=Usage(), response_id=None)

            def stream_response(self, *a: Any, **k: Any) -> Any:
                raise NotImplementedError

        def charge(amount: int, to: str) -> str:
            return env.pay(amount, to)
        if env.kit:
            from call4agents.adapters import openai_agents as c4a_oa
            env.kit_init()
            guarded = c4a_oa.effect(charge, name="bench.pay", approval=approval, reconcile=env.reconcile())
        else:
            guarded = charge
        self.guarded = guarded

        def pay(amount: int, to: str) -> str:
            """Pay someone."""
            result = guarded(amount=amount, to=to)
            env.after_action()
            return result

        tool = function_tool(pay, needs_approval=approval)
        return Agent(name="payer", instructions="pay", tools=[tool], model=Scripted())

    def _go(self, env: Env, coro_fn) -> Any:
        with kit_run(env):
            return asyncio.run(coro_fn())

    # --- no approval: the session is the saved state
    def start(self, env: Env) -> Any:
        from agents import Runner, SQLiteSession
        agent = self._agent(env, approval=False)

        async def go():
            session = SQLiteSession(env.run_id, str(env.work / "session.sqlite"))
            await Runner.run(agent, PROMPT, session=session, max_turns=10)
        self._go(env, go)
        return {"finished": True}

    def resume(self, env: Env) -> Any:
        from agents import Runner, SQLiteSession
        agent = self._agent(env, approval=False)

        async def go():
            session = SQLiteSession(env.run_id, str(env.work / "session.sqlite"))
            items = await session.get_items()
            if items and items[-1].get("role") == "assistant":
                return                                    # it had finished
            await Runner.run(agent, [] if items else PROMPT, session=session, max_turns=10)
        self._go(env, go)
        return {"finished": True}

    # --- approval: the saved RunState holds the waiting call and the decisions
    def _save(self, env: Env, result: Any) -> Optional[Dict[str, Any]]:
        if not result.interruptions:
            env.save("runstate.json", None)
            return None
        env.path("runstate.json").write_text(result.to_state().to_string(), encoding="utf-8")
        return json.loads(result.interruptions[0].arguments or "{}")

    async def _state(self, env: Env, agent: Any) -> Any:
        from agents import RunState
        return await RunState.from_string(agent, env.path("runstate.json").read_text(encoding="utf-8"))

    def start_approval(self, env: Env) -> Dict[str, Any]:
        from agents import Runner
        agent = self._agent(env, approval=True)

        async def go():
            return self._save(env, await Runner.run(agent, PROMPT, max_turns=10))
        return self._go(env, go)

    def decide(self, env: Env, shown: Dict[str, Any], decision: str) -> Dict[str, Any]:
        agent = self._agent(env, approval=True)

        async def go():
            state = await self._state(env, agent)
            ids = []
            for item in state.get_interruptions():
                (state.approve if decision == "yes" else state.reject)(item)
                ids.append(item.call_id)
            env.path("runstate.json").write_text(state.to_string(), encoding="utf-8")
            return ids
        ids = self._go(env, go)
        if env.kit:
            import call4agents as c4a
            with kit_run(env):
                (c4a.approve if decision == "yes" else c4a.deny)(self.guarded, **shown)
        return {"call_ids": ids, "decision": decision}

    def resume_approved(self, env: Env, message: Dict[str, Any]) -> Any:
        from agents import Runner
        agent = self._agent(env, approval=True)

        async def go():
            state = await self._state(env, agent)      # the decisions are in the saved state
            return self._save(env, await Runner.run(agent, state, max_turns=10))
        waiting = self._go(env, go)
        return {"waiting": waiting} if waiting else {"finished": True}

    def tamper(self, env: Env, new_args: Dict[str, Any]) -> None:
        p = env.path("runstate.json")
        doc = json.loads(p.read_text(encoding="utf-8"))
        changed = _rewrite(doc, json.dumps(new_args))
        if not changed:
            raise RuntimeError("no waiting pay call found in the saved RunState")
        p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _rewrite(node: Any, arguments: str) -> int:
    """Set `arguments` on every saved function call to pay (the waiting call and its copies in the state)."""
    n = 0
    if isinstance(node, dict):
        if node.get("type") == "function_call" and node.get("name") == "pay" and "arguments" in node:
            node["arguments"] = arguments
            n += 1
        for v in node.values():
            n += _rewrite(v, arguments)
    elif isinstance(node, list):
        for v in node:
            n += _rewrite(v, arguments)
    return n
