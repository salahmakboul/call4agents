# Microsoft Agent Framework (Python)

Call4Agents makes an Agent Framework tool act at most once per session, tool and exact arguments, and
can bind the framework's tool approval to the exact arguments the person saw.

## Install

```bash
pip install call4agents agent-framework-core
```

Tested with agent-framework-core 1.25.0 (the newest release on 12 November 2026), Python 3.12.

## Example

```python
import call4agents as c4a
from call4agents.adapters import agent_framework as c4a_maf
from agent_framework import Agent, FunctionInvocationContext, tool

c4a.init()                                   # the run record: ./.c4a/ledger.sqlite

@tool
@c4a_maf.effect                              # once per session, tool and exact arguments
def send_email(to: str, body: str, ctx: FunctionInvocationContext) -> str:
    """Send an email."""
    ...

agent = Agent(client=..., tools=[send_email])
session = agent.create_session(session_id="order-42")
await agent.run("Email bob.", session=session)
```

A runnable version with a scripted chat client (no API key, no network):
`python -m call4agents.examples.agent_framework_tool`. It sends the same message twice in one
session: 2 emails without the kit, 1 with it.

## Where the run id comes from

From `ctx.session.session_id` (`c4a_maf.run_id(session)`). The session id is kept when you save and
load the session (`to_dict` / `from_dict`), so a retried run finds its earlier effects. In workflows,
the agent executor stores its session in the checkpoint and restores it (we read this in the source of
1.25.0; it is not tested here).

Pass `session=` to `agent.run`. Without it, the framework may make a new session for each run (we saw
this when the agent has context providers): each run then has a new run id, and a retry can act again.
With no session at all the tool stops with a clear error. An active `with c4a.run(...)` wins.

The `FunctionInvocationContext` parameter is left out of the key. The adapter finds it by its type,
like the framework, so a model argument named `ctx` stays in the key. One case is not covered: an
unannotated `ctx` with an explicit `input_model=`; it stays in the key and the call fails loudly.

## Tool approval

```python
guarded = tool(c4a_maf.effect(approval=True)(send_email), approval_mode="always_require")
agent = Agent(client=..., tools=[guarded])

result = await agent.run("Email bob.", session=session)
request = result.user_input_requests[0]          # show request.function_call to the person, then:
answer = c4a_maf.respond(request, True, tools=[guarded], session=session)   # or False for a no
await agent.run(Message(role="user", contents=[answer]), session=session)
```

`respond()` records `c4a.approve` (or `c4a.deny`) for exactly the arguments in the request, then
returns the framework's approval response. When the tool runs, Call4Agents checks the yes against the
arguments it really runs with. It refuses a tool not wrapped with `approval=True`.

## What is tested

`python -m tests.test_adapters_more2 --require-frameworks` (real Agent Framework, scripted client,
offline; a socket guard fails the run on any non-loopback connection):

- same message twice in one session: 2 sends without the kit (control), plain `c4a.effect` fails,
  the adapter sends 1;
- `approval=True`: no yes is refused; a yes for other arguments is refused; a yes for the exact
  arguments sends 1, and a retry does not send again;
- `approval_mode` with `respond()`: a yes sends 1; the saved session and the queued answer changed to
  another address after the yes is refused; without the kit the same change sends to the new address
  (control); a no after the yes, with the old yes delivered, is refused;
- `call4agents crashtest` on `tests/crashtest_agents/maf_agent.py` (session saved to a file after each
  run; on restart the app sends the message again): a repeat is found without the guard, none with it.

Each check is mutation-proven (`python -m tests.mutations.run_kit_mutations --adapters-more2`).

## Known limits

- The framework hands a tool's error to the model as a function result. A refused call does not stop
  the run; the model sees an error. Check the run record, or the result, if you need to know.
- The changed-call test edits a saved session and the queued answer. Workflow checkpoints are not
  tested here; the crash and edit cases with checkpoints are in docs/finding/more_frameworks.
- A crash during the send leaves the call in doubt: the next try raises `EffectInDoubt` (returned to
  the model as an error) unless you pass `reconcile=` (see docs/IDEMPOTENCY.md).
- Hosted (provider-run) tools and streaming runs are not tested.
