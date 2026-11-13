# Team report: adapters-a (CrewAI, LlamaIndex, smolagents)

Branch team/adapters-a. Not for publishing; the lead reads and deletes it.

## Versions tested (Windows 10, Python 3.12.x)

One venv, `c4a-more-venv` (next to the worktrees): `pip install crewai llama-index-core smolagents`
installed together, `pip check`: no broken requirements. crewai 1.15.39, llama-index-core 0.14.25
(llama-index-workflows 2.26.1), smolagents 1.26.0, pydantic 2.12.5. The CrewAI example also ran in
c4a-crew-venv (crewai 1.15.33), same results.

## What I found in the framework source (and measured)

- CrewAI: `@tool` / BaseTool, no context object passed to a tool. Run id: the crew's `id`, read from
  CrewAI's own run context (`get_crew_context()`). Kept by CrewAI's task retry (measured: same id on
  both tries) and by `Crew.from_checkpoint` (measured once by hand, BaseTool tool). A new Crew gets a new
  id and the id cannot be set by the user (validator), so a fresh start needs `with c4a.run`.
  `current_flow_id` is NOT usable: inside a crew it is the agent executor's internal flow.
  Checkpoint with a `@tool` function tool fails in 1.15.x ("Unable to serialize unknown type: function").
- LlamaIndex: FunctionTool injects a `ctx: Context` parameter (by annotation). There is no id that
  survives a resume: each run, also one resumed from `Context.from_dict`, gets a new workflow run id,
  and one process refuses to reuse a run id ("already exists"). So the adapter takes the run id only
  from `with c4a.run` or `run=`, and refuses otherwise. Measured: a resume from a Context saved at the
  ToolCall event re-runs BOTH in-progress steps (call_tool and parse_agent_output), so the tool runs
  twice in the resumed run: 3 emails in all without the kit.
- smolagents: `@tool` / Tool.forward, no context object, no run/session id at all: `with c4a.run` or
  `run=` required. Measured: CodeAgent (default `timeout_seconds`) runs the model's code in a
  ThreadPoolExecutor without copying the context, so `with c4a.run` is not seen in the tool (plain
  c4a.effect fails "no current run"; the adapter fails with a message naming the fix).
  `executor_kwargs={"timeout_seconds": None}` keeps it (measured). ToolCallingAgent copies the context.

## Honest about "plain c4a.effect"

- CrewAI and smolagents (ToolCallingAgent): plain c4a.effect works inside `with c4a.run` (tested: 1).
  The adapter adds: the crew id as run id (CrewAI), `run=`, clear errors, approve() for the framework's
  tool objects. Without a run, plain fails with "no current run" (tested).
- LlamaIndex: plain fails on a tool with a Context parameter (TypeError, tested); without a Context
  parameter plain inside `with c4a.run` gives 1 (tested).

## _common.py change (for the merge)

One new function at the end, `wrap_plain_tool(f, run_from, what, options, context=None)`, 8 lines. It
calls the adapters-b `wrap_tool(..., ctx_name=context)` with `type_names=()` and sets
`options["run"] = options.get("run") or (lambda ctx: run_from())`. Why: (1) run_from takes no ctx
(CrewAI reads the crew id from its own context var); wrap_tool only calls run_from when a ctx exists;
(2) no parameter is left out for its name alone (in CrewAI/smolagents `context: str` is model text;
with `ctx_name=None` the name lookup is skipped). wrap_tool and context_param are unchanged.

## Other shared files

- tests/mutations/run_kit_mutations.py: `adapters_more2()` became `adapter_batch(batch, test_file)`,
  used by `--adapters-more2` (unchanged behaviour) and the new `--adapters-more`; new list ADAPTERS_MORE.
- docs/make_api.py already listed crewai/llamaindex/smolagents; docs/API.md regenerated after the rebase.
- tests/test_adapters_more.py has `--no-crashtest`, `--only-crashtest`, `--framework NAME`.

## Commands and measured results (Windows, c4a-more-venv, run from the worktree root)

- `python -m tests.test_adapters_more --require-frameworks --no-crashtest`: 30 ok, 0 FAIL, about 1 min.
  Without the kit: CrewAI 2 emails (its own task retry), LlamaIndex 3 (resume from a Context saved
  at the ToolCall event), smolagents 2 (job retried), CodeAgent 2. With the adapter: 1 each.
  Approval: no yes 0, yes for other args 0, yes for exact args 1, for all three.
  Socket guard: 0 connections or name lookups left the machine.
- `python -m tests.test_adapters_more --require-frameworks --only-crashtest`: 6 ok, 0 FAIL, 45 min
  (CrewAI is most of it: about 25 s per start). Crash points tested without / with the guard:
  CrewAI 7 / 9, LlamaIndex 6 / 8, smolagents 5 / 7. Repeat found without the guard, none with it, every
  point reached. An earlier run failed only because one CrewAI start passed the 120 s child timeout
  under load; the test now passes timeout=600 to crashtest().
- `python -m tests.mutations.run_kit_mutations --adapters-more` (PYTHONPATH unset): 16 mutants, all
  caught by a named FAIL line; 2 controls (adapter suite, crashtest suite) missed. First run: 1 BAD
  ("run= ignored" crashed the suite instead of printing FAIL); fixed the test (catches the error and
  prints FAIL); re-ran that mutant plus the control: caught / missed.
- Unchanged suites still pass after the _common change: tests.test_adapters (dev venv, 3 frameworks),
  tests.test_adapters_more2 --no-crashtest (adapters-b venv, 2 frameworks), tests.test_c4a_kit.
- safe-edit scan: 0.

## Not proven / not done

- CI workflow `.github/workflows/adapters-more.yml` (ubuntu/macos/windows, Python 3.12, no pins) is
  written but NOT run: no push. Install on Linux/macOS not checked here; no OS is excluded. The job
  runs the crashtest too (45 min on this machine), so timeout-minutes is 90. If CI time matters, add
  `--no-crashtest` there.
- CrewAI crashtest mutations are not in the batch (too slow); LlamaIndex and smolagents crashtest
  checks are mutation-proven.
- CrewAI: only the text (ReAct) tool path; native function calling not tested. `from_checkpoint`
  keeping the crew id was checked once by hand (BaseTool), not in a test; checkpoints with @tool
  function tools fail in CrewAI 1.15 itself.
- LlamaIndex: only FunctionAgent + FunctionTool. smolagents: no managed agents, no remote executors.
- crashtest's `fix_hint` has no CrewAI/LlamaIndex/smolagents line (it prints the generic hint). Not
  changed, to stay out of crashtest.py.
- CrewAI writes its own data under the user's app-data folder (one folder per working-folder name).
