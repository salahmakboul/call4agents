# TEAM_REPORT_props (Head of R&D, property tests) - not for publishing

Branch team/props, rebased on main 6cc8d2b (0.7.2). Venv: c4a-props-venv next to the worktrees
(system Python 3.12.10, hypothesis 6.170.15). All runs below WITHOUT PYTHONPATH (L73).

## Findings (top)

1. Fix 17's double payment. I found it on 0.7.1 (plain repro: slow effect, wait=0.3, truthful reconcile, a
   second caller -> 2 outside actions with one yes, late_result warning) before Fix 17 reached main. Main's
   Fix 17 fixes it, so I did not touch __init__.py. The machine has the case as a deterministic rule
   (`still_running_and_a_racer`: caller 1 waits on an event inside the action; no timing luck) plus
   `two_resumes` (two callers resume a crashed call at once). Against a v0.7.1 worktree with only the final
   test file copied in, Hypothesis finds the double payment and shrinks it to `start(item='a',
   behavior='ok')`, `still_running_and_a_racer(item='a', key='k1')` -> "outside actions per key > 1:
   ['res:a:k1:2', 'res:a:k1:3']". On 0.7.2: passes.
2. PF1 (minor, fixed in ledger.py). File fuzzing found damaged rows that raised a raw Python error, not the
   LedgerError that P15 promises ("row N is damaged ... run call4agents verify"). Survey on 0.7.2-pre code:
   3,000 damaged copies, 76 raw errors. Exact cases (plain repro script, 4 of 4 raw before, 0 of 4 after):
   - an approval/denial row that is not JSON -> JSONDecodeError (`_matching_in` used json.loads, not
     `_payload`; 63 of the 76)
   - an effect_approved row without "approval" -> KeyError('approval') in claim_approved
   - a tool_done row without "result" -> KeyError('result') in effect()
   - a format version that is not a number -> ValueError at open (also UnicodeDecodeError at open from a
     damaged page, seen in the survey)
   Did ANY of them let an action run? NO. In all 3,000 survey copies and every fuzz run, A (done), B (in
   doubt) and D (never approved) never ran, B never came back as done, C never ran twice. So it is an
   error-message bug, not a safety bug.
   Fix (ledger.py only, ~15 lines): `_payload(seq, text, kind)` checks the one field each of tool_done /
   effect_approved / approval / denial is read for; `_matching_in` reads through `_payload`; opening
   catches ValueError next to sqlite3.DatabaseError. Named check PF1 (4 hand-damaged rows) + 3 mutations.
3. My mistakes, fixed in the test, not the kit: (a) the model said a reconcile for a finished key is wrong;
   two concurrent resumes may both ask and both record the same found result (no outside action) - now
   allowed for keys called from threads only. (b) The model expected reconcile before a refusal; the kit
   refuses first when a "no" came after the yes was spent (documented). (c) The first mutation run MISSED
   8 machine mutations: in 30 derandomized runs Hypothesis almost never picked `call`. Now: call weighted
   x2, an approve_then_call rule, an @initialize approved call per run, and a coverage check that FAILS if
   any of 15 paths (8 behaviors, 3 reconcile answers, racer refused, threads, resumes) was never taken.
4. Known, by design, not fixed (in docs/PROPERTIES.md): effect_key("a\x00b","c",..) ==
   effect_key("a","b\x00c",..) (NUL in run id/tool; a fix would change every key, so a resume after upgrade
   would rerun in-doubt actions); {1: x} and {"1": x} give one key; bytes and {"__bytes__": hex} give one key;
   a lone surrogate in an argument raises UnicodeEncodeError (loud). A flipped byte inside a stored result
   would replay a wrong value silently (no row checksum; verify does not see it); measured 0 times.

## Commands and numbers

- `python -m tests.test_properties --require-hypothesis` on 0.7.2: all passed, 19.6 s on this laptop when
  idle (machine 11.0 s, canonical 4.3, codec 1.7, files 2.6). Under load from other jobs: 90 to 300 s.
  Examples: machine 40 runs x 20 steps (249 call steps, 23 racer runs all refused with EffectInDoubt), 200
  per canonical/codec property + 200 sets under 3 hash seeds, 200 damaged copies (94 clear errors, 0 wrong
  results). `--long` (500 x 50, 3,000 each) has NOT been run yet.
- Kit suites after the ledger.py change, on 0.7.2: test_c4a_kit, test_c4a_production, test_tickets,
  test_audit, test_undo, test_idempotency: exit 0, 0 FAIL lines.
- `python -m tests.mutations.run_kit_mutations --suite tests.test_properties` (new filter; default run
  unchanged): control MISSED (ok); 16 of 16 CAUGHT: yes never spent, no ignored, no ignored in doubt,
  every error = did not happen, reconcile None = done, UNKNOWN = None, tool_done twice, finished call not
  replayed, no owner check (Fix 17) [machine]; codec calls what is named, tuple as list [codec]; dict order,
  any str() [canonical]; 3 x PF1 [files]. Measured on the commit before the rebase (main's rebase changed
  only the runner's argument parsing). A 17th entry, "record overwrites a row" (INSERT OR REPLACE), was
  MISSED and removed: equivalent on this path (claim_approved checks the intent inside its transaction,
  so no unique conflict reaches the INSERT).

## Not proven

One process only; crashes are exceptions and a failing write, not real kills; honest provider only; no
limits, rules, tickets, undo, async, idempotent_for or PostgreSQL in the machine; CI workflow not yet run
on GitHub (macOS/Linux untested by me); the weekly long run not yet run.

## Files

tests/test_properties.py (new), tests/mutations/run_kit_mutations.py (17 entries incl. control, --suite,
copies test_properties.py), call4agents/ledger.py (PF1), .github/workflows/properties.yml,
docs/PROPERTIES.md. No README/CHANGELOG/version/tests.yml changes.
