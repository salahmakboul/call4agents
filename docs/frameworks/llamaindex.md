# LlamaIndex

Call4Agents makes a LlamaIndex function tool act at most once per job, tool and exact arguments, and
can make it wait for a person's yes for exactly those arguments.

## Install

```bash
pip install call4agents llama-index-core
```

Tested with llama-index-core 0.14.25 (llama-index-workflows 2.26.1), Python 3.12, on Windows.

## Example

```python
import call4agents as c4a
from call4agents.adapters import llamaindex as c4a_li
from llama_index.core.tools import FunctionTool
from llama_index.core.workflow import Context

c4a.init()                                   # the run record: ./.c4a/ledger.sqlite

@c4a_li.effect                               # once per job, tool and exact arguments
async def send_email(ctx: Context, to: str, body: str) -> str:
    """Send an email."""
    ...

agent = FunctionAgent(tools=[FunctionTool.from_defaults(send_email)], llm=...)
with c4a.run("order-42"):                    # your job id; the same for the resume
    await agent.run(ctx=Context.from_dict(agent, saved))
```

A runnable version with LlamaIndex's own `MockFunctionCallingLLM` (no API key, no network):
`python -m call4agents.examples.llamaindex_tool`. The app saves the Context when a tool is about to
run, the run dies after the email, and the app resumes from the saved Context. LlamaIndex runs the
steps that were in progress again: 3 emails without the kit (1 + 2 in the resumed run), 1 with it.

## Where the run id comes from

From you: `with c4a.run("<job id>")` around the run and the resume, or `run=lambda ctx: "<id>"`.
LlamaIndex has no id that survives a resume: each run, also one resumed from a saved Context, gets a
new workflow run id, and one process cannot start a run with an id it used before ("Workflow run
with run_id ... already exists"). So the adapter does not fall back to it; without a run id the
call is refused with a clear error.

The `Context` parameter is found the way LlamaIndex finds it (annotated `Context` or
`Context[...]`, anywhere in the signature) and left out of the key. A parameter named `ctx` that is
not a `Context` stays in the key.

Plain `c4a.effect` fails on a tool with a `Context` parameter (a Context has no stable text form).
On a tool without one, plain `c4a.effect` inside `with c4a.run` works the same as the adapter.

## Approval

```python
send = c4a_li.effect(approval=True)(send_email_fn)
with c4a.run("order-42"):
    c4a.approve(send, to="bob", body="hi")       # the yes, for exactly these arguments
```

## What is tested

`python -m tests.test_adapters_more --require-frameworks` (real LlamaIndex FunctionAgent, its mock
LLM, offline; a socket guard refuses and counts any connection or name lookup that leaves the
machine: 0 seen):

- resume from a Context saved before the tool: 3 emails without the kit (control), 1 with the adapter;
- plain `c4a.effect` with a `Context` parameter is refused (TypeError); without one it gives 1;
- the adapter without `c4a.run` is refused with a clear error (no silent shared run);
- `approval=True`: no yes is refused; a yes for other arguments is refused; a yes for the exact
  arguments sends 1;
- a `ctx: str` parameter stays in the key; `run=` gives the run id;
- `call4agents crashtest` on `tests/crashtest_agents/li_agent.py` (Context saved to a file before each
  tool call, resumed after a restart): the repeat is found without the guard, none with it.

Each check is mutation-proven (`python -m tests.mutations.run_kit_mutations --adapters-more`).

## Known limits

- Only `FunctionAgent` with `FunctionTool` is tested. `ReActAgent`, `CodeActAgent`, multi-agent
  workflows and custom workflow steps are not.
- A crash during the send leaves the call in doubt: the next try raises `EffectInDoubt` (LlamaIndex
  gives it to the model as an error) unless you pass `reconcile=` (see docs/IDEMPOTENCY.md).
- LlamaIndex turns a tool's error into text for the model. A refused call does not stop the run.
