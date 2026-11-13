# smolagents

Call4Agents makes a smolagents tool act at most once per job, tool and exact arguments, and can make
it wait for a person's yes for exactly those arguments.

## Install

```bash
pip install call4agents smolagents
```

Tested with smolagents 1.26.0, Python 3.12, on Windows.

## Example

```python
import call4agents as c4a
from call4agents.adapters import smolagents as c4a_smol
from smolagents import ToolCallingAgent, tool

c4a.init()                                   # the run record: ./.c4a/ledger.sqlite

@tool                                        # smolagents' decorator goes on top
@c4a_smol.effect                             # once per job, tool and exact arguments
def send_email(to: str, body: str) -> str:
    """Send an email.

    Args:
        to: Who gets the email.
        body: The text of the email.
    """
    ...

agent = ToolCallingAgent(tools=[send_email], model=...)
with c4a.run("order-42"):                    # your job id; the same when the job is retried
    agent.run("Email bob.")
```

A runnable version with a scripted model class (no API key, no network):
`python -m call4agents.examples.smolagents_tool`. The model call after the tool fails, `agent.run`
raises, and the job runs again: 2 emails without the kit, 1 with it.

## Where the run id comes from

From you: `with c4a.run("<job id>")`, or `run=lambda _: "<id>"`. smolagents has no run, session or
conversation id; without a run id the call is refused with a clear error. A smolagents tool gets no
context object, so every parameter is part of the key. On a `Tool` subclass, put the decorator on
`forward`; `self` is left out.

**CodeAgent:** by default it runs the model's code in a worker thread (for its time limit), and that
thread does not see `with c4a.run(...)` (the thread pool does not copy the context; measured). The
adapter then refuses the call and says why. Pass `run=lambda _: "<id>"`, or build the agent with
`executor_kwargs={"timeout_seconds": None}`. `ToolCallingAgent` copies the context into its tool
threads, so `with c4a.run` works there.

Said plainly: in a `ToolCallingAgent`, plain `c4a.effect` inside `with c4a.run` works the same as the
adapter. The adapter adds `run=`, the clear error and `approve(tool, ...)` for a smolagents Tool.

## Approval

```python
send = tool(c4a_smol.effect(approval=True)(send_email_fn))
with c4a.run("order-42"):
    c4a_smol.approve(send, to="bob", body="hi")  # the yes, for exactly these arguments
    agent.run("Email bob.")
```

## What is tested

`python -m tests.test_adapters_more --require-frameworks` (real smolagents agents, scripted models,
offline; a socket guard refuses and counts any connection or name lookup that leaves the machine:
0 seen):

- ToolCallingAgent, job retried after a failure: 2 emails without the kit (control), 1 with the adapter;
- plain `c4a.effect` outside a run is refused; inside `with c4a.run` it gives 1;
- `approval=True`: no yes is refused; a yes for other arguments is refused; a yes for the exact
  arguments sends 1;
- CodeAgent: 2 without the kit (control); `with c4a.run` is lost in its worker thread and the adapter
  refuses with a message naming the fix; with `run=` it sends 1;
- `call4agents crashtest` on `tests/crashtest_agents/smol_agent.py`: the repeat is found without the
  guard, none with it.

Each check is mutation-proven (`python -m tests.mutations.run_kit_mutations --adapters-more`).

## Known limits

- Managed agents, remote executors (E2B, Docker, Wasm) and streaming runs are not tested. With a
  remote executor the tool runs in another process, where this record and `c4a.run` are not.
- smolagents turns a tool's error into text for the model ("Please try again"). A refused call does
  not stop the run.
- The tests wrap the function first and then call `tool(...)`; stacking `@tool` over the decorator in
  source is the same call but was not run in the tests.
