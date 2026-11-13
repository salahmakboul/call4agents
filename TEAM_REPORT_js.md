# TEAM_REPORT_js (not for publishing; the lead reads it and deletes it before merging)

Branch: team/js. New folder js/ (TypeScript port of the core + crash test + two adapters), new workflow
.github/workflows/js.yml. No Python file, README.md, CHANGELOG.md or version was changed.

## What was built
- js/src: canonical.ts (Python-identical canonical text and effect key), codec.ts, ledger.ts (same table,
  entry kinds, indexes, payloads; WAL; BEGIN IMMEDIATE admission; HMAC approvals; "database is locked" retried
  for 30 s like Fix 18), owner.ts + index.ts (effect, run, approve, deny, UNKNOWN, reconcile, retryOn,
  idempotentFor, key, lease, idempotencyKey; Fix 17 owner rules, giveUp once, nextAttempt = newest closed + 1;
  Fix 19 mark-before-claim; no token in the owner check), crashtest*.ts + cli.ts (bin `call4agents crashtest`,
  live_signs port, --test-accounts, --demo), adapters/openai-agents.ts, adapters/ai-sdk.ts.
- package.json: name call4agents, 0.1.0, private false, NOT published. Zero runtime dependencies;
  devDependencies: typescript 5.9, @types/node 24, @openai/agents 0.21.0, ai 7.0.170, zod 4.
- js/README.md (install from source, example per framework, same / not yet / differences, cross-language test).

## Commands (run inside js/)
    npm ci && npm run build
    set C4A_PYTHON=<python 3.9+>  C4A_REQUIRE_PYTHON=1  C4A_REQUIRE_FRAMEWORKS=1
    npm test                 # node --test, all files in test/*.test.mjs
    npm run mutations        # node test/mutations.mjs

## Measured (Windows 10, Node 24.19.0, Python 3.12 for the fixture; only Windows could be run here)
- npm test: MEASURED_TESTS
- Mutations: MEASURED_MUTATIONS
- Cross-language fixture: 35 of 35 argument vectors give the same canonical text and the same sha256 key as
  the Python kit (main, 0.7.3 code). Two known differences are asserted: Python 10.0 -> "10.0" vs JS "10";
  1e16 -> Python "1e+16", JS refuses (TypeError).
- Cross-language record (feasible, done): a Python "yes" is spent by JS and Python then replays the JS result
  without running; a JS "yes" + finished call is replayed by Python; a Python-recorded call is replayed by JS;
  a JS "no" cancels a Python "yes" (Python gets ApprovalRequired). Both kits give the same host name (X4).
- Sizes: dist .js 79.2 kB (13 files), .d.ts 16.5 kB; dist folder 143 kB on disk.

## Proven (by the tests above, on Windows)
- Once-only, replay, error -> in doubt at once, retryOn, reconcile result / null / UNKNOWN / undefined refused,
  approval bound to exact arguments, one yes = one action, deny revokes (also in doubt), KeyConflict,
  idempotencyKey stable.
- Real kills: a child process killed (child.kill SIGKILL = TerminateProcess on Windows) after the send and
  before the record -> resume is EffectInDoubt, outbox has 1 line; with reconcile the dead owner is detected
  and the found result recorded.
- Races: 2 child processes, one yes, same action -> 1 send; 8 child processes, one yes, 8 different keys ->
  exactly 1 runs, 7 ApprovalRequired; K29e kill inside the call then 2 resumers at once -> 1 charge (twice).
- Fix 17/19 in one process: slow call + second caller (K29a), error then two callers at once (K29b), a pause
  right after the claim (K30: the second caller, wait 0, reached reconcile during the awaits after the claim;
  before the Fix 19 port this test FAILED with 2 payments, measured; after it, 1 payment and reconcile not asked).
- process.kill(pid, 0) on Windows: 5 checks of a live child, the child still ended normally (exit 0, wrote its
  file); after exit the check says gone.
- Adapters with the real packages, offline: @openai/agents with a scripted Model (turn retried after a model
  error: 2 sends without, 1 with; approved run resumed from its saved RunState string: 2 without, 1 with);
  ai 7 with MockLanguageModelV4 (generateText retried after a model error, new toolCallId: 2 without, 1 with).
- Crash test: unguarded agent -> RAN TWICE found (exit 1), guarded -> none (exit 0), every point reached by a
  real kill; --demo gives (1, 0); CT8 refusal cases as in tests/test_crashtest.py.

## NOT proven / limits
- macOS, Linux and Node 22 were not run here (only the workflow is written; first CI run will tell).
- Admission-atomicity mutation: removing only the transaction is masked by the UNIQUE indexes (as designed);
  the mutation removes the transaction AND the one_use index, and the 8-process test is timing-based.
- Crash test watches only top-level `function` declarations in an ES module script (no arrow functions,
  methods, CommonJS); the action-name scan is a word heuristic, not the Python AST scan.
- Not ported: tickets, Limit, Rules, retries/backoff, undo, audit, spans, replay/diff/prove, Postgres, CLI
  commands other than crashtest. A tool's default argument values are not filled in.
- Not published to npm (needs the owner's npm account).
