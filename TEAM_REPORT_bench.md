# TEAM_REPORT_bench (for the lead; delete before merging)

Branch team/bench, rebased on main 13b7ad6 (Fix 19 included). Folder bench/ + .github/workflows/bench.yml +
a `--bench` batch in tests/mutations/run_kit_mutations.py (main's `--suite` filter kept).

## What was built
- bench/subject.py: the contract (start, resume, start_approval, decide, resume_approved, tamper,
  not_applicable), Env with the fake bank (SQLite file shared by all processes) and the faults (kill after /
  inside / before the action, timeout after the charge, "unknown" lookup, S3 barrier + 1 s bank delay).
- bench/subjects: plain loop, LangGraph (StateGraph + SqliteSaver, interrupt, invoke(None)), OpenAI Agents SDK
  (SQLiteSession; needs_approval + saved RunState), Pydantic AI (agent.iter + saved history; requires_approval +
  DeferredToolResults). Scripted models only. Each in default and with call4agents (adapter, approval=,
  reconcile= bank lookup).
- bench/run.py: every step a new process; counts bank rows; INVALID when a setup point is not reached, when a
  variant imported another call4agents than it names, or when control C1/C2 fails (gate). Variants: default,
  call4agents 0.7.1 and 0.7.2 (from the git tags, PyPI fallback), and this checkout.
- bench/scenarios.md, bench/README.md (B1), bench/RESULTS.md + bench/results/<date>-windows.json (measured).
- bench/selfcheck.py: B1-B6 with three wrong-on-purpose control subjects.
- .github/workflows/bench.yml: workflow_dispatch + weekly, ubuntu/macos/windows, selfcheck then bench,
  JSON uploaded as artifact, nothing committed. (The task asked for dispatch + schedule, so it has no push/PR
  trigger, unlike the brief's default.)

## What I ran (Windows 10, Python 3.12.10, c4a-dev-venv)
- `python -m bench.run --require-frameworks --workers 6` (1126 s, 160 cells, 0 INVALID, exit 0). Wrote
  bench/results/2026-11-13-windows.json and bench/RESULTS.md. Versions: langgraph 1.2.16 (+ checkpoint-sqlite
  3.1.2), openai-agents 0.24.0, pydantic-ai-slim 2.59.0; call4agents 0.7.1 (tag), 0.7.2 (tag), head cd75e22.
- An earlier full run (before the rebase; head = b9868eb, whose call4agents/ is byte-identical to v0.7.2) gave
  the same cells. Not committed; superseded.
- `python -m bench.selfcheck` (no PYTHONPATH): 8/8 ok.
- `python -m tests.mutations.run_kit_mutations --bench` (no PYTHONPATH, system Python): 8/8 mutants caught by a
  FAIL line, the no-op control MISSED. Run twice (before and after the last rebase), same result.
- safe-edit scan: 0 files with control characters. gh calls: 11 of 30.

## Measured (counts from the fake bank; crash cells 5 repeats)
- Every framework default fails S1, S2, S3, S7 (5/5: 2 actions; S2 1/1: 2).
- Native passes (no call4agents): LangGraph S5; OpenAI Agents SDK S4 (the SDK refuses the changed call:
  "Model reused a tool call ID for a different invocation"), S5, S6 (a reject saved after the approve wins);
  Pydantic AI S5, S8 (refuses DeferredToolResults with another run's tool call id).
- Default fails: LangGraph S4 and S8 (resume value not tied to a thread), Pydantic AI S4. N/A: S6 for LangGraph,
  Pydantic AI and the plain loop (the yes is only the resume message); S8 for OpenAI Agents (no decision message).
- call4agents 0.7.1 fails S3 in all four subjects, 5/5: 2 actions (the concurrent reconcile race, Fix 17).
  0.7.2 and head pass every scenario. 0.7.2 vs head: no cell changed. Fix 19 (a window between threads of ONE
  process) is not reachable by this benchmark, whose workers are separate processes. Not shown, not claimed.
- With call4agents, a pass is not always a finished run: S7 stops with EffectInDoubt (plain, LangGraph,
  Pydantic AI); in the OpenAI Agents SDK the in-doubt error goes to the model as text and the run "finishes".

## Bugs found in my own subjects during the work (fixed, all before the committed run)
1. LangGraph resume skipped invoke(None) when get_state().next was empty; after a kill during the async
   checkpoint write it IS empty while invoke(None) still runs the tool. Gave a false "0 actions".
2. Runs A and B shared state files in S8 (file-based subjects): A's state was overwritten by B's.
3. The S3 barrier released workers before the framework imports, so one could finish before the other
   started: the OpenAI Agents SDK showed a false native S3 pass. Barrier now after imports, bank delay 1 s.

## Not proven / open
- Windows only. CI (bench.yml) has not run yet; Linux/macOS untested.
- S3 is timing-based (likely, not certain, overlap). 5/5 repeats each.
- The plain loop is our own simple baseline, not a framework; it is labelled so.
- Subjects use the documented default as I read it; maintainers may disagree (README invites corrections).
  Durable-execution add-ons (Temporal etc.) and LangGraph @task / durability="sync" are not tested.
- The cited issues: langgraph#9676 (open, read), openai-agents-python#4254 (closed question, read). The README
  does not claim more than their text says.
