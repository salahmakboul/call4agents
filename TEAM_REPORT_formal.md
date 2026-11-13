# TEAM_REPORT_formal (not for publishing)

Branch `team/formal`, rebased on main 5b26492 (Fix 17).

## Findings first

1. **0.7.1 double payment with a truthful reconcile=** (found by the model, then reproduced with the real
   0.7.1 library: `formal/repro_reconcile_race_071.py`, 2 payments in both of its scenarios, one run each).
   It is fixed on main as Fix 17. Seeded configs `bug_reconcile_live_owner`, `bug_give_up_twice`, `bug_v071` put
   it back; TLC finds it (22 / 29 / 22 states). The same script on main: 1 payment in both scenarios.
2. **NEW, still on main after Fix 17: a same-process window.** `_owner.started()` runs after `check()` has
   returned (`__init__.py` 545 async, 575 sync). Between the intent commit and `started()`, another thread
   of the SAME process sees "owner gone", reconciles truthfully ("not paid"), gives the attempt up and pays;
   then the first thread pays too. TLC: `finding_threads_window`, AtMostOnce violated, 22 states.
   Reproduced on main 3 out of 3 runs with `formal/repro_owner_window.py`: (a) a 0.5 s pause injected after
   the claim, 2 payments; (b) NO library change: an asyncio call whose event loop is blocked by another task
   for 0.8 s (> `wait=0.05`) plus a thread of the same process calling the same action, 2 payments.
   It is flagged afterwards (`late_result` + a Python warning), not silent. With the default `wait=2.0` the
   loop or thread must be stalled for more than 2 s. Separate processes are not affected.
   Proposed fix (checked in the model only, config `reconcile_threads_start_early` passes, 1,393,360 states):
   mark the attempt as in progress BEFORE its claim (and unmark it when the claim fails). Not implemented:
   it is the lead's call (security rule: serious fix within 7 days).
3. Harmless: two `tool_done` rows for one action (`finding_done_twice`, DoneOnce violated, 21 states): a caller
   whose wait ended without a poll that sees the result (`wait=0`, or a thread paused past the deadline)
   reconciles an attempt whose owner just finished, and records the result a second time. The action
   happened once. DoneOnce is kept as a property, not weakened.

## What I ran (Windows 10 laptop)

- Tools in `c4a-tools/` (next to the worktrees): Temurin `jdk-21.0.13+9` zip (sha256
  a4dbaa05...04f9, matches the release's .sha256.txt) and `tla2tools.jar` from tlaplus release **v1.7.4**
  (the newest non-prerelease tag; sha256 936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88,
  the release lists no digest, so this is the hash of the file I downloaded; CI pins it).
- `python formal/run_tlc.py --tla2tools <jar> --java <java>`: **24 configs, all as expected** (9 pass,
  2 findings fail as expected, 13 seeded bugs each caught with the named property), 12,182,980 distinct
  states in total, 594 s. Largest: idem_processes 4,729,851 states, 253 s. Table in docs/FORMAL_MODEL.md.
- `python formal/run_tlc.py --selftest`: 12 ok. Mutation: reading the first state count instead of the
  last (the bug I made first: progress lines) fails the selftest.
- Model mutants of the CORRECT spec (small bounds: reconcile, 2 workers, 2 calls): control passes (32,138
  states); owner check always "gone" -> AtMostOnce (22); "no" ignored on resume -> NoUnapprovedEffect (16);
  `give_up` never refusing -> MISSED (80,330 states, no error); next attempt by counting rows -> MISSED.
  The two misses are not a blind model: each half of Fix 17 part 2 alone is enough for AtMostOnce in the
  model; removing both (bug_give_up_twice) is caught. Reported in FORMAL_MODEL.md. (A first try of these
  mutants used a sed pattern that did not match: 0 targets, so that run was only a control. Redone with an
  exact-text replacement that checks the count.)
- `python -m tests.test_formal_trace`: 8 ok (72 crash/resume scenarios, 216 real processes, about 90 s).
- Trace-check mutations (my 5 entries of run_kit_mutations only): 4 of 4 caught (owner check removed -> T6;
  deny not checked in doubt, error = not done, reconcile result ignored -> T1-T5), control MISSED.
- Safe-edit scan: 0 files.

## Proven (in the model, within its bounds)

On main (Fix 17), separate processes: AtMostOnce, NoUnapprovedEffect, OneYesOneAction, DoneIsTrue and
AppendOnly hold for plain, approval, second-action, reconcile (truthful) and idempotent_for configs, with
crashes at every step, errors before and after the effect, and the person saying yes/no at any time.
Progress: every call ends (fair workers), and with no crash an admitted action gets its result.

## NOT proven

That the code matches the model (the trace check links them for 72 scenarios only); SQLite/PostgreSQL
durability and the atomicity of BEGIN IMMEDIATE; HMAC; clocks (`idempotent_for`, `lease`, ticket ttl): the
model ASSUMES no attempt runs longer than `lease`; Limit and Rules; tickets; more than 3 workers / 3 calls /
2 yes / 1 no; a provider that lies or lags. Bounds were made smaller for 3-worker configs (1 "yes"
in plain_approval, 0 "no" in plain_second_action) to finish in under 5 minutes; properties were not weakened.

## Notes for the lead

- Rebased on main 2da008a (0.7.2 + GUARANTEES.md). After the rebase: selftest ok, trace check 8/8 ok, and
  `formal/repro_owner_window.py` still pays twice in both modes, so finding 2 holds on 0.7.2. GUARANTEES.md
  G1 does not mention it yet; FORMAL_MODEL.md maps G1-G3 to the invariants and says so.
- TLC numbers are from the run before this last rebase; __init__.py and ledger.py did not change since 5b26492
  (only owner.py: the token comparison), so the model is the same.
- A spec mutant on the big reconcile_processes config (give_up never refusing, 3 calls each) ran more than
  20 minutes without finishing; I stopped it and used small bounds for all spec mutants.

- The upcoming owner.py change (no token comparison) does not change the model: it abstracts the owner check.
- The trace check is `tests/test_formal_trace.py` (not `formal/trace_check.py`) so the mutation runner can
  copy and run it like the other suites; added to `copy_tree`.
- New workflow `.github/workflows/formal.yml` uses `actions/setup-java@v4` (asked for in the task; not in
  the brief's list of pinned actions). It has not run on GitHub yet.
- `formal/cfg/*.cfg` are generated (`run_tlc.py --write-cfgs`); the runner fails if they drift from the table.
