# Team report: adapters-b (Google ADK, Microsoft Agent Framework)

Branch team/adapters-b, rebased on main 5b26492 (Fix 17). Not for publishing.

## Versions
google-adk 2.14.0, agent-framework-core 1.25.0: both the newest on PyPI (pip index, 11 Nov), so no
upgrade. Python 3.12.10, Windows 10. New venv with both: c4a-adapters-b-venv.

## Files
- call4agents/adapters/google_adk.py: effect, run_id, confirm (ADK tool confirmation glue)
- call4agents/adapters/agent_framework.py: effect, run_id, respond (approval_mode glue)
- call4agents/adapters/_common.py (shared; adapters-a also edits it). Exact change, nothing else:
  a module constant `_LOOK_UP = object()`, and wrap_tool gets a last parameter
  `ctx_name: Any = _LOOK_UP`; the line `ctx_name = context_param(f, type_names)` becomes
  `if ctx_name is _LOOK_UP: ctx_name = context_param(f, type_names)`; docstring extended by one sentence.
  Old callers behave as before. My adapters pass the name their framework itself injects (or None), so
  a model argument named `context`/`ctx` is never dropped from the key.
- call4agents/examples/google_adk_tool.py, agent_framework_tool.py: main(record, mode), scripted model
- tests/test_adapters_more2.py (--require-frameworks, --no-crashtest, --only-crashtest)
- tests/crashtest_agents/adk_agent.py (SqliteSessionService), maf_agent.py (session saved to a file)
- tests/mutations/run_kit_mutations.py: opt-in batch ADAPTERS_MORE2 + `--adapters-more2` (default
  inventory and count unchanged; does not use K29). The batch refuses to run with PYTHONPATH set, and a
  mutant counts as caught only if a check prints FAIL (a crashed suite is BAD).
- docs/frameworks/google_adk.md, agent_framework.md; .github/workflows/adapters-more2.yml
- Not done (shared files, for the lead): add "google_adk", "agent_framework" to ADAPTERS in
  docs/make_api.py and regenerate docs/API.md.

## Design decisions
- ADK run id = "adk:" + JSON [app_name, user_id, session_id]. Session ids are only unique per app and
  user in ADK, so all three. Not the invocation id: measured, a message sent again in the same session
  gets a new invocation id (2 sends); a resume with invocation_id keeps the session.
- Agent Framework run id = AgentSession.session_id (kept by to_dict/from_dict). Without session= the
  framework made a fresh random session per run (seen in a probe): then no protection; documented.
- Glue binds the yes at decision time to the arguments in the framework's request (not at run time,
  which would approve whatever runs). Glue refuses a tool not wrapped with approval=True.
- ADK #7148 (closed 7 Oct 2026, gh issue view): in 2.14.0 source, `apply_confirmation_gate` runs for
  every tool whose check_require_confirmation is True and returns "rejected" on a no. Read, not tested
  for custom BaseTools that ask only from inside run_async.

## Commands and numbers (all offline; socket guard: 0 non-loopback connects)
    ../c4a-adapters-b-venv/Scripts/python.exe -m tests.test_adapters_more2 --require-frameworks
  -> 31 checks, all pass, 3 min 50 s (crashtest part ~3.7 min). ADK crashtest: 9 crash points without
  the guard (repeat found), 11 with it (no repeat, all reached). Agent Framework: 5 / 7.
  Per framework: none=2, plain fails, adapter=1; approval: no yes refused, other args refused, exact
  args 1 (retry stays 1); glue: yes 1, call changed after the yes refused (control without kit: sent to
  "eve"), no-after-yes then old yes refused, unguarded tool refused by the glue.
  Skip path: ADK-only venv -> MAF "skip", with --require-frameworks "FAIL ... is installed" (and vice versa).
    env -u PYTHONPATH ../c4a-adapters-b-venv/Scripts/python.exe -m tests.mutations.run_kit_mutations --adapters-more2
  -> 16 entries: 2 controls MISSED, 14 mutants CAUGHT by named checks (context not excluded, wrong run
  id source, glue skips exact-args check, glue records no as yes, glue takes unguarded tool; x2
  frameworks; crash agent never/always guarded x2). "all mutations behave as expected".
  Regression (dev venv): tests.test_adapters pass (3 frameworks), tests.test_tickets pass.
  tests.test_c4a_kit: 1 FAIL in one run, then passed 3 runs (also passed with my changes stashed);
  I did not find which check failed (output not kept). Looks flaky; worth a look, not caused here as far
  as I can tell, not proven.
  Safe-edit scan: 0.

## Not proven
- Workflow checkpoints (MAF) and ADK resumable App / SQL session store with the glue: not tested here
  (crash cases without glue are in docs/finding/more_frameworks).
- The "changed after the yes" cases edit in-memory session / saved JSON, not a real database.
- CI workflow not run (no push allowed); only the local Windows run.
- Streaming, live tools, long-running tools, hosted tools: not tested.
