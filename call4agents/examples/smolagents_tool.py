# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""smolagents + Call4Agents, runnable offline (a scripted model class, no API key):

    pip install call4agents smolagents
    python -m call4agents.examples.smolagents_tool

A ToolCallingAgent emails bob. The model call after the tool fails (like a provider timeout), so
agent.run raises and the job is run again, the way a job queue retries a failed job. Without the kit
bob gets two emails. With the adapter and `with c4a.run(<job id>)` around each try he gets one.
The code_* modes show the CodeAgent case: its code runs in a worker thread that does not see
`with c4a.run`, so the adapter refuses the call unless run= gives the id.
"""
import tempfile
from pathlib import Path

MODES = ("none", "plain", "plain_in_run", "adapter", "approval_none", "approval_other", "approval_exact",
         "code_none", "code_adapter_run_lost", "code_adapter_run_arg")


def send_email(to: str, body: str) -> str:
    """Send an email.

    Args:
        to: Who gets the email.
        body: The text of the email.
    """
    OUTBOX.append(to)
    return f"sent to {to}"


OUTBOX: list = []


def main(record: str = "", mode: str = "adapter") -> dict:
    from smolagents import CodeAgent, ToolCallingAgent, tool
    from smolagents.models import ChatMessage, ChatMessageToolCall, ChatMessageToolCallFunction, MessageRole, Model

    import call4agents as c4a
    from call4agents.adapters import smolagents as c4a_smol

    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    c4a.init(record or str(Path(tempfile.mkdtemp()) / "ledger.sqlite"))
    OUTBOX.clear()
    failed, errors = [], []

    def after_tool(text: str):
        if not failed:
            failed.append(1)
            raise RuntimeError("provider timeout")      # agent.run raises; the job is retried

    class ScriptedToolModel(Model):           # asks for the tool; the call after the tool fails once
        def generate(self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kwargs):
            text = str(messages)
            if "send_email" in text and ("sent to bob" in text or "Error" in text):
                after_tool(text)
                name, args = "final_answer", {"answer": "done"}
            else:
                name, args = "send_email", {"to": "bob", "body": "hi"}
            call = ChatMessageToolCall(function=ChatMessageToolCallFunction(name=name, arguments=args),
                                       id=f"call-{len(text)}", type="function")
            return ChatMessage(role=MessageRole.ASSISTANT, content="", tool_calls=[call])

    class ScriptedCodeModel(Model):           # the same, as code for a CodeAgent
        def generate(self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kwargs):
            text = str(messages)
            if "sent to bob" in text or "Error" in text:
                after_tool(text)
                code = 'final_answer("done")'
            else:
                code = 'print(send_email(to="bob", body="hi"))'
            return ChatMessage(role=MessageRole.ASSISTANT, content=f"Thought: email bob\n<code>\n{code}\n</code>")

    fn = send_email
    # mode: "adapter" (the way to use it), "none" (no kit: the control), "plain" (c4a.effect without the
    # adapter and without a run), "plain_in_run" (c4a.effect inside `with c4a.run(...)`), "approval_*",
    # "code_*" (a CodeAgent)
    if mode in ("adapter", "code_adapter_run_lost"):
        fn = c4a_smol.effect(fn)
    elif mode == "code_adapter_run_arg":
        fn = c4a_smol.effect(run=lambda _: "job-42")(fn)
    elif mode in ("plain", "plain_in_run"):
        fn = c4a.effect(fn)
    elif mode.startswith("approval"):
        fn = c4a_smol.effect(approval=True)(fn)
    send_tool = tool(_errors_seen(fn, errors))
    if mode == "approval_other":
        with c4a.run("job-42"):
            c4a_smol.approve(send_tool, to="eve", body="hi")      # a yes for other arguments
    elif mode == "approval_exact":
        with c4a.run("job-42"):
            c4a_smol.approve(send_tool, to="bob", body="hi")

    def job():
        if mode.startswith("code"):
            agent = CodeAgent(tools=[send_tool], model=ScriptedCodeModel(), verbosity_level=-1, max_steps=3)
        else:
            agent = ToolCallingAgent(tools=[send_tool], model=ScriptedToolModel(), verbosity_level=-1, max_steps=3)
        agent.run("email bob")

    for attempt in (1, 2):                    # the first try fails after the tool; the queue retries it
        try:
            if mode == "plain":
                job()
            else:
                with c4a.run("job-42"):       # the job id, the same on the retry
                    job()
            break
        except Exception as exc:  # noqa: BLE001 - a failed try is retried once
            errors.append(f"try {attempt}: {type(exc).__name__}")
    return {"emails_sent": len(OUTBOX), "tool_errors": errors}


def _errors_seen(f, errors):
    """smolagents turns a tool's exception into text for the model; keep it so a test can see why."""
    import functools

    @functools.wraps(f)
    def call(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}"[:240])
            raise
    return call


if __name__ == "__main__":
    print(main())
