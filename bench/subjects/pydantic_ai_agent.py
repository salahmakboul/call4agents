# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Pydantic AI: an Agent with a FunctionModel (scripted, no key, no network).

Default (as documented): the app saves the message history (all_messages) while the run goes, with
agent.iter, and continues a run with message_history=. A tool that raises an error other than ModelRetry
ends the run with that error; the caller continues it from the saved history. Approval: a tool with
requires_approval=True and output_type=[str, DeferredToolRequests]; the run ends with the waiting calls;
the next run gets message_history= and DeferredToolResults(approvals={tool_call_id: True or False}).

Not used here: Pydantic AI's durable execution (Temporal, DBOS, Prefect), which needs a server. A subject
for it is welcome.
"""
import json
import uuid
from typing import Any, Dict, Optional

from bench.subject import Env, Subject, kit_run, next_move

PROMPT = "pay alice 50"


class PydanticAI(Subject):
    name = "pydantic_ai"
    title = "Pydantic AI"
    packages = ("pydantic-ai-slim", "pydantic")
    requires = ("pydantic_ai", "pydantic_ai.models.function")
    default_config = ("agent.iter with the message history saved after each step, continued with message_history=; "
                      "approval with requires_approval=True, DeferredToolRequests and DeferredToolResults")
    kit_config = ("the bank call wrapped in call4agents.adapters.pydantic_ai.effect(approval=..., reconcile=bank "
                  "lookup) inside c4a.run(run id); the yes and no also recorded with c4a.approve / c4a.deny")

    def not_applicable(self, scenario: str, kit: bool) -> Optional[str]:
        if scenario == "S6" and not kit:
            return ("the yes is only in the DeferredToolResults given to the next run; Pydantic AI keeps no record "
                    "of a decision, so a later no is never given to it")
        return None

    def _agent(self, env: Env, approval: bool):
        from pydantic_ai import Agent, DeferredToolRequests
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
        from pydantic_ai.models.function import FunctionModel

        def model(messages: list, info: Any) -> Any:
            outputs = []
            for m in messages:
                for p in m.parts:
                    if getattr(p, "part_kind", "") == "tool-return":
                        outputs.append(str(p.content))
                    elif getattr(p, "part_kind", "") == "retry-prompt":
                        outputs.append("error: " + str(p.content))
            if next_move(outputs, env.repeat):
                return ModelResponse(parts=[ToolCallPart("pay", dict(env.call), tool_call_id=f"pyd_{uuid.uuid4().hex[:16]}")])
            return ModelResponse(parts=[TextPart("done")])

        agent = Agent(FunctionModel(model), output_type=[str, DeferredToolRequests] if approval else str)

        def charge(amount: int, to: str) -> str:
            return env.pay(amount, to)
        if env.kit:
            from call4agents.adapters import pydantic_ai as c4a_pai
            env.kit_init()
            guarded = c4a_pai.effect(charge, name="bench.pay", approval=approval, reconcile=env.reconcile())
        else:
            guarded = charge
        self.guarded = guarded

        def pay(amount: int, to: str) -> str:
            result = guarded(amount=amount, to=to)
            env.after_action()
            return result
        agent.tool_plain(requires_approval=approval)(pay)
        return agent

    # --- no approval: the app saves the history after each step
    def _iterate(self, env: Env, history: Any) -> None:
        import asyncio

        from pydantic_ai.messages import ModelMessagesTypeAdapter
        agent = self._agent(env, approval=False)
        store = env.path("history.json")

        async def go():
            async with agent.iter(None if history else PROMPT, message_history=history) as run:
                async for _ in run:
                    store.write_bytes(ModelMessagesTypeAdapter.dump_json(run.all_messages()))
        with kit_run(env):
            asyncio.run(go())

    def start(self, env: Env) -> Any:
        self._iterate(env, None)
        return {"finished": True}

    def resume(self, env: Env) -> Any:
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        store = env.path("history.json")
        history = ModelMessagesTypeAdapter.validate_json(store.read_bytes()) if store.exists() else None
        if history and getattr(history[-1].parts[-1], "part_kind", "") == "text":
            return {"finished": True}                      # it had finished
        self._iterate(env, history)
        return {"finished": True}

    # --- approval: the run ends with DeferredToolRequests; the next run gets the decisions
    def _end(self, env: Env, result: Any) -> Optional[Dict[str, Any]]:
        from pydantic_ai import DeferredToolRequests
        env.path("history.json").write_bytes(result.all_messages_json())
        if isinstance(result.output, DeferredToolRequests) and result.output.approvals:
            call = result.output.approvals[0]
            env.save("waiting.json", {"tool_call_id": call.tool_call_id})
            return call.args_as_dict()
        return None

    def start_approval(self, env: Env) -> Dict[str, Any]:
        agent = self._agent(env, approval=True)
        with kit_run(env):
            return self._end(env, agent.run_sync(PROMPT))

    def decide(self, env: Env, shown: Dict[str, Any], decision: str) -> Dict[str, Any]:
        if env.kit:
            import call4agents as c4a
            self._agent(env, approval=True)
            with kit_run(env):
                (c4a.approve if decision == "yes" else c4a.deny)(self.guarded, **shown)
        call_id = env.load("waiting.json")["tool_call_id"]
        return {"approvals": {call_id: decision == "yes"}}

    def resume_approved(self, env: Env, message: Dict[str, Any]) -> Any:
        from pydantic_ai import DeferredToolResults
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        agent = self._agent(env, approval=True)
        history = ModelMessagesTypeAdapter.validate_json(env.path("history.json").read_bytes())
        with kit_run(env):
            result = agent.run_sync(message_history=history,
                                    deferred_tool_results=DeferredToolResults(approvals=dict(message["approvals"])))
        waiting = self._end(env, result)
        return {"waiting": waiting} if waiting else {"finished": True}

    def tamper(self, env: Env, new_args: Dict[str, Any]) -> None:
        p = env.path("history.json")
        doc = json.loads(p.read_text(encoding="utf-8"))
        n = 0
        for m in doc:
            for part in m.get("parts", []):
                if part.get("part_kind") == "tool-call" and part.get("tool_name") == "pay":
                    part["args"] = dict(new_args)
                    n += 1
        if not n:
            raise RuntimeError("no waiting pay call found in the saved history")
        p.write_text(json.dumps(doc), encoding="utf-8")
