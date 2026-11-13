# Google ADK

Call4Agents makes an ADK tool act at most once per session, tool and exact arguments, and can bind
ADK's tool confirmation to the exact arguments the person saw.

## Install

```bash
pip install call4agents google-adk
```

Tested with google-adk 2.14.0 (the newest release on 12 November 2026), Python 3.12.

## Example

```python
import call4agents as c4a
from call4agents.adapters import google_adk as c4a_adk
from google.adk.agents import LlmAgent
from google.adk.tools import ToolContext

c4a.init()                                   # the run record: ./.c4a/ledger.sqlite

@c4a_adk.effect                              # once per session, tool and exact arguments
def send_email(to: str, body: str, tool_context: ToolContext) -> str:
    """Send an email."""
    ...

agent = LlmAgent(name="mailer", model=..., tools=[send_email])
```

A runnable version with a scripted model (no API key, no network):
`python -m call4agents.examples.google_adk_tool`. It sends the same message twice in one session:
2 emails without the kit, 1 with it.

## Where the run id comes from

From the ADK session in the tool context: app name, user id and session id together
(`c4a_adk.run_id(session)` shows it). We use the session, not the invocation id:

- a resumed invocation (`runner.run_async(..., invocation_id=...)`) runs in the same session;
- a message sent again after a timeout or a crash runs in the same session, but gets a NEW invocation
  id. With the invocation id as run id, that retry would send again (the tests check this).

The cost: in one session, the same tool with the same arguments acts once. For a deliberate second
identical action, pass `c4a_key="..."` at call time.

The tool context is left out of the key. The adapter finds it the way ADK does (a parameter typed as
`ToolContext` / `Context`, or named `tool_context`), so a model argument named `context` stays in the
key. An active `with c4a.run(...)` wins over the session. A tool with no context parameter needs
`with c4a.run(...)` around the run, or it stops with a clear error.

## Tool confirmation

```python
from google.adk.tools import FunctionTool

guarded = c4a_adk.effect(approval=True)(send_email)
tool = FunctionTool(guarded, require_confirmation=True)

# ADK asks with an adk_request_confirmation function call; show its arguments to the person, then:
answer = c4a_adk.confirm(call, True, tools=[tool], session=session)   # or False for a no
runner.run_async(user_id=..., session_id=..., new_message=answer)
```

`confirm()` records `c4a.approve` (or `c4a.deny`) for exactly the arguments in the confirmation
call, then returns the answer for ADK. When the tool runs, Call4Agents checks the yes against the
arguments it really runs with. It refuses a tool not wrapped with `approval=True`, because the yes
would then bind nothing.

## What is tested

`python -m tests.test_adapters_more2 --require-frameworks` (real ADK, scripted model, offline; a
socket guard fails the run on any non-loopback connection):

- same message twice in one session: 2 sends without the kit (control), plain `c4a.effect` fails,
  the adapter sends 1;
- `approval=True`: no yes is refused; a yes for other arguments is refused; a yes for the exact
  arguments sends 1, and a retry does not send again;
- ADK confirmation with `confirm()`: a yes sends 1; the stored call (the model's call and ADK's copy)
  changed to another address after the yes is refused; without the kit the same change sends to the
  new address (control); a no after the yes, with the old yes reaching ADK, is refused;
- `call4agents crashtest` on `tests/crashtest_agents/adk_agent.py` (SQLite session; on restart the app
  sends the message again): a repeat is found without the guard, none with it.

Each check is mutation-proven (`python -m tests.mutations.run_kit_mutations --adapters-more2`).

## Known limits

- Only the session and the arguments ADK passes are known. The changed-call test edits an in-memory
  session; it is not a test of ADK's SQL or Vertex session stores.
- `confirm()` takes the arguments from the confirmation call as the model sent them. If ADK converts
  a value before the call (for example a string into a number for an `int` parameter), the yes may not
  match, and the call is refused (it fails closed).
- A crash during the send leaves the call in doubt: the next try raises `EffectInDoubt` unless you
  pass `reconcile=` (see docs/IDEMPOTENCY.md).
- ADK issue #7148 ("deny not enforced" for custom tools) is closed. In 2.14.0 the source applies the
  confirmation gate to every tool whose `check_require_confirmation` returns True, and a no returns
  "This tool call is rejected." We read this in the source; we did not test custom `BaseTool` classes
  that ask for confirmation only from inside `run_async`. With `approval=True`, a no recorded by
  `confirm()` is enforced by Call4Agents in any case.
- Live (streaming) tools and long-running tools are not tested.
