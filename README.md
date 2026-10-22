# Call4Agents

Call4Agents makes the actions of a Python agent safe to retry. You add it to the agent you already
have: LangGraph, Pydantic AI, the OpenAI Agents SDK, an MCP server, or plain Python. It does not
replace any of them.

![call4agents quickstart: an agent crashes right after sending an email, then resumes. Without call4agents 2 emails are sent; with it, 1.](docs/demo.svg)

- **Once-only actions.** A crash, a retry or a resume does not send the same email or payment twice.
- **Approvals bound to exact arguments.** A "yes" to `pay(10, alice)` cannot run `pay(9999, mallory)`.
  A "yes" covers one action, and a later "no" is enforced.
- **Undo.** Register how to reverse an action (`undo=`), and `c4a.undo()` reverses a run newest first,
  each undo once only, even across crashes.
- **Idempotency keys for outside APIs.** The same key across crashes and resumes, so the payment API
  itself can drop the duplicate.
- **Record, replay, diff.** Rerun a recorded run offline with zero model calls, and find the step where
  two runs (for example two models) begin to differ.

Version 0.3, early. Standard library only, zero dependencies (PostgreSQL for several machines is an optional extra). Apache-2.0.

Guides with runnable code for each framework: [salahmakboul.github.io/call4agents/guides](https://salahmakboul.github.io/call4agents/guides/).

**Tested on every change** ([tests.yml](.github/workflows/tests.yml)). These are the only systems we claim:

| | Python 3.10 | Python 3.11 | Python 3.12 | Python 3.13 |
|---|---|---|---|---|
| Linux (GitHub `ubuntu-latest`) | ✓ | ✓ | ✓ | ✓ |
| macOS (GitHub `macos-latest`) | ✓ | ✓ | ✓ | ✓ |
| Windows (GitHub `windows-latest`) | ✓ | ✓ | ✓ | ✓ |

With the real frameworks, on all three systems with Python 3.12: LangGraph (+ LangChain), Pydantic AI,
the OpenAI Agents SDK and the MCP Python SDK. CI installs the latest release of each, and each run prints
the exact versions with `call4agents doctor`.

## Try it (under a minute, offline, no API keys)

```bash
pip install call4agents
call4agents quickstart
```

`quickstart` runs a plain-Python demo. Then, for each of LangGraph, Pydantic AI and the OpenAI
Agents SDK that is installed, it runs a real crash and resume in that framework, using a fake model.
Each framework is run once without Call4Agents and once with it. Output on our machine (Windows 10,
Python 3.12, all three installed):

```
Plain Python - crash right after an email is sent, then resume:
  without the run record                        emails sent: 2
  with the run record (crash after the tool)    emails sent: 1
  with the run record (crash inside the tool)   emails sent: 1 (refused to resend: in doubt)
  "yes" to pay(10, alice), call changed to pay(9999, mallory): REFUSED; approved call: PAID; payments: 1

Frameworks found: LangGraph, Pydantic AI, OpenAI Agents SDK
  LangGraph          crash after the email, resume:  without call4agents 2 emails,  with call4agents 1
  Pydantic AI        crash after the email, resume:  without call4agents 2 emails,  with call4agents 1
  OpenAI Agents SDK  crash after the email, resume:  without call4agents 2 emails,  with call4agents 1
```

With no framework installed, only the plain-Python part runs (about 5 seconds on our machine). To see
the framework rows, install them first: `pip install langgraph langgraph-checkpoint-sqlite pydantic-ai
openai-agents` (about 75 seconds in a fresh virtual environment).

`call4agents selftest` checks the demo results. Run it from the root of a repository checkout to run the
full offline suites instead (kit, production, stress and idempotency checks).

Found a problem, or something missing? `call4agents report` prints your system and framework versions
and a link to the [issue form](https://github.com/salahmakboul/call4agents/issues/new/choose) with them
filled in. It sends nothing and opens nothing: you read it and decide. There is no tracking of any kind.

## When you need it

- Your agent sends emails, payments, tickets or database writes, and a crash, a timeout retry or a
  resume from a checkpoint can repeat them.
- A person approves an action, and the arguments can change before it runs, for example through an
  edited saved state or a model asked again after a restart.
- A person double-clicks "approve", or two workers pick up the same approved task.
- You call an API that supports `Idempotency-Key` (Stripe, Adyen and others) and want a crash
  half-way through to be retried safely instead of needing a person.
- You switch model or provider and want to see which tool calls changed before you release.

You probably do not need it if your agent only reads data, or if every action already runs inside
a durable workflow engine that gives you these guarantees.

## How to use it

```python
import call4agents as c4a

c4a.init()                                     # creates .c4a/ledger.sqlite and .c4a/secret

@c4a.effect                                    # once per (run, tool, exact arguments)
def send_email(to, body): ...

@c4a.effect(approval=True)                     # also needs a "yes" for these exact arguments
def pay(amount, to): ...

@c4a.effect(idempotent_for=24 * 3600)          # the API remembers keys for 24 h (Stripe v1)
def charge(amount, to):
    return requests.post(URL, json={"amount": amount, "to": to},
                         headers=c4a.idempotency_headers()).json()

@c4a.recorded                                  # model calls, for replay and diff
def ask_model(prompt): ...

with c4a.run("thread-42"):                     # use the id your framework resumes with
    send_email("bob", "hi")
    send_email("bob", "hi")                    # same action: returns the recorded result, sends nothing
    send_email("bob", "hi", c4a_key="second")  # a deliberate second identical action
    c4a.approve(pay, amount=10, to="alice")    # when the person says yes to what they SAW
    pay(amount=10, to="alice")
```

Rules worth knowing before you start:
- **Always inside `c4a.run(...)`.** An action or approval outside a run raises `RuntimeError`. New threads
  do not inherit the run, so enter `c4a.run` in the thread (or use `contextvars.copy_context().run`).
- **A tool is identified by module + function name** (`billing.charge`). If you move or rename the
  function between deploys, a resumed run sees a new tool. Give long-lived tools a fixed `name="charge"`.
- **Arguments need a stable text form**: numbers, strings, lists, dicts, sets, dataclasses, Pydantic models.
  An object that prints its memory address raises `TypeError`; pass `c4a_key=` instead. For methods,
  `self` is not part of the key.
- **If the function raises**, the action may or may not have happened (a timeout after the charge
  raises too). The next call raises `EffectInDoubt` and names the error. List errors that prove nothing
  happened with `retry_on=(ConnectionRefusedError,)`; after those, the next call runs the action again.

**LangGraph and LangChain.**
- `from call4agents.adapters import langgraph as c4a_lg`.
- Use `@c4a_lg.effect` in nodes or tools.
- Call `c4a_lg.approve(tool, config, **shown_args)` when an interrupt is answered, or `c4a_lg.deny(...)`.
- The thread id becomes the run id.

**Pydantic AI, the OpenAI Agents SDK, MCP.** Each has an adapter. It leaves the framework's context
argument (`RunContext`, `RunContextWrapper`, MCP's `Context`) out of the key, because that object
changes on every call and plain `@c4a.effect` refuses it. It also finds the run id for you. The
framework's own decorator goes on top:

```python
from call4agents.adapters import pydantic_ai as c4a_pai      # run id: the run's conversation_id
@agent.tool
@c4a_pai.effect
def send_email(ctx: RunContext[Deps], to: str, body: str) -> str: ...

from call4agents.adapters import openai_agents as c4a_oa     # run id: c4a.run(...) or your context's session_id
@function_tool
@c4a_oa.effect
def send_email(ctx: RunContextWrapper[dict], to: str, body: str) -> str: ...

from call4agents.adapters import mcp as c4a_mcp              # run id: the Mcp-Session-Id, or run=lambda ctx: ...
@server.tool()
@c4a_mcp.effect
def create_ticket(title: str, ctx: Context) -> str: ...
```

Each has a copy-and-run example (fake model, no key), tested in CI with the real framework. Each one
prints 1 where the same code without the kit prints 2:

```bash
python -m call4agents.examples.pydantic_ai_tool     # pip install pydantic-ai
python -m call4agents.examples.openai_agents_tool   # pip install openai-agents
python -m call4agents.examples.mcp_tool             # pip install mcp
```

**Plain Python.** Decorate the function with `@c4a.effect` and wrap the work in `c4a.run(<id>)`.

**Undo a run.** Register how to reverse each action, then reverse a whole run, newest first:

```python
@c4a.effect(undo=lambda amount, to, result: refunds.create(charge=result["id"]))
def charge(amount, to): ...

@c4a.effect(undo=lambda day: hotel.cancel(day))
def book(day): ...

with c4a.run("trip-42"):
    c4a.undo()          # cancel the booking, then refund the charge; each undo runs once only
```

Each undo is itself a once-only action. If the process dies half-way, calling `c4a.undo()` again
continues where it stopped. An undo that crashed or raised is "in doubt": undo stops there, and
nothing older is touched until a person checks and runs `call4agents resolve`. Actions without
`undo=` are listed in the result, never skipped silently.

## How it works

The kit's whole state is one local SQLite file, which we call the run record. Entries are only ever
added to it, never changed or deleted.

1. **Before** a decorated action runs, an *intent* is written: run id, tool name, and a hash of the
   exact arguments. A unique index lets only one caller write the intent for the same action, so
   simultaneous callers cannot both start it.
2. **After** it returns, the *result* is written. A later call of the same action gets that result back
   and the action does not run again.
3. If the process dies **between** the two writes, nobody knows whether the action happened. The kit
   raises `EffectInDoubt` instead of guessing. You can resolve it in two ways:
   - `reconcile=` asks the outside system whether the action happened;
   - `idempotent_for=` lets the kit resend it with the same key while the API still remembers that key
     ([docs/IDEMPOTENCY.md](docs/IDEMPOTENCY.md)).
4. **Approvals.** An approval is an HMAC, made with a local secret, over the tool name and its exact
   arguments, and it is scoped to the run. Each approval can be spent by one action only. A later `deny`
   cancels it.
5. **Replay.** `c4a.init(replay_from="old.sqlite")` returns recorded model calls and results without
   calling anything. They come back as the same Python types, including Pydantic models and dataclasses.
   `call4agents diff a.sqlite b.sqlite` shows the step where two runs start to differ.

## Commands

| Command | What it does |
|---|---|
| `call4agents quickstart` | the demo plus a crash and resume in each installed framework |
| `call4agents demo` | the plain-Python demo only |
| `call4agents selftest` | the kit's checks (in a repository checkout, the full offline suites) |
| `call4agents doctor` | system and framework versions for a bug report; prints only, sends nothing |
| `call4agents report` | the same, plus a link to the issue form with it filled in; prints only, sends nothing |
| `call4agents status <record>` | rows, actions, actions in doubt, integrity |
| `call4agents doubt <record> [--fail-if-any]` | every action that may or may not have happened (exit 3 if any, for alerts) |
| `call4agents resolve <record> <key> done\|not-done "note"` | decide one in-doubt action; stored as a new row with your note |
| `call4agents archive <record> <archive> <days>` | move finished runs older than N days to another file |
| `call4agents verify <record>` | integrity, format version, late results |
| `call4agents diff a.sqlite b.sqlite [run_id]` | the step where two recorded runs start to differ |
| `call4agents prove spec.json` | puts each bug back and checks that your guard catches it |

`python -m call4agents ...` works the same way.

## In production

[docs/PRODUCTION.md](docs/PRODUCTION.md) covers the rest:
- costs: a new action is 1.9 ms (median) on our machine, a repeated one 45 µs;
- the big test, with 12 processes, random kills and a payment API that drops answers: 0 double charges,
  against 244 of 300 orders without the kit;
- secret rotation, archiving, what the record stores, every error message and what to do;
- a guide for whoever is on call.

## Several machines (new in 0.3)

When workers or replicas on different machines can pick up the same run, they need one shared record.
Point every machine at the same PostgreSQL database and give them the same secret:

```python
# pip install "call4agents[postgres]"
c4a.init("postgresql://user:password@db-host/agents", secret=os.environ["C4A_SECRET"].encode())
```

The table and the UNIQUE indexes are the same as in the SQLite file, so the same guarantees hold across
machines. This covers the case in [langgraph#7417](https://github.com/langchain-ai/langgraph/issues/7417),
where a long tool call is sent to a second worker while the first is still running it. The second worker gets
`EffectInDoubt` instead of running the call again, and a later caller gets the recorded result.
The secret has to be passed in, because a local secret file would be different on each machine.
The `call4agents` commands (`list`, `resolve`, `verify`, `archive`) work on SQLite files only for now.

## Measured in each framework

You can rerun every number in this table. The tests use fake models, fake tools and real process kills.

| Case | LangGraph | Pydantic AI | OpenAI Agents SDK | How to check |
|---|---|---|---|---|
| Crash after the email, then resume: emails sent without the kit | 2 | 2 | 2 | `call4agents quickstart` |
| The same with Call4Agents | **1** | **1** | **1** | `call4agents quickstart` |
| With Call4Agents: timeout retry after the email | **1** | – | – | `python -m tests.framework_kill_langgraph` |
| With Call4Agents: model asked again after a restart (approved 10, now says 12) | **refused** | – | – | same |
| With Call4Agents: LangChain human-in-the-loop, saved state edited to 9999 → mallory | **refused** | – | – | same |

A dash means the case was not tested in that framework.

## What is proven and what is not

**Proven by tests in this repository** (`tests/`):
- the table above;
- the big test (`python -m tests.bigtest_c4a`). 12 processes are killed at random while they charge
  300 orders through a payment API that drops and delays answers. The result is 0 double charges.
  Without the kit, the same run charges 244 of 300 orders twice or more;
- an action resumed in 3 new processes, as a method with a set argument, runs once;
- 14 simultaneous callers of one action: it runs once;
- 8 simultaneous actions on one "yes": exactly one runs;
- 3,000 of 3,000 records from 20 parallel writers;
- 5 kills during writes left the file intact;
- a crash after a payment API charged, then a resume with the idempotency key: 1 charge, and the
  original charge is returned;
- replay returns the same types, including real Pydantic and OpenAI SDK objects;
- a shared PostgreSQL record (`python -m tests.test_shared_record`), with each machine played by a separate
  process: 8 machines call one action at once and it runs once; a long action still running on one machine
  is not started again by a second one; after a crash on one machine, the next raises `EffectInDoubt`;
  a "yes" given on one machine counts on another, only for the same arguments and the same secret.

Each guard is mutation-tested: when its bug is put back, its check fails. A no-op control is run
next to every batch of mutations. Rerun them with `python -m tests.mutations.run_kit_mutations`.

**Not proven:**
- real payment or email systems, and production traffic;
- systems and Python versions not listed at the top of this page;
- several real machines on a real network sharing one PostgreSQL record (the tests use separate processes
  and one local PostgreSQL server), and managed platforms such as LangGraph Cloud;
- an independent code review;
- adapters other than LangGraph (the decorator itself works in any framework).

## Costs and limits

- A crash *during* an action is not resolved for you. You get `EffectInDoubt` unless you use `reconcile=`
  or `idempotent_for=`. Most frameworks would simply run the action again.
- A refused approval means the action does not run. Your app has to ask the person again.
- You decorate each action yourself, and call `approve` with the arguments the person saw.
- Replaying a recorded run that contains approval-gated actions needs the approvals again in the
  new run record (`c4a.approve(...)` before the replayed call).
- If the model rewrites the arguments on a retry (`10` becomes `10.00`), the call is treated as a new
  action. Pass `c4a_key=` (for example, an order id) when that can happen.
- The secret in `.c4a/secret` protects approvals from anyone who cannot read that file. It does not
  protect them from someone who can (see [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md)).

## More

- [docs/PRODUCTION.md](docs/PRODUCTION.md): costs, the big test, running it on call, every error message
- [docs/IDEMPOTENCY.md](docs/IDEMPOTENCY.md): idempotency keys and the exact retry rule
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md): what the approval secret protects, and what it does not
- [CHANGELOG.md](CHANGELOG.md)

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Copyright 2026 Salah Eddine Makboul.
"Call4Agents" is the project's name. The licence does not grant the right to use it for other products (Apache-2.0, section 6).
