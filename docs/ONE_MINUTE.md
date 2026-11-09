# Call4Agents in one minute

**One yes, one payment, even after a crash.** Call4Agents sits between your agent's decision and the real action.
It writes the call down before it runs and reads that record back before anything runs again. Every row below is
one problem, the line that handles it, what we measured, and a command you can run offline to see it.

| Problem | What goes wrong | The line | Measured | See it |
|---|---|---|---|---|
| Crash right after an approved action | The documented resume runs it again | `@c4a.effect(approval=True)` | 2 payments in LangGraph, Pydantic AI, OpenAI Agents SDK (30/30 runs each), Google ADK and Microsoft Agent Framework (5/5 each); 1 with Call4Agents in the first four ([finding](finding/README.md)) | `call4agents crashtest --demo` |
| The stored call is changed after the yes | The changed call runs with the old yes | `approval=True` (the yes is an HMAC over the exact arguments) | LangGraph and Pydantic AI paid 9999 to mallory; with the guard nothing was paid | `call4agents incident` |
| One yes, many callers | Several workers spend the same yes | `approval=True` (one yes = one use) | 8 simultaneous actions on one yes: exactly 1 runs (K17d) | `python -m tests.test_c4a_kit` |
| Timeouts and retries against a payment API | The retry charges again | `@c4a.effect(idempotent_for=3600)` (how long the API keeps keys) | 12 processes killed at random over 300 orders: 0 double charges; without it, 244 of 300 charged twice or more | `python -m tests.bigtest_c4a` |
| Retries that repeat actions or hide errors | A blind retry charges twice, or swallows the real error | `retries=3` with `retry_on=`, `idempotent_for=` or `reconcile=` (next release) | Retries only when the record decides the next try; other errors raised at once; same-key resend: 1 charge (K26-K26i) | `python -m tests.test_c4a_kit` |
| A crash during the call | Nobody knows if it happened | `reconcile=` asks the provider | Stops as `EffectInDoubt` instead of guessing; 412 crashes at every write, 0 double charges | `python -m tests.test_crash_matrix` |
| The provider can't tell yet | A lagging lookup says "not found" and the action runs again | `reconcile=` returns `c4a.UNKNOWN` (next release) | Stays in doubt, nothing runs (checks K25, K25b) | `python -m tests.test_c4a_kit` |
| Several machines | Two machines run the same action | `c4a.init("postgresql://...")` (beta) | 8 processes on one PostgreSQL record: runs once | `python -m tests.test_shared_record` |
| Which tools are unguarded? | Nobody knows until an incident | `call4agents scan <path>` | Reads source, runs nothing; what it misses is in [SCAN_RESULTS.md](SCAN_RESULTS.md) | `call4agents scan .` |
| Does my own agent repeat? | You find out in production | `call4agents crashtest`, the GitHub Action, the pytest fixture (next release) | Finds the repeat in small real agents on three frameworks, none with the guard | `call4agents crashtest your_agent.py` |
| Who approved what, and what ran? | No record you can hand over | `call4agents audit <record> out.json` (experimental) | Signed export; a changed, removed or moved entry is detected (A3, A4). Anyone with the secret could sign a false one | `python -m tests.test_audit` |

**Not proven:** real payment or email systems and production traffic; several real machines on a real network;
`crashtest` and `scan` on real production agents; an independent code review. Rows marked "next release" are on
`main` but not on PyPI yet. Each guard is mutation-tested: its bug is put back and its check must fail.

Install: `pip install call4agents` (standard library only). Full details: [README](../README.md).
