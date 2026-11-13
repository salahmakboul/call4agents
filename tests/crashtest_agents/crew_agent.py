"""A small real CrewAI crew with a scripted model, for `call4agents crashtest`. AGENT_GUARD=1 adds the one-line fix.
A new Crew gets a new id after a restart, so the job id comes from `with c4a.run(...)`."""
import os

for _name, _value in (("CREWAI_DISABLE_TELEMETRY", "true"), ("OTEL_SDK_DISABLED", "true"),
                      ("CREWAI_TRACING_ENABLED", "false"), ("CREWAI_DISABLE_TRACKING", "true")):
    os.environ.setdefault(_name, _value)
from crewai import Agent, Crew, Task
from crewai.llms.base_llm import BaseLLM
from crewai.tools import tool

import call4agents as c4a
from call4agents.adapters import crewai as c4a_crew

c4a.init("record.sqlite", secret=b"crashtest-agent-0")


class ScriptedLLM(BaseLLM):
    def call(self, messages, tools=None, callbacks=None, available_functions=None, from_task=None,
             from_agent=None, response_model=None, **kwargs):
        text = messages if isinstance(messages, str) else " ".join(str(m.get("content", "")) for m in messages)
        if "Action: send_email" not in text:
            return 'Thought: I will email bob\nAction: send_email\nAction Input: {"to": "bob"}'
        return "Thought: done\nFinal Answer: done"

    def supports_function_calling(self) -> bool:
        return False


def send_email(to: str) -> str:
    """Send an email."""
    with open("outbox.txt", "a") as f:
        f.write(f"email to {to}\n")
    return "sent"


guarded = c4a_crew.effect(send_email) if os.environ.get("AGENT_GUARD") == "1" else send_email
agent = Agent(role="mailer", goal="email people", backstory="sends email", llm=ScriptedLLM(model="scripted"),
              tools=[tool("send_email")(guarded)], verbose=False)
crew = Crew(agents=[agent], tasks=[Task(description="email bob", expected_output="done", agent=agent)])

if not os.path.exists("done.txt"):                 # the job finished before this start
    with c4a.run("job-1"):
        crew.kickoff()
    with open("done.txt", "w") as f:
        f.write("done\n")
