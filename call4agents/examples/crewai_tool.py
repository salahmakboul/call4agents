# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""CrewAI + Call4Agents, runnable offline (a scripted model, no API key):

    pip install call4agents crewai
    python -m call4agents.examples.crewai_tool

The agent emails bob. The model call after the tool fails once (like a provider timeout), so CrewAI
retries the task (the agent's max_retry_limit) and the agent asks for the tool again. Without the
kit bob gets two emails. With the adapter he gets one: the retry keeps the crew id, the run id.
"""
import os
import tempfile
from pathlib import Path

MODES = ("none", "plain", "plain_in_run", "adapter", "approval_none", "approval_other", "approval_exact")


def main(record: str = "", mode: str = "adapter") -> dict:
    for name, value in (("CREWAI_DISABLE_TELEMETRY", "true"), ("OTEL_SDK_DISABLED", "true"),
                        ("CREWAI_TRACING_ENABLED", "false"), ("CREWAI_DISABLE_TRACKING", "true")):
        os.environ.setdefault(name, value)
    from crewai import Agent, Crew, Task
    from crewai.llms.base_llm import BaseLLM
    from crewai.tools import tool

    import call4agents as c4a
    from call4agents.adapters import crewai as c4a_crew

    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    c4a.init(record or str(Path(tempfile.mkdtemp()) / "ledger.sqlite"))
    outbox, errors = [], []

    class ScriptedLLM(BaseLLM):              # asks for the tool; the call after the tool fails once
        failed: bool = False

        def call(self, messages, tools=None, callbacks=None, available_functions=None, from_task=None,
                 from_agent=None, response_model=None, **kwargs):
            text = messages if isinstance(messages, str) else " ".join(str(m.get("content", "")) for m in messages)
            if "Action: send_email" not in text:
                return 'Thought: I will email bob\nAction: send_email\nAction Input: {"to": "bob", "body": "hi"}'
            if not self.failed:
                self.failed = True
                raise RuntimeError("provider timeout")       # CrewAI retries the whole task
            return "Thought: done\nFinal Answer: done"

        def supports_function_calling(self) -> bool:
            return False                                      # the text (ReAct) tool path

    def send_email(to: str, body: str) -> str:
        """Send an email."""
        outbox.append(to)
        return f"sent to {to}"
    # mode: "adapter" (the way to use it), "none" (no kit: the control), "plain" (c4a.effect without the
    # adapter and without a run), "plain_in_run" (c4a.effect inside `with c4a.run(...)`), "approval_*"
    if mode in ("adapter", "plain_in_run"):
        send_email = c4a_crew.effect(send_email) if mode == "adapter" else c4a.effect(send_email)
    elif mode == "plain":
        send_email = c4a.effect(send_email)
    elif mode.startswith("approval"):
        send_email = c4a_crew.effect(approval=True)(send_email)
    send_tool = tool("send_email")(_errors_seen(send_email, errors))

    agent = Agent(role="mailer", goal="email people", backstory="sends email", llm=ScriptedLLM(model="scripted"),
                  tools=[send_tool], verbose=False)
    crew = Crew(agents=[agent], tasks=[Task(description="email bob", expected_output="done", agent=agent)])
    if mode == "approval_other":
        c4a_crew.approve(send_tool, crew, to="eve", body="hi")    # a yes for other arguments
    elif mode == "approval_exact":
        c4a_crew.approve(send_tool, crew, to="bob", body="hi")
    if mode == "plain_in_run":
        with c4a.run("job-42"):
            crew.kickoff()
    else:
        crew.kickoff()
    return {"emails_sent": len(outbox), "tool_errors": errors, "run_id": c4a_crew.run_id(crew)}


def _errors_seen(f, errors):
    """CrewAI turns a tool's exception into text for the model; keep the type so a test can see why."""
    import functools

    @functools.wraps(f)
    def call(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}"[:200])
            raise
    if hasattr(f, "c4a_name"):
        call.c4a_name, call.c4a_fn, call.c4a_exclude = f.c4a_name, f.c4a_fn, f.c4a_exclude
    return call


if __name__ == "__main__":
    print(main())
