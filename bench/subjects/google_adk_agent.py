# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Google ADK: an LlmAgent with a scripted BaseLlm (no key, no network), in a resumable App.

Default (as documented): App(..., resumability_config=ResumabilityConfig(is_resumable=True)), sessions in a
SqliteSessionService, so a session and its events survive the process. A run is runner.run_async(user_id,
session_id, new_message=...); an interrupted invocation is resumed with runner.run_async(user_id, session_id,
invocation_id=...), the id read from the saved session's events. No on_tool_error_callback (the default): a
tool error ends the invocation and reaches the caller, who resumes it once. Approval: FunctionTool(pay,
require_confirmation=True); the run pauses with an adk_request_confirmation call; the person's answer is a
FunctionResponse(name="adk_request_confirmation", response={"confirmed": ...}) sent as new_message with the
paused invocation's id (the tool confirmation docs for resumable apps).

Seen with google-adk 2.14.1 (19 Nov 2026): after a tool error ADK saves an error event in the session, and
resuming that invocation ends it without running the tool again (and without a model answer). A
FunctionResponse whose id matches no call in the session is refused with ValueError (S8). When two workers
resume one session, the second one to save gets StaleSessionError, after its tool already ran (S3).
"""
import asyncio
import json
import sqlite3
from typing import Any, Dict, List, Optional

from bench.subject import Env, Subject, kit_run, next_move

APP, USER = "bench_app", "bench-user"
REQUEST = "adk_request_confirmation"
PROMPT = "pay alice 50"


class GoogleADK(Subject):
    name = "google_adk"
    title = "Google ADK"
    packages = ("google-adk", "google-genai")
    requires = ("google.adk", "google.adk.runners", "google.adk.sessions.sqlite_session_service")
    default_config = ("resumable App + SqliteSessionService, resume with run_async(invocation_id=...); no "
                      "on_tool_error_callback (a tool error reaches the caller); approval with "
                      "FunctionTool(require_confirmation=True) and an adk_request_confirmation FunctionResponse")
    kit_config = ("the bank call wrapped in call4agents.adapters.google_adk.effect(approval=..., reconcile=bank "
                  "lookup) inside c4a.run(run id); the yes and no built with c4a_adk.confirm(...), which also "
                  "records them with c4a.approve / c4a.deny")

    def not_applicable(self, scenario: str, kit: bool) -> Optional[str]:
        if scenario == "S6" and not kit:
            return ("the yes is the FunctionResponse sent with the resume itself; ADK keeps no record of a decision "
                    "before that, so a later no is never given to it")
        return None

    def _runner(self, env: Env, approval: bool):
        from google.adk.agents import LlmAgent
        from google.adk.apps import App, ResumabilityConfig
        from google.adk.models.base_llm import BaseLlm
        from google.adk.models.llm_response import LlmResponse
        from google.adk.runners import Runner
        from google.adk.sessions.sqlite_session_service import SqliteSessionService
        from google.adk.tools import FunctionTool
        from google.genai import types

        class Scripted(BaseLlm):
            model: str = "scripted"

            async def generate_content_async(self, llm_request: Any, stream: bool = False) -> Any:
                outputs = []
                for c in llm_request.contents:
                    for p in c.parts or []:
                        r = p.function_response
                        if r is not None and r.name == "pay":
                            body = r.response or {}
                            outputs.append(str(body.get("result", body)))
                if next_move(outputs, env.repeat):
                    part = types.Part(function_call=types.FunctionCall(name="pay", args=dict(env.call)))
                else:
                    part = types.Part(text="done")
                yield LlmResponse(content=types.Content(role="model", parts=[part]))

        def pay(amount: int, to: str) -> str:          # the name the model calls; the guard keys on bench.pay
            return env.pay(amount, to)
        if env.kit:
            from call4agents.adapters import google_adk as c4a_adk
            env.kit_init()
            guarded = c4a_adk.effect(pay, name="bench.pay", approval=approval, reconcile=env.reconcile())
        else:
            guarded = pay
        self.guarded = guarded

        def tool_pay(amount: int, to: str) -> str:
            """Pay someone."""
            result = guarded(amount=amount, to=to)
            env.after_action()
            return result
        tool_pay.__name__ = "pay"

        agent = LlmAgent(name="payer", model=Scripted(), instruction="pay",
                         tools=[FunctionTool(tool_pay, require_confirmation=approval)])
        app = App(name=APP, root_agent=agent, resumability_config=ResumabilityConfig(is_resumable=True))
        service = SqliteSessionService(db_path=str(env.work / "sessions.db"))
        return Runner(app=app, session_service=service), service, types

    def _go(self, env: Env, coro: Any) -> Any:
        with kit_run(env):
            return asyncio.run(coro)

    @staticmethod
    async def _drain(agen: Any) -> List[Any]:
        return [e async for e in agen]

    @staticmethod
    def _parts(event: Any) -> List[Any]:
        return list((event.content.parts if event.content else None) or [])

    def _waiting(self, events: List[Any]) -> Optional[Dict[str, Any]]:
        """The last adk_request_confirmation that has no answer yet in these events, as saved JSON."""
        answered = {p.function_response.id for e in events for p in self._parts(e)
                    if p.function_response is not None and p.function_response.name == REQUEST}
        found = None
        for e in events:
            for p in self._parts(e):
                fc = p.function_call
                if fc is not None and fc.name == REQUEST and fc.id not in answered:
                    original = (fc.args or {}).get("originalFunctionCall") or {}
                    found = {"id": fc.id, "invocation_id": e.invocation_id, "args": dict(original.get("args") or {})}
        return found

    async def _session(self, service: Any, env: Env) -> Any:
        return await service.get_session(app_name=APP, user_id=USER, session_id=env.run_id)

    # --- no approval: the session in SQLite is the saved state
    def start(self, env: Env) -> Any:
        runner, service, types = self._runner(env, approval=False)

        async def go():
            await service.create_session(app_name=APP, user_id=USER, session_id=env.run_id)
            await self._drain(runner.run_async(user_id=USER, session_id=env.run_id, new_message=types.Content(
                role="user", parts=[types.Part(text=PROMPT)])))
        self._go(env, go())
        return {"finished": True}

    def resume(self, env: Env) -> Any:
        runner, service, types = self._runner(env, approval=False)

        async def go():
            session = await self._session(service, env)
            events = list(session.events) if session else []
            if events and events[-1].author != "user" and any(p.text for p in self._parts(events[-1])):
                return "finished before"                      # the final answer was saved
            if not events:                                   # nothing was saved: start the message again
                if session is None:
                    await service.create_session(app_name=APP, user_id=USER, session_id=env.run_id)
                await self._drain(runner.run_async(user_id=USER, session_id=env.run_id, new_message=types.Content(
                    role="user", parts=[types.Part(text=PROMPT)])))
                return "started again"
            await self._drain(runner.run_async(user_id=USER, session_id=env.run_id,
                                               invocation_id=events[-1].invocation_id))
            return "resumed"
        return {"finished": True, "how": self._go(env, go())}

    # --- approval: the run pauses with an adk_request_confirmation call, saved in the session
    def start_approval(self, env: Env) -> Dict[str, Any]:
        runner, service, types = self._runner(env, approval=True)

        async def go():
            await service.create_session(app_name=APP, user_id=USER, session_id=env.run_id)
            events = await self._drain(runner.run_async(user_id=USER, session_id=env.run_id, new_message=types.Content(
                role="user", parts=[types.Part(text=PROMPT)])))
            return self._waiting(events)
        waiting = self._go(env, go())
        env.save("waiting.json", waiting)
        return waiting["args"] if waiting else None

    def decide(self, env: Env, shown: Dict[str, Any], decision: str) -> Dict[str, Any]:
        runner, service, types = self._runner(env, approval=True)
        waiting = env.load("waiting.json")
        yes = decision == "yes"
        if env.kit:
            from call4agents.adapters import google_adk as c4a_adk

            async def find():
                session = await self._session(service, env)
                for e in session.events:
                    for p in self._parts(e):
                        if p.function_call is not None and p.function_call.id == waiting["id"]:
                            return p.function_call
                raise RuntimeError("the adk_request_confirmation call is not in the saved session")
            call = asyncio.run(find())
            with kit_run(env):
                content = c4a_adk.confirm(call, yes, tools=[self.guarded])
        else:
            content = types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
                id=waiting["id"], name=REQUEST, response={"confirmed": yes}))])
        return {"content": content.model_dump(mode="json", exclude_none=True), "decision": decision}

    def resume_approved(self, env: Env, message: Dict[str, Any]) -> Any:
        runner, service, types = self._runner(env, approval=True)
        waiting = env.load("waiting.json")                     # this run's paused invocation
        content = types.Content.model_validate(message["content"])

        async def go():
            events = await self._drain(runner.run_async(user_id=USER, session_id=env.run_id,
                                                        invocation_id=waiting["invocation_id"], new_message=content))
            return self._waiting(events)
        again = self._go(env, go())
        if again:
            env.save("waiting.json", again)
            return {"waiting": again["args"]}
        return {"finished": True}

    def tamper(self, env: Env, new_args: Dict[str, Any]) -> None:
        """No ADK API edits a stored event, so the change is a write to the session database: the model's pay call
        and the copy of it inside the adk_request_confirmation call."""
        db = sqlite3.connect(str(env.work / "sessions.db"))
        n = 0
        try:
            rows = db.execute("SELECT id, event_data FROM events WHERE app_name = ? AND user_id = ? AND session_id = ?",
                              (APP, USER, env.run_id)).fetchall()
            for event_id, data in rows:
                doc = json.loads(data)
                k = _rewrite(doc, new_args)
                if k:
                    db.execute("UPDATE events SET event_data = ? WHERE id = ? AND session_id = ?",
                               (json.dumps(doc), event_id, env.run_id))
                    n += k
            db.commit()
        finally:
            db.close()
        if not n:
            raise RuntimeError("no waiting pay call found in the session database")


def _rewrite(node: Any, new_args: Dict[str, Any]) -> int:
    """Set the arguments of every saved call to pay (the model's call, and originalFunctionCall in the request)."""
    n = 0
    if isinstance(node, dict):
        if node.get("name") == "pay" and isinstance(node.get("args"), dict):
            node["args"] = dict(new_args)
            n += 1
        for v in node.values():
            n += _rewrite(v, new_args)
    elif isinstance(node, list):
        for v in node:
            n += _rewrite(v, new_args)
    return n
