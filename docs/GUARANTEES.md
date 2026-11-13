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
- K29a-e: a call still running is not treated as a crash, and two callers that give up the same attempt start
  only one new one. This includes real processes: one is killed inside the call, then two resume at once.
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
  raced (Fix 17, in the CHANGELOG).

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
- A "yes" has no expiry yet. Approval tickets (`c4a.pause`) do have one. Expiry for `c4a.approve` is planned for
  1.0 (see [PLAN_1_0.md](PLAN_1_0.md)).
- A "yes" is not bound to the tool's version yet. This is also planned for 1.0.

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
- **A formal model of the protocol** (TLA+, in progress). It already found the double payment fixed in Fix 17,
  which our tests had missed because they crashed only one caller.
- **Property-based tests and fuzzing** of the record and the reconcile logic (in progress). They found that some
  damaged rows gave a raw Python error instead of a clear one.
- **A crash-safety benchmark** that any framework can run on itself (in progress).

When a promise turns out to be wrong, the CHANGELOG says so: which versions are affected, who is affected, and the
fix. Fix 17 is one example.
