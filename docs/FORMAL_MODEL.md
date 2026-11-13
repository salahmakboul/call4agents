# Formal model of the Call4Agents protocol (TLA+)

`formal/Call4Agents.tla` is a model of what the code does when several workers call the same
`@c4a.effect`, a person says yes and no, processes crash, and the outside world is asked (`reconcile=`)
or removes duplicates (`idempotent_for=`). The TLC model checker visits **every** reachable state of the
model (within the limits below) and checks the properties in each one.

It models the code on main after Fix 17 (commit 5b26492), not the README. Line numbers below are from that commit.

Run it: `python formal/run_tlc.py --tla2tools tla2tools.jar` (Java 11 or newer; CI uses Temurin 21 and
tla2tools.jar v1.7.4, sha256 `936a2620...0e88`, see `.github/workflows/formal.yml`).

## The model found a real bug in 0.7.1

TLC found a double payment in 0.7.1 with a **truthful** `reconcile=` and no crash. We then reproduced it with
the real 0.7.1 library (`formal/repro_reconcile_race_071.py`, two scenarios, one run each): 2 payments in both.
It is fixed on main as Fix 17. The configs `bug_reconcile_live_owner`, `bug_give_up_twice` and `bug_v071` put the
old behaviour back, and TLC must still find it.

Form 1, a call that is still running (`bug_v071` and `bug_reconcile_live_owner`, shortest trace 22 states):

1. Worker 2 claims attempt 1 and starts the tool. The tool is slow.
2. Worker 1 calls the same action, fails to claim attempt 1, waits `wait` seconds, and gets no result.
3. Worker 1 asks `reconcile`. The provider truthfully says "not paid" (worker 2's request has not arrived).
   Worker 1 writes `tool_abandoned` for attempt 1, claims attempt 2 and pays.
4. Worker 2's request arrives too: 2 payments.

Form 2, an attempt closed twice (`bug_give_up_twice`, shortest trace 29 states; the owner check is present):

1. Worker 1 claims attempt 1; the tool raises a `retry_on` error (nothing happened), so worker 1 writes
   `tool_abandoned` for attempt 1.
2. Worker 2 had read "next attempt = 1" just before. Its claim of attempt 1 fails, it waits, the owner of
   attempt 1 is gone (its call ended), so it asks `reconcile`: truthfully "not paid". It writes a SECOND
   `tool_abandoned` for attempt 1.
3. Meanwhile worker 1 called again: "next attempt = 2" (one closed row), claims it and pays.
4. 0.7.1 counted closed rows: worker 2 now reads "next attempt = 3", claims it and pays: 2 payments.

The real-library scenario 2 is a variant of form 2 (a `tool_error`, then two callers reconcile at the same time):
there the record shows no `late_result`, so nothing in the record says it happened twice.

Fix 17 has two parts, and each closes one form: the owner check (no reconcile or same-key resend while the
attempt's owner may still run it), and `Ledger.give_up` (an attempt is closed once, in one transaction;
the next attempt is the newest closed one + 1).

## A second finding: a window inside one process (main, after Fix 17)

Config `finding_threads_window` (expected to FAIL until the code changes). The owner check counts a call of
**this** process as running only between `_owner.started()` and `_owner.finished()` (`__init__.py` 575-587).
`started()` is called after `check()` has returned, so for a short time the intent is in the record but the
call is not marked as running. TLC's counterexample (shortest, 22 states):

1. Thread 1 claims attempt 1 (the intent is committed) and is paused before `_owner.started()`.
2. Thread 2 of the same process calls the same action, fails to claim attempt 1, waits `wait` seconds,
   and asks the owner check: same pid, not in progress, so "gone".
3. Thread 2 asks `reconcile`: truthfully "not paid". It gives attempt 1 up, claims attempt 2 and pays.
4. Thread 1 continues and pays too. (Its result is written after the attempt was given up, so the record
   gets a `late_result` and Python warns: the double is visible, not silent.)

Reproduced with the real library on main, 3 out of 3 runs, in `formal/repro_owner_window.py`: once with a pause
injected after the claim, and once **without any change to the library**: an async call whose event loop is
blocked by another task for longer than `wait` (0.8 s against `wait=0.05`), and a thread of the same process
calling the same action. With the default `wait=2.0` the loop must be blocked for more than 2 seconds.
Separate processes are not affected (a live pid counts as running until `lease`).

The config `reconcile_threads_start_early` checks one way to close it: mark the call as running before its
claim (and unmark it when the claim fails). With that change TLC finds no error. This is a proposal; it is
not in the code.

## A third finding, harmless: two results for one action

Config `finding_done_twice` (expected to FAIL; shortest trace 21 states). Worker 2 reads "no result" and its
claim of attempt 1 fails, because worker 1 holds it. Worker 1 pays, writes `tool_done` and returns. Worker 2's
wait ends without a poll that sees the result (the code polls only BEFORE the deadline: `wait=0`, or a thread
paused past the deadline). The owner of attempt 1 is gone (its call ended), so worker 2 asks `reconcile`, gets
the result, and writes a second `tool_done`. The action happened once and both rows say so; the kit reads the
newest. We keep `DoneOnce` as a property and say the code does not have it, instead of weakening it. (Two callers
reconciling the same crashed attempt at the same time also write two rows.)

## What is modelled

- **Workers**: 2 or 3. Workers 1 and 2 make the same call (same run, tool and arguments, so the same key).
  Worker 3 makes either a call with other arguments, or a second named action (`c4a_key`) with the same arguments.
- **The run record**: a sequence of entries that the model only appends to: `approval`, `denial`,
  `approval_used`, `effect_approved`, `tool_intent`, `tool_done`, `tool_error`, `tool_abandoned`,
  `tool_retry_same_key`, `late_result`.
- **A person** who records "yes" and "no" for exact arguments at any time (bounded number).
- **The admission transaction** `claim_approved` as one step: result already recorded? approved action
  resumed but denied since? intent exists? spend the oldest live, unspent, not-denied "yes"
  (`approval_used` + `effect_approved` + `tool_intent` together).
- **The outside world**: a counter per action. With `idempotent_for=` the provider applies a key once.
- **Crashes at any step**: process memory (where the worker is, its attempt number) is lost; the record and
  the world stay. A worker that comes back makes a new call. Threads of one process crash together.
- **The tool**: returns; raises after the effect happened; raises before it happened; raises a `retry_on`
  error (only when nothing happened).
- **reconcile=**: returns the result if the action happened, `None` if it did not (truthful), or `UNKNOWN`
  at any time.
- **The owner check** (Fix 17): reconcile or a same-key resend only when the attempt's owner crashed or
  finished. Two kinds of owner: separate processes (a live process holds its attempt from the claim on),
  or threads of one process (only the in-progress mark counts).

## Properties (each in one sentence)

| Property | Meaning |
| --- | --- |
| `AtMostOnce` | The outside world sees each action at most once. |
| `NoUnapprovedEffect` | The tool runs only under an intent written by an admission that spent a "yes" for exactly its arguments on this action, with no "no" for those arguments between that "yes" and the intent. |
| `OneYesOneAction` | Each "yes" is spent once, by one action. |
| `DoneIsTrue` | A recorded result means the action really happened. |
| `DoneOnce` | At most one `tool_done` per action (the code does NOT have this with reconcile; see above). |
| `AppendOnly` | Entries are never changed or removed (an action property). |
| `AllCallsEnd` | Progress: with fair workers, every call ends (no worker waits forever). |
| `AdmittedFinishes` | Progress: with no crash and a tool that returns, an admitted action gets its result. |

The progress properties use weak fairness for the workers only; crashes and the person are not fair.

## Results

One full run on 13 Nov 2026: 24 configs, all as expected, 12,182,980 distinct states in total, 594 s.

| Config | Kind | Bounds (besides the defaults below) | Result | Distinct states | Depth / trace | Time |
| --- | --- | --- | --- | --- | --- | --- |
| plain_approval | pass | 3 workers | no error | 130,029 | 35 | 8 s |
| plain_second_action | pass | 3 workers, worker 3 = 2nd action, same args; 2 yes, 0 no | no error | 732,165 | 40 | 42 s |
| plain_no_approval | pass | 3 workers, no approval, worker 3: 2 calls | no error | 2,286,009 | 45 | 59 s |
| reconcile_processes | pass | reconcile, 3 calls each | no error | 2,263,158 | 81 | 94 s |
| reconcile_three | pass | reconcile, 3 workers, worker 3 = 2nd action | no error | 552,897 | 56 | 25 s |
| idem_processes | pass | idempotent_for, 3 calls each | no error | 4,729,851 | 78 | 253 s |
| reconcile_threads_start_early | pass | threads, proposed change (not main) | no error | 1,393,360 | 83 | 69 s |
| live_calls_end | pass | progress, reconcile, no approval | no error | 32,138 | 51 | 8 s |
| live_admitted_finishes | pass | progress, no crash, tool returns | no error | 1,509 | 24 | 3 s |
| finding_threads_window | finding | threads, reconcile, no approval | AtMostOnce violated | 5,133 | trace 22 | 2 s |
| finding_done_twice | finding | reconcile, no approval | DoneOnce violated | 3,062 | trace 21 | 2 s |
| bug_split_admission | seeded | | NoUnapprovedEffect violated | 727 | trace 10 | 2 s |
| bug_no_spend | seeded | 3 workers, 2nd action | OneYesOneAction violated | 2,017 | trace 10 | 2 s |
| bug_no_deny_in_doubt | seeded | | NoUnapprovedEffect violated | 4,164 | trace 16 | 2 s |
| bug_reconcile_lies_none | seeded | reconcile | AtMostOnce violated | 4,485 | trace 21 | 2 s |
| bug_reconcile_lies_found | seeded | reconcile | DoneIsTrue violated | 1,796 | trace 16 | 2 s |
| bug_retry_on_lies | seeded | | AtMostOnce violated | 1,546 | trace 14 | 2 s |
| bug_provider_forgets | seeded | idempotent_for | AtMostOnce violated | 2,528 | trace 17 | 2 s |
| bug_no_unique_intent | seeded | no approval | AtMostOnce violated | 1,004 | trace 13 | 2 s |
| bug_delete_on_abandon | seeded | | AppendOnly violated | 215 | trace 8 | 2 s |
| bug_no_timeout | seeded | progress, reconcile | AllCallsEnd violated | 9,364 | trace 18 | 4 s |
| bug_reconcile_live_owner | seeded | reconcile | AtMostOnce violated | 5,977 | trace 22 | 2 s |
| bug_give_up_twice | seeded | reconcile | AtMostOnce violated | 13,859 | trace 29 | 3 s |
| bug_v071 | seeded | reconcile, both 0.7.1 parts | AtMostOnce violated | 5,987 | trace 22 | 2 s |

Defaults (`BASE` in `formal/run_tlc.py`): 2 workers making the same call (worker 3, when present, makes 1 call),
2 calls each, 1 "yes", 1 "no", approval on, `retry_on` set, crashes at any step, the tool may raise,
separate processes. Each config's exact constants are in `formal/cfg/<name>.cfg` (generated from the table in
`run_tlc.py`; the runner fails if a file and the table differ).

TLC 1.7.4, Temurin 21.0.13, `-workers auto`, Windows 10 laptop. "Depth" is the longest shortest path in the
state graph (for a pass) and "trace" is the length of the shortest counterexample (for a violation; TLC
searches breadth-first, so no shorter one exists in the model).

## Seeded bugs (each must be caught)

A model whose properties can never fail proves nothing. Each seeded bug puts one real mistake back
(`BUGS = {...}` in the config), and `run_tlc.py` fails if TLC does NOT report the named property.

- `split_admission`: the "yes" is checked in one step and the intent written in a later one. A "no" lands in
  between, and the action is admitted on a "yes" that was already cancelled (NoUnapprovedEffect, 10 states).
- `no_spend`: a used "yes" is not marked as spent. One "yes" admits the first action and then a second named
  action with the same arguments (OneYesOneAction, 10 states).
- `no_deny_in_doubt`: the "no" is not checked when an admitted action is resumed. The first try fails with a
  `retry_on` error, the person says "no", and the retry runs anyway (NoUnapprovedEffect, 16 states).
- `reconcile_lies_none`: reconcile says "not done" after the payment; the next attempt pays again (AtMostOnce, 21).
- `reconcile_lies_found`: reconcile says "done" when nothing happened; a result is recorded for a payment that
  does not exist (DoneIsTrue, 16).
- `retry_on_lies`: a `retry_on` error is raised after the payment; the attempt is given up and the next one pays
  again (AtMostOnce, 14).
- `provider_forgets`: the API does not deduplicate the idempotency key; the same-key resend pays again (AtMostOnce, 17).
- `no_unique_intent`: no UNIQUE index on `tool_intent`; two workers claim the same attempt and both pay (AtMostOnce, 13).
- `delete_on_abandon`: giving up deletes the old intent instead of appending a row (AppendOnly, 8).
- `no_timeout`: `wait` never ends while there is no result; after a crash of the owner, the waiting worker waits
  forever (AllCallsEnd, a lasso of 18 states).
- `reconcile_live_owner`, `give_up_twice`, and both together (`v071`): the 0.7.1 bug above (AtMostOnce, 22 / 29 / 22).

The runner was also checked against itself: `--selftest` checks the output parser on canned TLC outputs, and a
parser that reads the first state count (a progress line) instead of the last one fails it.

We also edited the CORRECT model one piece at a time (small bounds: 2 workers, 2 calls, reconcile):

| Edit of the correct model | TLC |
| --- | --- |
| none (control) | no error, 32,138 states |
| the owner check always says "gone" | AtMostOnce violated, trace 22 |
| the "no" is not checked on resume | NoUnapprovedEffect violated, trace 16 |
| `give_up` never refuses (but next attempt = newest closed + 1) | no error, 80,330 states |
| next attempt counts closed rows (but `give_up` closes once) | no error, 32,138 states |

The last two rows mean: the second part of Fix 17 is two protections, and in the model **each one alone is
enough** for AtMostOnce; only removing both (0.7.1, `bug_give_up_twice`) breaks it. `give_up`'s "once" rule
also keeps a `Limit` hold from being released twice, which the model does not cover.

## Map: model step -> code (commit 5b26492)

| Model step | Code |
| --- | --- |
| `Start` | `wrapper()` `__init__.py` 569-572, a new call |
| `ReadDone` | `decide_run` 449-451: a recorded result is returned |
| `ReadAttempt` | 452 `lg.next_attempt(k)`; `ledger.py` 418-425 |
| `Claim`, `ClaimNext` | `claim()` 403-415 -> `ledger.claim` 288 (UNIQUE index `one_intent`, 207) or `ledger.claim_approved` 300 (one `BEGIN IMMEDIATE` transaction, 298; UNIQUE index `one_use`, 209); `ClaimNext` is 478 |
| `Begin` | 575 `_owner.started(running)` (after `check()` returned) |
| `Effect` | 577 `f(*args, **kwargs)` |
| `RecordDone`, `CheckLate` | `record_result` 506-511 (`tool_done`, then `late_result` if the attempt was given up) |
| `FailOther`, `FailRetry` | `failed()` 520 (`tool_error`) and 516 (`tool_abandoned` for a `retry_on` error) |
| `ReadErr` | 455-458: an error of THIS attempt skips the wait and the owner check |
| `Wait` | 459-463: poll for the result until the deadline |
| `OwnerCheck` | 464-470 -> `owner.may_still_run` (`owner.py` 76) |
| `AfterWait` (idem) | 471-481: `give_up(.., "tool_retry_same_key")`, `continue` on False, then `ClaimNext` |
| `Reconcile`, `ReconDone` | 484-490 |
| `Abandon` | 492 `lg.give_up(...)` (`ledger.py` 396-416), then the next pass of `for _ in range(2)` (448) or 493 |
| `Crash` | `os._exit` anywhere; an open transaction rolls back (a step either happened or not) |
| `Approve`, `Deny` | `ledger.record_approval` 482, `record_denial` 487 |

## Simplifications (where the model is not the code)

1. One run, one tool. Keys are names (`k1`, `k2`), not hashes; arguments are `a` or `b`. An HMAC match is
   modelled as "same arguments". `KeyConflict` (a named key reused with other arguments) is not modelled.
2. Each SQLite statement or transaction is one atomic step. `record_result`'s read of `tool_abandoned` and its
   `late_result` append are merged into one step (it only adds a warning row).
3. Time is not modelled. `wait` ends at any moment. The `idempotent_for` window is a free choice at each check.
   `lease`: the owner check says "gone" in the model only when the owner crashed or finished. The code is
   stricter (a live pid or another host counts as running until `lease` seconds), so it allows fewer reconciles
   than the model. The model ASSUMES no attempt runs longer than `lease`; an owner still running after its lease
   is outside the model.
4. `retries=N` (the loop inside one call) is modelled as further calls by the same worker; backoff is not modelled.
5. Process identity (host, pid, token, pid reuse) is abstracted to "which worker holds the attempt". So the
   planned change on main that stops comparing the process token does not change the model.
   Mixed setups (threads in several processes) are not modelled.
6. Not modelled: `Limit` and `Rules`, approval tickets (`pause`/`decide`), replay, `undo`, the codec, async
   (the same steps as sync; the in-progress window is modelled in its thread form), `KeyConflict`, the result
   that cannot be encoded (it acts like "raises after it happened").
7. Bounds: at most 3 workers, 1-3 calls each, 1-2 "yes" and 1 "no". A bug that needs more steps is not seen.

## What this does NOT prove

- **That the Python code matches the model.** The model was written by reading the code; a mistake in that
  reading is not caught by TLC. The trace check (below) links them for some scenarios only.
- **Durability**: that SQLite or PostgreSQL really keeps a committed row after a power cut (fsync, WAL, disks
  that lie), or that `BEGIN IMMEDIATE` and the UNIQUE indexes behave as one atomic step. The model assumes it.
- **HMAC**: that a "yes" cannot be forged, or that the secret is kept secret.
- **Clocks**: `idempotent_for` windows, `lease`, ticket expiry. A clock that jumps, or an attempt longer than
  `lease`, is outside the model.
- **Limit and Rules** (not modelled).
- **More workers, calls or answers** than the bounds above.
- **A provider that lies**: `reconcile` that says "not done" when it was done, a `retry_on` error raised after
  the action happened, or an API that forgets an idempotency key early. The seeded bugs show each of these
  breaks the guarantee; the model only proves the guarantee when they do not happen.
- **A provider that is slow**: a request still being processed after the tool returned or raised (reconcile
  must then answer `UNKNOWN`). The model treats the provider as up to date when asked.

## The trace check (links the model to the code)

`python -m tests.test_formal_trace` runs the **real library** through 72 crash/resume scenarios (216 processes)
(real processes killed with `os._exit` before and after each write to the record, and inside the action;
with approval + reconcile, approval + `retry_on`, and reconcile without approval; the second process either
resumes or first says "no"), then checks each record and the fake provider's payments against the same
invariants written in Python (T1-T5), plus T6 (a call still running is not reconciled) and T7 (controls: the
checker sees each kind of broken record). It takes about 90 seconds. It is mutation-proven (entries `TR ...` in
`tests/mutations/run_kit_mutations.py`): 4 mutations of the kit are caught (reconcile while the owner runs: T6;
the "no" not checked on resume, an error treated as "not done", reconcile's result ignored: T1-T5) and the
no-op control is missed.

The trace check is not a proof. It checks 72 scenarios with one or two callers, not every interleaving, and it
cannot see the same-process window above (that needs a paused thread; see `formal/repro_owner_window.py`).
