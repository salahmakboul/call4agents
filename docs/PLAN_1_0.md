# The plan to 1.0

Call4Agents 1.0 is a promise. In 1.0 and in every 1.x release:

- the stable API below keeps working;
- records written by any earlier version can still be read;
- a change that could run an action twice, or without its "yes", is never a minor release.

This page says what will be stable, what stays experimental, what we merge or drop, and how you upgrade. It is a
plan, not a release. Each item lists what must be true before it is called stable.

## Stable in 1.0

These are the parts most users need. They change only in 2.0.

| Part | What it is | Before 1.0 |
|---|---|---|
| `c4a.init`, `c4a.run` | the record and the run id | done |
| `@c4a.effect` | once per (run, tool, exact arguments); `name=`, `key=`, `c4a_key=`, `exclude=`, `wait=` | done |
| `approval=True`, `c4a.approve`, `c4a.deny` | a "yes" bound to the exact arguments, spent by one action; a "no" revokes | expiry (see below) |
| `reconcile=` | the provider says: here is the result / it did not happen (`None`) / it cannot tell yet (`c4a.UNKNOWN`) | `UNKNOWN` leaves "experimental" |
| `retry_on=`, `idempotent_for=`, `c4a.idempotency_key()` | errors that prove nothing happened; resending with the same key | done |
| `retries=` | retry inside one call, only when the record decides the next try is safe | leaves "experimental" |
| `ApprovalRequired`, `EffectInDoubt`, `KeyConflict` | the three errors | done |
| The record format | entry kinds and schema version 1 (see `call4agents/ledger.py`) | a written format page |
| `doctor`, `status`, `doubt`, `resolve`, `verify` | the on-call commands | done |
| `crashtest`, the `c4a_crashtest` pytest fixture, the GitHub Action | 2 versus 1 in your own CI | leaves "experimental" |
| Adapters with real-framework tests | LangGraph, Pydantic AI, OpenAI Agents SDK, MCP, and the new ones once their tests pass in CI on 3 systems | per adapter |

"Leaves experimental" means four things:

- the documentation for it is complete;
- tests run against the real frameworks on Linux, macOS and Windows;
- the mutation runner catches every listed bug;
- at least one outside user has run it and told us what broke, or one release has passed with no open bug on it.

## Changes before 1.0

These come from our own reading of the complaints that agent developers post in issue trackers and forums.
Each one is a small change to the stable API, so it has to happen before 1.0, not after.

1. **A "yes" can expire.** Today `c4a.approve()` has no time limit; only tickets (`pause`/`decide`) have one.
   1.0 adds `c4a.approve(..., ttl=SECONDS)`. Without `ttl`, it works as today.
2. **A "yes" can be bound to the tool's version.** If you pass `@c4a.effect(version="2")`, a "yes" given for
   version 1 does not run version 2. Without `version`, nothing changes.
3. **The person who decides is named, and can be checked.** `decide(ticket, ..., by="anna")` stores `by` as text
   and does not check it. 1.0 documents this plainly and adds an optional check function, so your login system
   can check who is deciding.
4. **The record path in throwaway containers.** If the default `.c4a/` folder is inside a container that is
   thrown away, the record is lost with it. `doctor` and `init` will warn when this looks likely.
5. **The secret outside the agent's process.** The approval secret lives in the agent's own process. 1.0
   documents how to keep it in a separate signer, and supports one simple interface for that.

## Stays experimental in 1.0

These work and are tested, but we have not seen enough real use to promise their exact form. They may change
in a 1.x release, and the change will be in the CHANGELOG with a migration note.

| Part | Why it stays experimental |
|---|---|
| Approval tickets (`c4a.pause`, `c4a.decide`, the adapters' `tickets()`/`results()`) | no real approval UI has reported back yet |
| `c4a.Limit` (shared limits) | used only in our tests |
| `c4a.Rules` (rules file) | the rules language may grow |
| `audit` / `audit-verify` | format `c4a-audit-2` is new |
| `spans` (OpenTelemetry) | not yet loaded into a real collector; the GenAI names are still in Development status |
| `scan` | gives false positives; useful as a hint only |
| PostgreSQL record (beta) | tested on one local server, not on real networks |

## Merge or drop

1. **One way to ask a person.** There are two ways now: `pause`/`decide` (tickets), and the adapters'
   `tickets()`/`results()`. We keep both names. Before 1.0, they should share one page and one set of
   words, and there should be one example per framework.
2. **`undo`, `diff`, `prove`, `replay`.** These are useful for testing, but they are not part of the
   once-only promise. They move to their own "tools" page and stay out of the stable list. Nothing is removed
   without a deprecation warning that lasts at least one minor release.
3. **Less in the README.** The README becomes a short front page. The detail moves to the docs site: concepts,
   one guide per framework, the API reference, and recipes for payments, bookings, emails and refunds.

## Proof

1.0 ships with three kinds of proof:

- a **formal model** of the protocol, checked with the TLA+ model checker (in progress; it already found the
  double payment fixed in 0.7.2);
- **property-based tests and fuzzing** of the record and the reconcile logic (in progress);
- a **guarantees page** that links each promise to its tests and its known limits: [GUARANTEES.md](GUARANTEES.md).
  The model and the property tests will be linked from it when they are published.

A promise that has no check behind it is not on the guarantees page.

## Upgrading

- **0.7 to 0.8.** Nothing is removed. New, optional parameters (`ttl=`, `version=`) are added. You may see
  deprecation warnings for names that will move.
- **0.9.** A release candidate. The API is frozen, and the only changes are bug fixes and documentation.
- **1.0.** The same code as the last 0.9 release candidate that had no open bug for two weeks.
- **The record.** Every record written since 0.1 can still be read. A record written by 1.0 can be read by
  0.7 for the stable entry kinds; the new expiry and version information is stored in fields that 0.7 does
  not read.

## Also planned: JavaScript

A TypeScript version of the core and the crash test is being built. It covers the OpenAI Agents SDK for
JavaScript and the Vercel AI SDK. Its keys are computed the same way as in Python. It gets its own version
number and reaches 1.0 on its own schedule.
