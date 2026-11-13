# API reference

This page is generated from the code by `python docs/make_api.py`. Do not edit it by hand; CI fails if
it is out of date. Import the package as `import call4agents as c4a`.

## Stable

Covered by the 1.0 promise (see PLAN_1_0.md).

### `init`

```python
init(path: str = '.c4a/ledger.sqlite', secret: Optional[bytes] = None, replay_from: Optional[str] = None, old_secrets: Optional[list] = None, allow_network_drive: bool = False) -> call4agents.ledger.Ledger
```

Open (or create) the run record and the approval secret. Call it once, before any action.

path: a SQLite file (default .c4a/ledger.sqlite; the secret goes next to it in .c4a/secret).
secret: the approval secret as bytes; pass it when several processes or machines share one record.
replay_from: a recorded run record. Model calls and effects then come from it (offline).
old_secrets: secrets you rotated away from; approvals they signed still count (C4A_OLD_SECRETS works too).
path "postgresql://...": one run record shared by several machines (needs call4agents[postgres] and the
same secret on every machine).

### `run`

```python
run(run_id: str)
```

`with c4a.run("thread-42"):` - every action and approval inside belongs to this run. Use the id your
framework resumes with (a thread, conversation or session id), so a resumed run finds its record.
New threads do not inherit it: enter c4a.run there, or use contextvars.copy_context().run.

### `effect`

```python
effect(fn: Optional[Callable] = None, *, approval: bool = False, name: Optional[str] = None, reconcile: Optional[Callable[..., Any]] = None, key: Optional[Callable[..., str]] = None, wait: float = 2.0, idempotent_for: Optional[float] = None, retry_on: tuple = (), exclude: tuple = (), undo: Optional[Callable[..., Any]] = None, retries: int = 0, backoff: float = 0.5, limit: Optional[ForwardRef('Limit')] = None, rules: Any = None, lease: float = 600.0)
```

Mark a function that changes the outside world.

- Once-only per (run, tool, exact arguments): a repeat returns the recorded result.
- Crash between intent and result: EffectInDoubt, unless reconcile(**args) returns the
  result (the effect happened) or None (it did not; then it runs once). Experimental: it can
  return c4a.UNKNOWN when the outside world cannot tell yet; the call then stays in doubt
  (EffectInDoubt, nothing recorded, nothing run) and the next try asks reconcile again.
- reconcile= and idempotent_for= are used only when the attempt's owner is provably not running it
  any more (Fix 17): a dead process on this host, or this process with the call no longer in progress.
  An owner on another machine, or a live process on this host, counts as running for `lease` seconds
  (default 600) after the attempt started; until then the call raises EffectInDoubt instead of asking
  the provider, because a call still running gets a truthful "not done yet". Set lease= to the longest
  time one attempt of this action can take.
- approval=True: runs only if c4a.approve(...) recorded a "yes" for these exact arguments.
  One "yes" covers ONE action: a retry or resume of the same action reuses it, a deliberate
  second action (new c4a_key) needs a new "yes". c4a.deny(...) cancels earlier "yes" answers,
  also for an action that is in doubt. Callers racing with a single "yes": one runs, the
  others get ApprovalRequired (nothing runs twice).
- Explicit key: pass c4a_key="..." at call time, or key=lambda **args: ... here.
  A deliberate second identical action uses a new key. Default: same run + tool + arguments.
- Callers racing on the same effect: exactly one runs it; the others wait up to `wait`
  seconds for its result, then raise EffectInDoubt.
- idempotent_for=SECONDS: the function passes c4a.idempotency_key() to an outside API
  that removes duplicate requests with the same key, for that long (Stripe v1: 24 h). An
  in-doubt effect is then retried with the SAME key instead of raising (each later call
  within that time may resend it; the API, not luck, prevents the double charge). After that
  time the key may be forgotten by the API, so the effect stays in doubt (a late retry could
  charge twice).
- The function raises: the action may or may not have happened (a timeout after the charge
  raises too), so the next call raises EffectInDoubt at once, naming the error. retry_on=
  (SomeError, ...) lists errors that prove nothing happened: after those, the next call runs
  the action again.
- retries=N (experimental): after an error, try again up to N times in the same call, waiting
  backoff * 2**n seconds (at most 30), but ONLY when the record decides the next try: the error
  is in retry_on (nothing happened), or idempotent_for is set (the resend uses the same key), or
  reconcile= is set (the provider is asked first). Any other error is raised at once, never
  retried blind; a try that comes out in doubt stops the retries with EffectInDoubt. Each try is
  recorded with its error.
- The tool name is module + qualified name; for methods, `self` is not part of the key (pass
  c4a_key= to tell two objects apart). Arguments without a stable text form raise TypeError.
- exclude=("ctx",): parameters left out of the key (a framework's context object, which changes
  on every call). The framework adapters set this for you.
- undo=fn: how to reverse this action. fn gets the same arguments (and `result=` if it accepts
  it). c4a.undo() runs the undo of each finished action in a run, newest first, each once only.
- limit=c4a.Limit(...) (experimental): a limit shared by every process on the record.
- rules=c4a.Rules.load(path) or rules=policy (experimental): checked against the bound arguments before a new
  attempt is admitted; RuleRefused when a rule says no (nothing runs), approval needed when it says "ask".
  See call4agents/rules.py.

### `approve`

```python
approve(tool: Any, **args: Any) -> str
```

Record a "yes" for this tool with exactly these arguments (in the current run).

### `deny`

```python
deny(tool: Any, **args: Any) -> str
```

Record a "no" for this tool with exactly these arguments: earlier "yes" answers no longer count.

### `current_ledger`

```python
current_ledger() -> call4agents.ledger.Ledger
```

The record opened by c4a.init(). RuntimeError if init() was not called.

### `idempotency_key`

```python
idempotency_key() -> str
```

Inside an @effect: the stable key of this effect - the same on every retry, resume or
re-run of the same (run, tool, arguments). Send it to the outside API.

### `idempotency_headers`

```python
idempotency_headers(header: str = 'Idempotency-Key') -> Dict[str, str]
```

{header: key} for HTTP APIs (Stripe/Adyen: Idempotency-Key; PayPal: PayPal-Request-Id).

### `ApprovalRequired`

```python
class ApprovalRequired(tool: str, args: Dict[str, Any]) -> None
```

The effect needs a "yes" for exactly these arguments, and none matches.

### `EffectInDoubt`

```python
class EffectInDoubt(tool: str, args: Dict[str, Any], why: str = 'crash during the effect') -> None
```

The intent was recorded but no result: the process died DURING the effect.
We cannot know if it happened, so we refuse to repeat it (no silent duplicate).

### `KeyConflict`

```python
class KeyConflict(tool: str, name: str, recorded: Any, new: Dict[str, Any]) -> None
```

A named action (c4a_key= or key=) was called again with arguments that mean something else.
Nothing ran: an explicit key names ONE action, like an idempotency key at a payment API.

### `UNKNOWN`

```
UNKNOWN = c4a.UNKNOWN
```

What a reconcile function returns when the outside world cannot tell yet (a search that lags
behind writes, a transaction still pending). Nothing is recorded, nothing runs, and the call stays
in doubt: the next try asks reconcile again. Returning None would mean "it did not happen" and run
the action once more, so None must only be returned when the provider can really confirm it.

## Experimental

Works and is tested; the exact form may still change in a 1.x release.

### `pause`

```python
pause(tool: Any, *, ttl: float = 86400.0, **args: Any) -> str
```

Experimental. Record that the current run waits for a person to decide on exactly this call, and
return a ticket to show them with the call. Only c4a.decide(ticket, ...) turns it into a "yes" or "no",
and only for these arguments: the ticket holds none, so nothing the client sends back can change them.

### `decide`

```python
decide(ticket: str, decision: str, *, by: str = '') -> Dict[str, Any]
```

Experimental. "approve" or "deny" the call a ticket was issued for, recording who decided.
Refused (TicketRefused) if this record did not issue the ticket, or it was changed, expired or already
decided. Returns the run, tool and arguments that were decided.

### `TicketRefused`

```python
class TicketRefused
```

A decision came with a ticket this record did not issue, or one that was edited, expired or already used.

### `Limit`

```python
class Limit(name: str, maximum: float, cost: Optional[Callable[..., float]] = None, per: Optional[float] = None) -> None
```

A limit shared by every process that uses the same record (experimental).

    refunds = c4a.Limit("refunds", 500, cost=lambda amount, **_: amount, per=86400)
    @c4a.effect(limit=refunds)
    def refund(order, amount): ...

Each action admitted under `name` holds its cost (default 1, so `maximum` counts actions) from the moment its
intent is written. The check and the hold are one transaction with the intent, so workers racing for the last
unit cannot both get it. The cost is given back only when the kit proves the action did not happen (an error in
retry_on, or reconcile= saying so). An action in doubt keeps holding it. per=SECONDS counts only holds made in
that window. Every process must use the same name, maximum and per: each checks its own.

### `LimitReached`

```python
class LimitReached(name: str, used: float, cost: float, maximum: float) -> None
```

A shared limit would be exceeded. Nothing ran and nothing was recorded (an approval stays unspent).

### `Rules`

```python
class Rules(tools: 'Dict[str, Any]', source: 'str' = 'inline') -> 'None'
```

Per-tool rules from a file or a dict. See the module docstring.

### `RuleRefused`

```python
class RuleRefused(tool: 'str', args: 'Dict[str, Any]', rule: 'str', why: 'str') -> 'None'
```

A rule refused this call. Nothing ran and no intent was recorded.

## Testing and inspection tools

Useful around the once-only promise; not part of it.

### `recorded`

```python
recorded(fn: Optional[Callable] = None, *, name: Optional[str] = None, encode: Optional[Callable] = None, decode: Optional[Callable] = None)
```

Record a model call's arguments and result; in replay mode return the recording instead.
Results keep their type (call4agents.codec); a type it cannot keep raises NotRecordable at
record time unless you pass encode= (value -> JSON) and decode= (JSON -> value).

### `diff`

```python
diff(path_a: str, path_b: str, run_id: Optional[str] = None) -> Dict[str, Any]
```

(no docstring)

### `prove`

```python
prove(check: str, mutations: List[Dict[str, str]], cwd: str = '.') -> Dict[str, Any]
```

(no docstring)

### `undo`

```python
undo(run_id: Optional[str] = None) -> Dict[str, list]
```

Reverse the finished actions of a run, newest first, with the undo= each one registered.

Each undo is itself a once-only action (keyed by the action it reverses), so calling undo() again
after a crash continues where it stopped and never reverses the same action twice. Actions with no
undo= are listed in "no_undo", not silently skipped. The tools' modules must be imported (their
decorators register the undo functions). Returns {"undone": [...], "no_undo": [...]}, newest first.

### `UndoFailed`

```python
class UndoFailed(tool: str, error: BaseException, undone: list) -> None
```

An undo raised. Undos already done stay done; this one is in doubt (it may have run), and
the older actions were NOT undone. Resolve it (`call4agents doubt` / `resolve`), then call undo again.

### `ReplayMismatch`

```python
class ReplayMismatch
```

(no docstring)

### `ReplayMissing`

```python
class ReplayMissing
```

(no docstring)

### `Ledger`

```python
class Ledger(path: str = '.c4a/ledger.sqlite', secret: Optional[bytes] = None, old_secrets: Optional[List[bytes]] = None, allow_network_drive: bool = False) -> None
```

(no docstring)

## Framework adapters

Each adapter leaves the framework's context object out of the key and finds the run id for you.

### `call4agents.adapters.langgraph`

LangGraph / LangChain adapter.

The run id is LangGraph's thread id, so a resumed thread finds its own effects and approvals.

    from call4agents.adapters import langgraph as c4a_lg

    @c4a_lg.effect(approval=True)       # inside any node or tool
    def pay(amount: int, to: str): ...

    # when the human says yes to the interrupt, bind the yes to the arguments they SAW:
    c4a_lg.approve(pay, config, amount=10, to="alice")
    graph.invoke(Command(resume=...), config)

#### `langgraph.thread_id`

```python
thread_id(config: Optional[Dict[str, Any]]=None)
```

(no docstring)

#### `langgraph.effect`

```python
effect(fn: Optional[Callable]=None, **options: Any)
```

c4a.effect, with the run id taken from the current LangGraph thread.

#### `langgraph.approve`

```python
approve(tool: Any, config: Dict[str, Any], **args: Any)
```

Record the human's yes for exactly these arguments, in this thread.

#### `langgraph.deny`

```python
deny(tool: Any, config: Dict[str, Any], **args: Any)
```

Record the human's no for exactly these arguments, in this thread (cancels an earlier yes).

#### `langgraph.pause`

```python
pause(tool: Any, config: Optional[Dict[str, Any]]=None, *, ttl: float=86400.0, **args: Any)
```

Experimental. An approval ticket for exactly this call, in this thread. Put it in the interrupt
payload; the person's answer goes to c4a.decide(ticket, "approve" | "deny") before you resume. When the
node runs again on resume, the same undecided ticket comes back, so one decision answers it.

    def ask(state, config):
        ticket = c4a_lg.pause(pay, config, **state["call"])
        interrupt({"call": state["call"], "ticket": ticket})

### `call4agents.adapters.pydantic_ai`

Pydantic AI adapter.

    from call4agents.adapters import pydantic_ai as c4a_pai

    @agent.tool                      # Pydantic AI's decorator goes on top
    @c4a_pai.effect                  # once per conversation, tool and exact arguments
    def send_email(ctx: RunContext[Deps], to: str, body: str) -> str: ...

The run id is the run's `conversation_id`, which Pydantic AI keeps when you resume with the saved
message history (or pass conversation_id= yourself). `ctx` is left out of the key. An active
`with c4a.run(...)` wins over the conversation id.

#### `pydantic_ai.effect`

```python
effect(fn: Optional[Callable]=None, **options: Any)
```

c4a.effect for a Pydantic AI tool (with or without a RunContext first parameter).

#### `pydantic_ai.tickets`

```python
tickets(result: Any, tools: Any, *, ttl: float=86400.0)
```

Experimental. For a run that ended with DeferredToolRequests: one approval ticket per waiting call,
{tool_call_id: ticket}. Show each ticket with its call; send the person's decisions to results().
`tools`: the agent's tools wrapped with c4a_pai.effect(approval=True).

#### `pydantic_ai.results`

```python
results(decisions: Dict[str, str], *, by: str='')
```

Experimental. {ticket: "approve" | "deny"} -> DeferredToolResults for the next run. The tool call ids
come from THIS record, not from the client, and each ticket is decided once (c4a.decide).

### `call4agents.adapters.openai_agents`

OpenAI Agents SDK adapter.

    from call4agents.adapters import openai_agents as c4a_oa

    @function_tool                   # the SDK's decorator goes on top
    @c4a_oa.effect                   # once per run id, tool and exact arguments
    def send_email(ctx: RunContextWrapper[MyContext], to: str, body: str) -> str: ...

    with c4a.run(session_id):        # e.g. your SQLiteSession's id
        await Runner.run(agent, "...", session=session)

The run id comes from an active `with c4a.run(...)`. Without one, it is read from the run's context
object: a string, or a dict / object with `c4a_run_id`, `conversation_id`, `session_id` or
`thread_id`. The ctx parameter is left out of the key.

#### `openai_agents.effect`

```python
effect(fn: Optional[Callable]=None, **options: Any)
```

c4a.effect for an OpenAI Agents SDK function tool (with or without a context first parameter).

#### `openai_agents.tickets`

```python
tickets(result: Any, tools: Any, *, run_id: Optional[str]=None, ttl: float=86400.0)
```

Experimental. For a run that stopped on approvals (`result.interruptions`): one approval ticket per
waiting call, {call_id: ticket}. Show each ticket with its call; give the person's answers to apply().
`tools`: the agent's tools wrapped with c4a_oa.effect(approval=True). The run id is `run_id`, an active
`with c4a.run(...)`, or the run's context (as for effect).

#### `openai_agents.apply`

```python
apply(state: Any, decisions: Dict[str, str], *, by: str='')
```

Experimental. {ticket: "approve" | "deny"} -> state.approve / state.reject for exactly the waiting call
each ticket was issued for (the call id comes from THIS record). Returns the state for Runner.run(agent, state).

### `call4agents.adapters.mcp`

MCP server adapter (the official `mcp` Python SDK).

    from call4agents.adapters import mcp as c4a_mcp

    @server.tool()                   # the SDK's decorator goes on top
    @c4a_mcp.effect                  # once per MCP session, tool and exact arguments
    def create_ticket(title: str, ctx: Context) -> str: ...

Why: an MCP client that loses a response often sends the same tools/call again. With this adapter
the repeat returns the recorded result instead of creating a second ticket.

The run id comes from an active `with c4a.run(...)`, else from the `Mcp-Session-Id` header of the
streamable-HTTP session. On stdio there is no session id: pass run=lambda ctx: "<id>" (for example
a client or user id) or enter `c4a.run(...)` yourself. The Context parameter is left out of the key.

#### `mcp.effect`

```python
effect(fn: Optional[Callable]=None, **options: Any)
```

c4a.effect for an MCP server tool. The Context parameter may be anywhere in the signature.

### `call4agents.adapters.pytest_plugin`

pytest plugin (experimental): `call4agents crashtest` inside your test suite.

    def test_refund_survives_a_crash(c4a_crashtest):
        c4a_crashtest("agents/refund_agent.py", watch=["refund"], test_accounts=True)

The fixture runs the script once cleanly, then kills it before and after each action and each save, restarts it,
and fails the test if an action ran twice or a call ran that the clean run never made. It returns the report.

Every action really runs, many times. The fixture refuses to start until you pass test_accounts=True, which says
the script talks only to fakes, sandboxes or test accounts. Installed with call4agents; pytest loads it by itself.

#### `pytest_plugin.c4a_crashtest`

```python
c4a_crashtest()
```

(no docstring)

### `call4agents.adapters.google_adk`

Google ADK adapter.

    from call4agents.adapters import google_adk as c4a_adk

    @c4a_adk.effect                  # once per session, tool and exact arguments
    def send_email(to: str, body: str, tool_context: ToolContext) -> str: ...

    agent = LlmAgent(..., tools=[send_email])

The run id is the ADK session (app name, user id, session id). A resumed invocation, and a message sent
again after a timeout, run in the same session; the invocation id is new for each new message, so it
would let a re-sent message act again. The tool context is left out of the key. An active
`with c4a.run(...)` wins over the session.

ADK tool confirmation (`FunctionTool(..., require_confirmation=True)`): wrap the tool with
`c4a_adk.effect(approval=True)` and build the person's answer with `confirm(...)`. It records the yes
(or no) in call4agents for exactly the arguments shown in the `adk_request_confirmation` call, so a
call changed after the yes is refused when it runs.

#### `google_adk.run_id`

```python
run_id(session: Any)
```

The run id for an ADK session (a Session, or a tool / callback context). A string is used as it is.

#### `google_adk.effect`

```python
effect(fn: Optional[Callable]=None, **options: Any)
```

c4a.effect for an ADK function tool (with or without a ToolContext parameter).

#### `google_adk.confirm`

```python
confirm(call: Any, confirmed: bool, *, tools: Any, session: Any=None)
```

The person's answer to an `adk_request_confirmation` call, as the Content to send back to ADK.
First records c4a.approve (or c4a.deny) for exactly the arguments in that call, in this session.
`tools`: the agent's tools, wrapped with c4a_adk.effect(approval=True).

### `call4agents.adapters.agent_framework`

Microsoft Agent Framework (Python) adapter.

    from call4agents.adapters import agent_framework as c4a_maf

    @tool
    @c4a_maf.effect                  # once per session, tool and exact arguments
    def send_email(to: str, body: str, ctx: FunctionInvocationContext) -> str: ...

    session = agent.create_session(session_id="order-42")
    await agent.run("...", session=session)

The run id is the AgentSession's `session_id`: it is kept when the session is saved and loaded again
(to_dict / from_dict) and in workflow checkpoints, so a retried or restored run finds its effects. The
FunctionInvocationContext parameter is left out of the key. An active `with c4a.run(...)` wins.
Without session=, Agent Framework may make a new session for each run: then each run is a new run id.

Tool approval (`@tool(approval_mode="always_require")`): wrap the tool with
`c4a_maf.effect(approval=True)` and turn the person's answer into the response with `respond(...)`. It
records the yes (or no) in call4agents for exactly the arguments in the approval request, so a call
changed after the yes is refused when it runs.

#### `agent_framework.run_id`

```python
run_id(session: Any)
```

The run id for an AgentSession (or a FunctionInvocationContext). A string is used as it is.

#### `agent_framework.effect`

```python
effect(fn: Optional[Callable]=None, **options: Any)
```

c4a.effect for an Agent Framework function tool (with or without a FunctionInvocationContext).

#### `agent_framework.respond`

```python
respond(request: Any, approved: bool, *, tools: Any, session: Any=None)
```

The person's answer to a function_approval_request, as the response Content to send back.
First records c4a.approve (or c4a.deny) for exactly the arguments in that request, in this session.
`tools`: the agent's tools, wrapped with c4a_maf.effect(approval=True).
