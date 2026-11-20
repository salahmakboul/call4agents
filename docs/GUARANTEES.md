# Guarantees

This page lists what Call4Agents promises. Each promise comes with:

- the conditions under which it holds;
- the checks that prove it, which you can rerun;
- what it does not cover.

A promise with no check behind it is not on this page. Every check named here is also mutation-tested: we put the
bug back on purpose, and the check must fail. You can rerun that with `python -m tests.mutations.run_kit_mutations`.

The words used here are explained in [CONCEPTS.md](CONCEPTS.md).

## G1. An action runs at most once

**The promise.** For one run, one tool and the same arguments (or the same `c4a_key`), the function runs at most
once. This holds when the process crashes, the framework retries or resumes, or several callers arrive at the same
time. A later call returns the recorded result. If the kit cannot know whether the action happened, it raises
`EffectInDoubt` and does not run it again.

**Conditions:**

- The record is on a local disk. The kit refuses network drives, because SQLite locking is not reliable there. It
  can also be a PostgreSQL database shared by every caller.
- Every caller uses the same record.
- If you pass `reconcile=`, it answers truthfully: the result when it happened, `None` only when it surely did not,
  and `c4a.UNKNOWN` when it cannot tell yet.
- If you pass `idempotent_for=`, the outside API really removes repeated requests that carry the same key, for
  that long.
- If you pass `lease=`, one attempt never runs longer than that.

**Checks:**

- K1 and K1b: a repeated call runs once; another run is another action.
- K2: a crash during the action is "in doubt", and the action is not repeated.
- K3, K25 and K25b: the three answers of `reconcile` (the result, `None`, `UNKNOWN`).
- K26c: a resend with the same key; the provider charges once.
- K29a-f: a call still running is not treated as a crash, also in the moment right after its claim, and two
  callers that give up the same attempt start only one new one. This includes real processes: one is killed
  inside the call, then two resume at once.
- The formal model: `AtMostOnce` holds in every passing configuration ([FORMAL_MODEL.md](FORMAL_MODEL.md)).
- The property tests: "outside actions per key <= 1" after every step of random runs with racing callers
  ([PROPERTIES.md](PROPERTIES.md)).
- The crash matrix (`python -m tests.test_crash_matrix`) kills a real process before and after every write to the
  record and inside the action, then kills the recovery too: 412 crashes, charged at most once at every point
  (M1, M3, M6).
- The stress test: 14 callers of one action at once, 1 run.
- The big test (`python -m tests.bigtest_c4a`): 12 processes killed at random while they charge 300 orders
  through a payment API that drops and delays answers. 0 double charges. Without the kit, 244 of 300 orders are
  charged twice or more.

**Not covered:**

- A `reconcile` that lies. If it says `None` although the action happened, the action runs again.
- An attempt that runs longer than `lease=` while its owner is alive on another machine or in another process.
- Power loss, as far as your disk or database does not keep a committed write (the record uses SQLite with
  `synchronous=FULL`, or PostgreSQL's own durability settings).
- Records changed by hand.
- Versions up to 0.7.1, which could run an action twice with `reconcile=` or `idempotent_for=` when two callers
  raced (Fix 17), and 0.7.2, which still could when two threads of one process raced in a short window (Fix 19).
  See the CHANGELOG.

## G2. A "yes" covers exactly the call that was shown, once

**The promise.** An action with `approval=True` runs only if `c4a.approve` recorded a "yes" for exactly its
tool and arguments.

- The "yes" is an HMAC, signed with the record's secret, over the tool and the arguments.
- It is spent by one action. A retry or resume of that same action reuses it. A different action needs a new
  "yes", even under the same `c4a_key`.
- Spending the "yes" and writing the intent happen in one database transaction.

**Conditions:** the secret is kept away from whoever is not allowed to approve. Anyone who has the secret can
sign a "yes".

**Checks:**

- K5: a hand-written approval without the secret is refused.
- K17: one "yes" covers one action, and a deliberate second action needs a new one.
- K17d: 8 actions at once on one "yes": exactly one runs.
- K22: after a crash, a spent "yes" does not cover other arguments under the same key, and with two threads the
  unapproved amount is refused.
- The atomic admission tests (`python -m tests.test_atomic_admission`, also on PostgreSQL): a failure before the
  intent rolls the whole admission back, and processes crash before and after the commit.
- The crash matrix M2: a call nobody approved is never charged, at any crash point.

**Not covered:**

- Whether the person understood what they approved: the kit binds the "yes" to the arguments you showed.
- A "yes" without `ttl=` never expires, and one for a tool without `version=` is not bound to a version
  (see G2a and G2b).

## G2a. A "yes" with `ttl=` expires, and expiry never causes a second action (0.8)

**The promise.** `c4a.approve(tool, ..., ttl=SECONDS)` records a "yes" that can admit a NEW action only until
`ttl` seconds after it was recorded. After that, the call raises `ApprovalRequired` (the message says the yes
expired), nothing runs and nothing is recorded for it; a new `c4a.approve` covers it again.

**`ttl` limits when the yes can be SPENT, not when the action happens.** An action admitted before the expiry
can still run or resume after it (for example its retry after an error that proved nothing happened). What
`ttl` guarantees is that no action is admitted on that yes after the expiry, and that the expiry never makes an
admitted action run a second time.

- The expiry time is stored in the approval row and signed with it (inside the HMAC). A row whose expiry was
  changed, added or removed counts for nothing.
- Expiry is read in the same database transaction that spends the "yes" and writes the intent. There is no
  window between the check and the spend.
- An action the "yes" already admitted keeps it after expiry. A finished action returns its recorded result. One
  in doubt stays in doubt, also after a new "yes" (that yes is not spent on it). `reconcile=` on its retry works
  as without `ttl`: a found result is recorded, and a proven "did not happen" lets the same action run once.
- Without `ttl=` the row is exactly the 0.7 row, and works exactly as in 0.7.

**Conditions:**

- The clock rule is the one tickets (`c4a.pause(ttl=)`) use: the expiry time is the approving process's
  `time.time()` plus `ttl`, and the admitting process compares it with its own `time.time()`. The yes is live
  while `now <= exp` (at `exp` exactly it is still live; the formal model uses `now < exp`, which makes no
  difference to any safety property). When several
  machines share a record, their clocks must agree to well within `ttl`; a skew of S seconds moves the expiry
  by S. A clock set back makes a yes live longer; a clock set forward makes it expire sooner.
- The other conditions of G2.

**Checks:**

- K30a: an expired yes admits nothing (ApprovalRequired, no intent, the yes stays unspent); a new yes works.
- K30b, K30f: inside its ttl it is spent once; a finished action replays after expiry.
- K30c: without ttl the row and its MAC text are exactly 0.7's.
- K30d: an expiry edited later, or removed, in the record is refused.
- K30e: an action in doubt stays in doubt after expiry, and a new yes does not run it again.
- K30h (real processes, `os._exit` during the payment, then the yes expires, then a new process resumes):
  killed after the charge, the resume stays in doubt and a second "yes" does not pay again (1 charge); with
  `reconcile=` the resume returns the payment (1 charge); killed before the bank, reconcile proves it did not
  happen and the admitted action runs once (1 charge).
- ST9: `call4agents story` shows a yes that expired before any action used it, and that nothing ran under it.
- Mutations: the expiry never checked, also checked on an admitted action, kept outside the MAC, not stored,
  dropped by `approve`, `ttl` not validated.

**Not covered:**

- Clocks that disagree (see conditions), and a clock that jumps.
- An admitted action that proves it did not happen runs later than the expiry. That is the same one action the
  person approved, not a second one; if that is not acceptable, use `c4a.deny` before resolving it.
- Approval tickets: the ticket's own `ttl` limits when a person can decide; the "yes" a ticket records has no
  expiry of its own.
- A kit older than 0.8 reading the record: it does not know the signed expiry, so it treats a yes with `ttl` as no
  yes at all (it refuses; it never runs on it).

## G2b. A "yes" is bound to the tool's version (0.8)

**The promise.** For a tool declared with `@c4a.effect(version="2")`, only a "yes" given for version "2"
(`c4a.approve(that_tool, ...)`, or a ticket from `c4a.pause(that_tool, ...)`) admits a new action. A yes for
another version, or for no version, does not, and a 0.7 approval row admits only tools without `version=`.

- The version is stored in the approval row and signed with it.
- A "no" (`c4a.deny`) covers every version of the tool.
- The version is not part of the action's key: a finished version-1 action returns its recorded result to
  version-2 code and does not run again (G1 is unchanged).
- An action admitted under a version-1 yes and still in doubt is not continued by version-2 code: it raises
  `ApprovalRequired`, and a new yes does not change that. Resolve it by hand.
- Without `version=` nothing changes.

**Checks:** K31a-K31i, and the mutations: the version not compared, ignored by `approve`, ignored by the
admission, dropped by a ticket.

**Not covered:**

- The version is a label you set. The kit does not check that the code really changed, or that it did not.
- `c4a.approve("module.tool", ...)` with a tool name instead of the tool gives a yes with no version.

## G2c. An ordered run starts nothing new while an earlier action is uncertain (0.8, opt-in)

**The promise.** Inside `with c4a.run(run_id, ordered=True):`, a NEW action (one with no intent in the record yet)
is refused with `EffectInDoubt`, naming the earlier action, while any other action of the same run has no final
outcome. Nothing runs, nothing is recorded for the refused action, and a yes it would have spent stays unspent.

- Final means: done (with no late result left unchecked), or EVERY attempt proved not to have happened (each has
  its own "did not happen": an error in `retry_on`, or `reconcile=` returning `None`), or a person resolved it
  (`call4agents resolve <key> not-done`). An attempt closed by a same-key resend (`idempotent_for=`) is no proof:
  it may have reached the provider.
- Not final means: running, killed, raised, being resent with the same key, a late result, or any attempt
  without a proof (`call4agents doubt` lists the same actions, plus running ones).
- It unblocks when the earlier action becomes final: `call4agents resolve <key> done|not-done`, a `reconcile=` on
  its retry, or, for a late result, `call4agents resolve <key> done "note"`, which records that a person checked
  for a duplicate (a `late_checked` row).
- The check is in the same transaction as the new action's intent: 8 threads starting 8 different actions of one
  ordered run at once give 1 run and 7 refusals.
- The retry or resume of an action already started is not refused; it follows the usual rules.
- A nested `with c4a.run(same id):` (the adapters do this) keeps the run ordered.
- Runs without `ordered=True` behave exactly as before.

**Conditions:** every caller of the run passes `ordered=True`. The flag lives in the caller's context, not in
the record: another process, a thread that did not copy the context, or an adapter that enters the same run id
without the flag is not refused. Use one worker per ordered run, or pass the flag in each of them.

**Checks:** K32a-K32g (K32g: attempt 1 raised after reaching the provider and the same-key resend got a `retry_on`
error; the action stays uncertain, the next step is refused, and `doubt` and `story` show it as uncertain), and
MS5 (real kills): the loop that carries on past an uncertain reserve runs no charge and
no email in an ordered run (0 and 0), against 1 and 1 without it (MS4). Mutations: the check removed in each
admission path, the flag dropped by `run()` or by a nested run, a retry refused, a late result ignored or never
cleared, only the newest attempt read (by the order check, and by `doubt`), "not done" counted as uncertain, a
person's "not done" ignored, `resolve` unable to clear a late result.

**Not covered:**

- Other runs: the order is per run id. Two runs that touch the same account are not ordered against each other.
- A late result that arrives after a later action was admitted. It blocks the actions after it, but it cannot
  undo what already ran.
- A caller that enters the run without `ordered=True` (see conditions).

## G3. A "no" wins

**The promise.** `c4a.deny` cancels every earlier "yes" for the same arguments. This includes an action that is in
doubt: its retry needs a new "yes".

**Checks:**

- K17b: a "no" after the "yes" is enforced.
- K17c: a "no" stops the retry of an approved action that is in doubt. As a control, without the "no" it retries
  once.
- The atomic admission tests: a denial written while another process is admitting the action waits for that
  commit, so it cannot slip between the check and the write.

**Not covered:** an action that already ran before the "no". The record shows that it ran; the kit cannot take it
back. That is what `undo=` is for.

## G4. A named action is never confused with another

**The promise.** A `c4a_key` (or `key=`) used again with arguments that mean something else is a conflict, not a
cache hit. Nothing runs, and you never get another call's result. Numbers that are equal (10, 10.0,
`Decimal("10.00")`) count as the same.

**Checks:** K21 (three checks) and the crash matrix M4.

## G5. The record tells the truth about itself

**The promise.**

- The record only grows: rows are added, never changed or deleted.
- A damaged record gives a clear error, never a silent wrong answer.
- `call4agents verify` reports the record's integrity, format version and late results.
- `call4agents doubt` lists every action that may or may not have happened.

**Checks:**

- P1-P20 in `tests/test_c4a_production.py`, including a record from a newer version, a damaged file, a damaged
  row, a busy file, and a write refused with "database is locked".
- The stress test: 5 kills during writes leave the file intact.

**Not covered:** someone with write access to the file who rewrites it on purpose. The audit export
(`call4agents audit`) signs what was there when it was exported, not before.

## How we look for what we missed

- **Mutation testing:** every check above must fail when its bug is put back. Each batch includes a no-op
  control, which must not fail.
- **A formal model of the protocol**, checked with TLC: [FORMAL_MODEL.md](FORMAL_MODEL.md). It found the double
  payments fixed in Fix 17 and Fix 19, which our tests had missed because they crashed only one caller.
- **Property-based tests and fuzzing** of the record and the reconcile logic: [PROPERTIES.md](PROPERTIES.md).
  They found that some damaged rows gave a raw Python error instead of a clear one (fixed in 0.7.3), and on
  0.7.1 they find the double payment by themselves.
- **A crash-safety benchmark** that any framework can run on itself (in progress).

When a promise turns out to be wrong, the CHANGELOG says so: which versions are affected, who is affected, and the
fix. Fix 17 and Fix 19 are examples.
