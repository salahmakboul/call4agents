# An open crash-safety benchmark for agent tool calls

This benchmark asks one question of an agent framework: **when something goes wrong around a tool call that
changes the outside world, does the action happen the right number of times, with the arguments a person
approved?** Any framework or project can run it on itself. The results table is [RESULTS.md](RESULTS.md).

It is offline: scripted models, no API keys, no network. Crashes are real process kills. Actions are counted
by a fake bank, an SQLite file that every process writes to, never by what a model or a framework reports.

## Why it exists

Agent frameworks save their state between steps, so a run can resume after a crash or wait for a person.
The step that runs a tool sits between two saves. If the process dies after the tool acted and before the
next save, the resumed run does not know the action happened. Approvals have a similar gap: the "yes" and the
call that later runs are not always tied to each other.

These cases are known and discussed in the frameworks' own trackers, for example:

- LangGraph [#9676](https://github.com/langchain-ai/langgraph/issues/9676) (open): with parallel tool calls under
  `HumanInTheLoopMiddleware` and `durability="exit"`, an approved call can be asked again and run twice.
- OpenAI Agents SDK [#4254](https://github.com/openai/openai-agents-python/issues/4254) (closed, a question): when a
  run retries, a tool with a side effect can run twice, and `tool_call_id` is not a stable key for it.

None of this is special to one framework. A hand-written loop has the same gaps (the plain loop subject shows it),
and each framework closes some of them already (see "Passed without call4agents" in RESULTS.md).

## What it measures

Ten cells per subject and variant: two controls and eight scenarios. Full rules: [scenarios.md](scenarios.md).

| id | story | pass |
|---|---|---|
| C1 | a run with no fault | 1 action |
| C2 | an approved call | 1 action |
| S1 | crash after the action, before the framework saves; resume | 1 action |
| S2 | the bank charged, but the call raised a timeout; retry | 1 action |
| S3 | two workers resume the same run at once | 1 action |
| S4 | a yes for 50; the saved call changes to 5000 before it runs | no action with 5000 |
| S5 | one yes; the agent calls the tool twice | 1 action |
| S6 | a no after the yes, before it runs | 0 actions |
| S7 | crash inside the action; the bank cannot tell yet if it happened | 1 action |
| S8 | the yes of run A is delivered to run B | 0 actions in B |

Each subject runs in four variants: the framework's **documented default**, and the same agent with the tool
guarded by **call4agents 0.7.1** (the release before Fix 17), **call4agents 0.7.2**, and **call4agents from this
checkout**. The older releases stay in the table on purpose: 0.7.1 fails S3, and the table shows that the fix
passes it.

## Run it

```
pip install langgraph langgraph-checkpoint-sqlite pydantic-ai openai-agents
python -m bench.selfcheck          # first: the benchmark can see a duplicate and a wrong amount (about 30 s)
python -m bench.run                # all installed subjects; crash cells 5 times each
python -m bench.run --subjects langgraph --scenarios S1,S4 --repeat 3 --out /tmp/bench
```

`bench.run` writes `bench/results/<date>-<os>.json` and `bench/RESULTS.md`. With `--out DIR` it writes both there
instead. A subject whose framework is not installed is skipped with a message (`--require-frameworks` makes that
an error). Each released call4agents comes from its git tag (`v0.7.1`, `v0.7.2`), or from PyPI when the tag is
missing; `--release 0.7.1,0.7.2` picks the releases (oldest first) and `--no-release` leaves them out.

## Add your framework in about 10 minutes

1. Copy the closest file in [subjects/](subjects/) to `subjects/<your_framework>.py`.
2. Implement the contract in [subject.py](subject.py): `start` and `resume` (a run without approval),
   `start_approval`, `decide`, `resume_approved` and `tamper` (a run that waits for a yes). Each method runs in a
   new process, so save what the next one needs under `env.path(name)` the way your framework documents it.
3. The tool body is always `result = env.pay(amount, to); env.after_action(); return result`. Do not code the
   faults: the benchmark injects them there.
4. Use your framework's own test or scripted model, following `next_move()` in subject.py.
5. If a scenario cannot apply (for example, no approval feature), return the reason from `not_applicable()`. The
   cell then shows N/A with that reason. It is not a failure.
6. Register the class in [subjects/\_\_init\_\_.py](subjects/__init__.py) and run
   `python -m bench.run --subjects <name> --repeat 1`. C1 and C2 must pass first: if they do not, the benchmark
   marks the cells they stand for INVALID instead of counting them.

## Submit a result

Open a pull request with:

- the JSON file written by `bench.run`, unchanged (it holds the Python, framework and call4agents versions,
  and every step's exit code and error line);
- the subject code you used, in `bench/subjects/` (results from code that is not public are not accepted);
- the operating system in the file name, as `bench.run` writes it.

The CI workflow `.github/workflows/bench.yml` runs the benchmark every week on Linux, macOS and Windows and keeps
the JSON as an artifact. It does not change RESULTS.md; only a reviewed PR does.

## Fairness

- Each framework runs in its **documented default** configuration. What each subject does is listed at the end of
  RESULTS.md and in the docstring of its file.
- A framework that handles a scenario natively **passes without call4agents**, and RESULTS.md says so in its own
  section, "Passed without call4agents".
- If a subject does not use your framework the way its documentation says, open an issue or a PR with the
  correction. We rerun and replace the result.
- call4agents is measured the same way. Its failures are in the same table and in their own section,
  "Failed with call4agents". An older release stays in the table when a newer one fixed something.
- Every count comes from the fake bank. A cell whose setup did not happen as designed (the kill was not reached,
  the run did not stop for a yes, the variant imported another call4agents than it names) is INVALID, never a pass.

## What it does not show

- Only these faults, at these points, with one tool and a scripted model. A pass is not a proof for other paths.
- Durable-execution add-ons that need a server (for example Temporal, DBOS or Prefect integrations) are not used.
  Subjects that use them are welcome.
- The fake bank has no idempotency keys. A real provider that removes duplicate requests by key helps in S1-S3 with
  any framework, if the tool sends a key that stays the same across restarts.
- S3 is a real race between two processes. The bank waits 1 s per call so the workers overlap, which makes the
  race likely, not certain. Crash cells are repeated for this reason.
- The committed results were measured on Windows only, so far.
