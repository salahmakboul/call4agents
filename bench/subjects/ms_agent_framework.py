# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Microsoft Agent Framework (Python): an Agent with a scripted chat client (no key, no network), run as the
start executor of a workflow with FileCheckpointStorage.

Default (as documented): WorkflowBuilder(start_executor=agent, checkpoint_storage=FileCheckpointStorage(...)),
one storage folder per run. A crashed run is restored from its newest checkpoint with
workflow.run(checkpoint_id=...); with no checkpoint yet, the message is sent again. A tool error goes to the
model as a function result ("Error: Function failed.", the default include_detailed_errors=False), and the
scripted model asks once more. Approval: @tool(approval_mode="always_require"); the workflow pauses with a
function_approval_request (a request_info event); the person's answer is
request.to_function_approval_response(approved=...), sent with workflow.run(checkpoint_id=<newest>,
responses={request_id: answer}) in a new process.

The scripted client is built like the tests' mock client (FunctionInvocationLayer + BaseChatClient).
"""
import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional

from bench.subject import Env, Subject, kit_run, next_move

PROMPT = "pay alice 50"


class MSAgentFramework(Subject):
    name = "ms_agent_framework"
    title = "Microsoft Agent Framework"
    packages = ("agent-framework-core",)
    requires = ("agent_framework",)
    default_config = ("the agent as start executor of a workflow with FileCheckpointStorage, a crash restored from "
                      "the newest checkpoint with run(checkpoint_id=...); tool errors go to the model (default "
                      "include_detailed_errors=False); approval with @tool(approval_mode=\"always_require\") and "
                      "to_function_approval_response, sent with run(checkpoint_id=..., responses=...)")
    kit_config = ("the bank call wrapped in call4agents.adapters.agent_framework.effect(approval=..., "
                  "reconcile=bank lookup) inside c4a.run(run id); the yes and no built with c4a_maf.respond(...), "
                  "which also records them with c4a.approve / c4a.deny")

    def not_applicable(self, scenario: str, kit: bool) -> Optional[str]:
        if scenario == "S6" and not kit:
            return ("the yes is the approval response sent with the resume itself; Agent Framework keeps no record "
                    "of a decision before that, so a later no is never given to it")
        return None

    def _workflow(self, env: Env, approval: bool):
        from agent_framework import (Agent, BaseChatClient, ChatResponse, Content, FileCheckpointStorage,
                                     FunctionInvocationLayer, Message, WorkflowBuilder, tool)

        class Scripted(FunctionInvocationLayer, BaseChatClient):
            def _inner_get_response(self, *, messages: Any, stream: bool, options: Any, **kwargs: Any) -> Any:
                async def answer():
                    calls = {c.call_id for m in messages for c in (m.contents or [])
                             if c.type == "function_call" and c.name == "pay"}
                    outputs: List[str] = []
                    for m in messages:
                        for c in m.contents or []:
                            if c.type == "function_result" and c.call_id in calls:
                                text = str(c.result)
                                outputs.append(f"error: {c.exception}: {text}" if c.exception else text)
                    if next_move(outputs, env.repeat):
                        call = Content.from_function_call(call_id=f"call_{uuid.uuid4().hex[:16]}", name="pay",
                                                          arguments=json.dumps(env.call))
                        return ChatResponse(messages=Message(role="assistant", contents=[call]))
                    return ChatResponse(messages=Message(role="assistant", contents=["done"]))
                if stream:
                    raise NotImplementedError("the benchmark uses non-streaming runs")
                return answer()

        def pay(amount: int, to: str) -> str:          # the name the model calls; the guard keys on bench.pay
            return env.pay(amount, to)
        if env.kit:
            from call4agents.adapters import agent_framework as c4a_maf
            env.kit_init()
            guarded = c4a_maf.effect(pay, name="bench.pay", approval=approval, reconcile=env.reconcile())
        else:
            guarded = pay
        self.guarded = guarded

        def tool_pay(amount: int, to: str) -> str:
            """Pay someone."""
            result = guarded(amount=amount, to=to)
            env.after_action()
            return result
        tool_pay.__name__ = "pay"
        fn = tool(approval_mode="always_require")(tool_pay) if approval else tool(tool_pay)

        agent = Agent(client=Scripted(), name="payer", instructions="pay", tools=[fn])
        storage = FileCheckpointStorage(env.path("checkpoints"))
        wf = WorkflowBuilder(start_executor=agent, checkpoint_storage=storage, name="pay_workflow").build()
        return wf, storage

    def _go(self, env: Env, coro: Any) -> Any:
        with kit_run(env):
            return asyncio.run(coro)

    @staticmethod
    def _requests(result: Any) -> List[Any]:
        return [(ev.request_id, ev.data) for ev in result.get_request_info_events()
                if getattr(ev.data, "type", None) == "function_approval_request"]

    def _save_waiting(self, env: Env, result: Any) -> Optional[Dict[str, Any]]:
        pending = self._requests(result)
        if not pending:
            env.save("waiting.json", None)
            return None
        rid, request = pending[-1]
        env.save("waiting.json", {"request_id": rid, "request": request.to_dict()})
        return dict(request.function_call.parse_arguments() or {})

    # --- no approval: the checkpoints are the saved state
    def start(self, env: Env) -> Any:
        wf, _ = self._workflow(env, approval=False)
        self._go(env, wf.run(PROMPT))
        return {"finished": True}

    def resume(self, env: Env) -> Any:
        wf, storage = self._workflow(env, approval=False)

        async def go():
            latest = await storage.get_latest(workflow_name="pay_workflow")
            if latest is None:                                 # nothing was saved: send the message again
                await wf.run(PROMPT)
                return "started again"
            await wf.run(checkpoint_id=latest.checkpoint_id)
            return "restored"
        return {"finished": True, "how": self._go(env, go())}

    # --- approval: the workflow pauses with a function_approval_request
    def start_approval(self, env: Env) -> Dict[str, Any]:
        wf, _ = self._workflow(env, approval=True)

        async def go():
            return self._save_waiting(env, await wf.run(PROMPT))
        return self._go(env, go())

    def decide(self, env: Env, shown: Dict[str, Any], decision: str) -> Dict[str, Any]:
        from agent_framework import Content
        self._workflow(env, approval=True)
        waiting = env.load("waiting.json")
        request = Content.from_dict(waiting["request"])
        yes = decision == "yes"
        if env.kit:
            from call4agents.adapters import agent_framework as c4a_maf
            with kit_run(env):
                answer = c4a_maf.respond(request, yes, tools=[self.guarded])
        else:
            answer = request.to_function_approval_response(approved=yes)
        return {"request_id": waiting["request_id"], "response": answer.to_dict(), "decision": decision}

    def resume_approved(self, env: Env, message: Dict[str, Any]) -> Any:
        from agent_framework import Content
        wf, storage = self._workflow(env, approval=True)
        answer = Content.from_dict(message["response"])

        async def go():
            latest = await storage.get_latest(workflow_name="pay_workflow")
            result = await wf.run(checkpoint_id=latest.checkpoint_id, responses={message["request_id"]: answer})
            return self._save_waiting(env, result)
        waiting = self._go(env, go())
        return {"waiting": waiting} if waiting else {"finished": True}

    def tamper(self, env: Env, new_args: Dict[str, Any]) -> None:
        """Change the waiting call in every saved checkpoint of this run, with the storage's own load and save."""
        _, storage = self._workflow(env, approval=True)

        async def go():
            n = 0
            for cp in await storage.list_checkpoints(workflow_name="pay_workflow"):
                k = _rewrite(cp, json.dumps(new_args), set())
                if k:
                    await storage.save(cp)
                    n += k
            return n
        if not asyncio.run(go()):
            raise RuntimeError("no waiting pay call found in the saved checkpoints")


def _rewrite(node: Any, arguments: str, seen: set) -> int:
    """Set `arguments` on every saved function call to pay inside a checkpoint: the call in the agent session's
    messages, the copies in its pending approval requests, and the request the workflow is waiting on. They are
    stored as Content objects (with __slots__) and as plain dicts."""
    if id(node) in seen or isinstance(node, (str, bytes, int, float, bool, type(None))):
        return 0
    seen.add(id(node))
    n = 0
    if isinstance(node, dict):
        if node.get("type") == "function_call" and node.get("name") == "pay" and "arguments" in node:
            node["arguments"] = arguments
            n += 1
        items = list(node.values())
    elif isinstance(node, (list, tuple, set)):
        items = list(node)
    else:
        if getattr(node, "type", None) == "function_call" and getattr(node, "name", None) == "pay":
            node.arguments = arguments
            n += 1
        fields = dict(getattr(node, "__dict__", {}))
        for cls in type(node).__mro__:
            for k in getattr(cls, "__slots__", ()):
                if k not in fields and hasattr(node, k):
                    fields[k] = getattr(node, k)
        items = list(fields.values())
    for v in items:
        n += _rewrite(v, arguments, seen)
    return n
