# Changelog

## 0.5.1

**Security fix: please upgrade**
- **An approval could be used for other arguments under the same named key** (issue #1, found and reported by
  @krivonosoff161). With `approval=True` and an explicit `c4a_key` or `key=`: if the process stopped after a "yes"
  for `pay(10)` was spent and before the intent was recorded, or if a second call arrived in that moment, then
  `pay(20)` with the same key ran. It now raises `ApprovalRequired`. Calls without an explicit key were not
  affected, because their key contains the arguments. The bug is in every release since approvals with explicit
  keys existed (0.1.0 to 0.5.0).
- **The caller that loses a race on a named key gets `KeyConflict`**, not the winner's result.
- Check K22 (the crash path, a forced thread order, and the approved call finishing afterwards) and two mutations.

## 0.5.0

**New (experimental)**
- **Approval tickets for the OpenAI Agents SDK** (`c4a_oa.tickets(result, tools)`, `c4a_oa.apply(state, decisions)`)
  **and LangGraph** (`c4a_lg.pause(tool, config, **args)`; the ticket goes in the `interrupt()` payload). Checks
  T12 and T13 run them against the real frameworks.
- **One open ticket per call.** Pausing the same call again while its ticket is open (not decided yet, or approved
  and not used) returns the same ticket, so a LangGraph node that re-runs on resume does not leave a second request.
  After the action ran, or after a "no", a new pause asks again (T11).

**Fixed**
- **A named action reused with other arguments is a conflict** (issue #1, reported by @krivonosoff161). Calling an
  effect again with the same `c4a_key` (or `key=`) but arguments that mean something else returned the first call's
  result silently: nothing ran twice, but the caller was told the new call succeeded. It now raises `KeyConflict`
  and nothing runs. The same key with the same arguments still returns the recorded result; numbers are compared
  by value (10, 10.0 and Decimal("10.00") are the same amount). Check K21 and two mutations.

**Project**
- SECURITY.md (private reporting, acknowledged within 7 days), CONTRIBUTING.md, CODE_OF_CONDUCT.md, ROADMAP.md,
  docs/RELEASING.md (how a release is made and how a bad one is rolled back), issue and pull-request templates.
  GitHub Discussions and private vulnerability reporting are on.
- Docs: "check the outside system" replaced by "get an authoritative answer from the provider": a read that finds
  nothing can lag, so it is not proof that an action did not happen (thanks to the discussion in
  openai-agents-python#5409).

## 0.4.0

**New (experimental)**
- **Approval tickets.** `c4a.pause(tool, **args)` records that a run waits for a person and returns a ticket;
  `c4a.decide(ticket, "approve" | "deny", by=...)` records the decision for exactly that call. The ticket holds
  no arguments (a random id and a MAC), so what may run is read from the record, never from the client. A ticket
  the record did not issue, or one that was changed, expired or already decided, raises `TicketRefused`.
  Pydantic AI bridge: `c4a_pai.tickets(result, tools)` and `c4a_pai.results(decisions)`. Checks T1-T10 (with the
  real Pydantic AI in CI) and five mutations. Chosen because human approval is the most-asked-for area in the
  agent frameworks' issues; the cases come from langchain#40491, pydantic-ai#6452 and pydantic-ai#6968.

**Improved**
- The on-call commands (`status`, `doubt`, `resolve`, `verify`, `archive`) take a `postgresql://` address too.
  They refuse an address with no run record instead of creating one. `archive` copies finished runs to a SQLite
  file, then removes them. Checks S8-S12.
- The multi-machine record is labelled beta in the README.
- README: a 15-line first example with its real output, and a "When something goes wrong" table.

## 0.3.0

**New**
- **One run record shared by several machines.** `c4a.init("postgresql://...")` keeps the record in PostgreSQL
  (`pip install "call4agents[postgres]"`), with the same table, UNIQUE indexes and format check as the SQLite file.
  A long action that is still running on one worker is not started again by another (the case in langgraph#7417).
  Every machine must pass the same secret; there is no local secret file for a shared record.
- Checks S1-S7 (`python -m tests.test_shared_record`) with a separate process per machine, and mutations for them.
  CI runs them on Linux, macOS and Windows against a throwaway local PostgreSQL.

**Fixed while building it**
- Approval keys contain a NUL separator, which PostgreSQL text cannot store. The PostgreSQL store escapes it
  reversibly (S7); SQLite records are unchanged.

## 0.2.1

Metadata only, no code changes: links to the website and the guides (Homepage, Documentation), more keywords and classifiers on PyPI, and a link to the guides in the README.

## 0.2.0

**New**
- **Adapters for Pydantic AI, the OpenAI Agents SDK and MCP** (`call4agents.adapters.pydantic_ai`, `.openai_agents`, `.mcp`). They leave the framework's context argument out of the key; plain `@c4a.effect` refuses it, because it changes on every call. They also take the run id from the framework: the conversation id, the run context or the MCP session id. Each has a copy-and-run example that CI runs against the real framework on Linux, macOS and Windows.
- **Undo:** `@c4a.effect(undo=...)` and `c4a.undo()` reverse a run's finished actions, newest first. Each undo runs once only, a crash half-way continues where it stopped, and a failed undo stops the walk and is "in doubt" until a person resolves it. Developers asked for this in Pydantic AI issues #9141, #4679 and #7247.
- **`exclude=`** on `@c4a.effect`: parameters left out of the key.
- **`call4agents report`:** prints your versions and a link to the issue form with them filled in. It sends nothing.
- **An animated 30-second demo** at the top of the README (`docs/demo.svg`), built from a real `quickstart` run.

**Checks added:** K20, P18, U1–U6, the adapter checks, and mutations for each (`python -m tests.mutations.run_kit_mutations`).

## 0.1.0 (first public release)

**Licence: Apache-2.0.** Companies can use, change and ship it, with a patent grant. The NOTICE file must be kept, so we stay credited, and section 6 gives no right to use the project's name.

**What it does**
- **Once-only actions** (`@c4a.effect`): a crash, retry or resume does not repeat an action. An action interrupted half-way is reported as "in doubt" instead of being repeated.
- **Approvals bound to exact arguments:** one "yes" covers one action, `c4a.deny` is enforced, and racing callers on one "yes" run once.
- **Idempotency keys for outside APIs** (`c4a.idempotency_headers()`, `idempotent_for=`).
- **Record, replay and diff** for model calls, keeping result types; `prove` for mutation-testing your own guards.
- **LangGraph / LangChain adapter;** the decorator also works in Pydantic AI, the OpenAI Agents SDK and plain Python.
- **Commands:** `quickstart`, `selftest`, `doctor`.

**For production**
- **On call:** `status`, `doubt [--fail-if-any]`, `resolve`, `archive`, `verify`, so nobody has to read the database.
- **Long-running systems:** a versioned record format, secret rotation (`old_secrets=`, `C4A_OLD_SECRETS`), and an atomic secret file for many processes.
- **Speed:** about 1.9 ms per new action on our test machine (one reused connection per thread).
- **Clear errors** for damaged or foreign record files, network drives, unstable arguments, generators, unnamed lambdas and results that cannot be recorded.
- **Late results** are recorded and reported, never hidden.
- **Safe replay:** a crafted record cannot make the decoder call a function.
