# CrewAI

Call4Agents makes a CrewAI tool act at most once per crew run, tool and exact arguments, and can make
it wait for a person's yes for exactly those arguments.

## Install

```bash
pip install call4agents crewai
```

Tested with crewai 1.15.39 (and the example with 1.15.33), Python 3.12, on Windows.

## Example

```python
import call4agents as c4a
from call4agents.adapters import crewai as c4a_crew
from crewai.tools import tool

c4a.init()                                   # the run record: ./.c4a/ledger.sqlite

@tool("send_email")                          # CrewAI's decorator goes on top
@c4a_crew.effect                             # once per crew run, tool and exact arguments
def send_email(to: str, body: str) -> str:
    """Send an email."""
    ...

crew = Crew(agents=[agent], tasks=[task])    # agent = Agent(..., tools=[send_email])
crew.kickoff()
```

A runnable version with a scripted model (no API key, no network):
`python -m call4agents.examples.crewai_tool`. The model call after the tool fails once, so CrewAI
retries the task (the agent's `max_retry_limit`) and the agent asks for the tool again: 2 emails
without the kit, 1 with it.

## Where the run id comes from

From the crew's `id` (`c4a_crew.run_id(crew)`), which CrewAI puts in the run's context. CrewAI keeps
it when it retries a failed task, and, by its source, when you resume with `Crew.from_checkpoint(...)`
or `kickoff(from_checkpoint=...)` (the id may be set only when it comes from a checkpoint). A new
`Crew(...)` gets a new id and CrewAI does not let you set it. So for a fresh start of the same job
(after a crash, or from a job queue), wrap it: `with c4a.run("<job id>"): crew.kickoff()`, or pass
`run=lambda _: "<id>"`. An active `with c4a.run(...)` wins over the crew id.

CrewAI gives a function tool no context object, so every parameter is part of the key (a parameter
named `context` is text the model wrote, and stays in the key). On a `BaseTool` subclass, put the
decorator on `_run`; `self` is left out.

Plain `c4a.effect` also works inside `with c4a.run(...)`. The adapter adds the crew id as the run id
and a clear error when there is none.

## Approval

```python
send = tool("send_email")(c4a_crew.effect(approval=True)(send_email_fn))
crew = Crew(...)
c4a_crew.approve(send, crew, to="bob", body="hi")   # the yes, for exactly these arguments
crew.kickoff()
```

`approve` / `deny` take the CrewAI tool (from `@tool` or a `BaseTool`) or the wrapped function.

## What is tested

`python -m tests.test_adapters_more --require-frameworks` (real CrewAI, scripted model, offline; a
socket guard refuses and counts any connection or name lookup that leaves the machine: 0 seen):

- CrewAI's own task retry: 2 emails without the kit (control), 1 with the adapter;
- plain `c4a.effect` outside a run is refused ("no current run"); inside `with c4a.run` it gives 1;
- `approval=True`: no yes is refused; a yes for other arguments is refused; a yes for the exact
  arguments sends 1;
- outside a crew and without `c4a.run`: a clear error; a `context: str` parameter stays in the key;
- `call4agents crashtest` on `tests/crashtest_agents/crew_agent.py` (job id from `c4a.run`, the crew
  starts again after a restart): the repeat is found without the guard, none with it.

The adapter checks are mutation-proven (`python -m tests.mutations.run_kit_mutations --adapters-more`).
The crashtest part is not (one CrewAI crashtest takes about 7 minutes here).

## Known limits

- Tested only on the text (ReAct) tool path (`supports_function_calling()` is False in the scripted
  model). The native function-calling path is not tested.
- The crew id across `Crew.from_checkpoint` was checked once by hand with a `BaseTool` tool. With a
  `@tool` function tool, CrewAI 1.15 could not write the checkpoint at all ("Unable to serialize
  unknown type: function"), so that path is not tested.
- CrewAI turns a tool's error into text for the model. A refused call does not stop the crew; the
  model sees the error. Check the run record if you need to know.
- CrewAI writes its own files under the user's app-data folder (named after the current folder).
