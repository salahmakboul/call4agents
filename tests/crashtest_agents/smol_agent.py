"""A small real smolagents ToolCallingAgent with a scripted model, for `call4agents crashtest`.
AGENT_GUARD=1 adds the one-line fix. smolagents has no run id, so the job id comes from `with c4a.run(...)`;
after a restart the job runs again from the start, the way a job queue retries it."""
import os
from pathlib import Path

from smolagents import ToolCallingAgent, tool
from smolagents.models import ChatMessage, ChatMessageToolCall, ChatMessageToolCallFunction, MessageRole, Model

import call4agents as c4a
from call4agents.adapters import smolagents as c4a_smol

c4a.init("record.sqlite", secret=b"crashtest-agent-0")


class ScriptedModel(Model):
    def generate(self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kwargs):
        done = any((m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == MessageRole.TOOL_RESPONSE
                   for m in messages)
        name, args = ("final_answer", {"answer": "done"}) if done else ("send_email", {"to": "bob"})
        call = ChatMessageToolCall(function=ChatMessageToolCallFunction(name=name, arguments=args),
                                   id=f"call-{len(messages)}", type="function")
        return ChatMessage(role=MessageRole.ASSISTANT, content="", tool_calls=[call])


def send_email(to: str) -> str:
    """Send an email.

    Args:
        to: Who gets the email.
    """
    with open("outbox.txt", "a") as f:
        f.write(f"email to {to}\n")
    return "sent"


guard = c4a_smol.effect if os.environ.get("AGENT_GUARD") == "1" else (lambda f: f)
agent = ToolCallingAgent(tools=[tool(guard(send_email))], model=ScriptedModel(), verbosity_level=-1, max_steps=3)

if not Path("done.txt").exists():                    # the job finished before this start
    with c4a.run("job-1"):
        agent.run("email bob")
    Path("done.txt").write_text("done\n")
