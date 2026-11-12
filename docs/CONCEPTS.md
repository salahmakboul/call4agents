# Concepts

This page explains the few ideas that Call4Agents is built on. With them, you can tell what will happen
to any action, in any crash. Each idea links to the code that does it.

## The problem in one picture

An agent decides to act, and a tool sends the email, charges the card or books the room. Then the process
dies before the framework saves that the tool ran. The framework resumes from its last save, and the tool
runs again. This is called *at-least-once* execution. It is the normal behaviour of durable agent frameworks,
not a bug in them. For reading data it is fine. For a payment, it means two payments.

Call4Agents keeps its own small record of each action, outside the framework's saved state. The tool's next
call reads that record before it does anything.

## The run

Every action belongs to a **run**: `with c4a.run("thread-42"):`. Use the id your framework resumes with, such
as a LangGraph thread id, a Pydantic AI conversation id or a session id. A resumed run then finds its own
record. The framework adapters find this id for you.

## The action and its key

`@c4a.effect` marks a function that changes the outside world. One call of it is one **action**. An action
is named by its key: the run, the tool's name (module + function) and the exact arguments, written in one
stable text form. Two calls with the same key are the same action. Two calls with a different amount are two
different actions.

If you really want a second identical action (a second email with the same text), give it its own name:
`send_email("bob", "hi", c4a_key="second")`.

## The record: intent first, result after

For each action the record gets two rows. The rows are only ever added, never changed or deleted.

1. **intent**: written *before* the function runs. Only one caller can write it, because the database
   enforces it with a unique index. That caller runs the action. Every other caller waits for its result
   (up to `wait=` seconds, default 2), and then raises `EffectInDoubt` instead of running it.
2. **done**: written right *after* the function returns, with the result.

On the next call of the same action:

| What the record shows | What happens |
|---|---|
| a `done` row | the recorded result is returned. Nothing runs. |
| no row | the action runs. |
| an `intent` row but no `done` row | the action is **in doubt**: see below |

## In doubt, and the three answers

An intent without a result means that the process died, or the function raised, while the action ran. The
action may have happened, or it may not. Running it again could pay twice. Not running it could lose the
payment. So by default the kit does neither: it raises `EffectInDoubt`, and a person or a check decides.

You can give the kit a check: `reconcile=`, a function that asks the outside world (your payment provider,
your mailbox, your booking system). It gives one of three answers:

| reconcile returns | Meaning | What happens |
|---|---|---|
| the result | it happened | the result is recorded, nothing runs again |
| `None` | it surely did not happen | the action runs once more |
| `c4a.UNKNOWN` | the provider cannot tell yet | still in doubt; nothing runs; the next try asks again |

Only return `None` when the provider can really confirm it. A search index that lags behind writes should
answer `UNKNOWN`, not `None`.

**The kit asks only when the caller is gone.** An intent without a result can also mean that the call is still
running, perhaps just slow. Then the provider truthfully says "not done yet", and running the action again would pay
twice. So each intent names its owner: the machine, the process and a token for that process. The kit asks
`reconcile` only when that owner is provably not running the call any more. That is either a process on this machine
that has ended, or this process when the call is no longer in progress. An owner on another machine, or another
live process on this machine, counts as running for `lease=` seconds (default 600) after the attempt started.
Until then, the call raises `EffectInDoubt`. Set `lease=` to the longest time one attempt of the action can take.
Versions before this rule (up to 0.7.1) asked too early: see the CHANGELOG, Fix 17.

Two more ways to leave doubt:
- `retry_on=(SomeError,)`: errors that prove nothing happened, such as a refused connection. After one of
  them, the next call runs the action again.
- `idempotent_for=SECONDS`: the outside API removes duplicate requests that carry the same key (Stripe keeps
  keys for 24 hours). The function sends `c4a.idempotency_key()`, and an action in doubt is resent with the
  same key. It is the API, not luck, that prevents the second charge. After that time, the action stays in doubt.

## A "yes" bound to the exact arguments

`@c4a.effect(approval=True)` runs only after `c4a.approve(tool, **args)` has recorded a "yes" for exactly
those arguments.

- The "yes" is signed (HMAC with the record's secret) over the tool and the arguments. A "yes" to
  `pay(10, "alice")` cannot run `pay(9999, "mallory")`.
- **One yes, one action.** The "yes" is spent by one action. A retry or resume of that same action reuses
  it. A deliberate second action needs a new "yes".
- **A "no" wins.** `c4a.deny(tool, **args)` cancels the earlier "yes" answers for those arguments, also for an
  action that is in doubt.
- Spending the "yes" and writing the intent happen in **one database transaction**. Eight callers racing on
  one "yes": exactly one runs.

## Waiting for a person (tickets, experimental)

When a person decides later, perhaps in another process, `c4a.pause(tool, **args)` returns a ticket. You show
the ticket next to the call. `c4a.decide(ticket, "approve" | "deny", by=...)` turns it into a "yes" or a "no",
only for the arguments the ticket was issued for. The ticket holds no arguments, so nothing a client sends back
can change them. A ticket expires (its `ttl`) and is decided only once.

## What the kit cannot know

- If your `reconcile=` lies (answers `None` although the payment happened), the action runs twice. The kit
  trusts the answer you give it.
- If the function changes the world *and then* does something that can fail before it returns, the record
  sees the failure, not the change. That is why an error means "in doubt" unless you list it in `retry_on`.
- The record is only as durable as its disk. A hard kill is covered; a power cut is covered as far as SQLite's
  or PostgreSQL's own durability settings cover it.

The exact list of what is proven, and how, is in [GUARANTEES.md](GUARANTEES.md).
