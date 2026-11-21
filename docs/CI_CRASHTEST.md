# Does your agent pay twice after a crash? Run this in CI

One command, or five lines in a GitHub workflow. It kills your agent at every step, restarts it, and fails the job
if an action ran twice or ran without the approval the clean run asked for. Your agent does not need to use
Call4Agents first. Experimental.

## The command

```
pip install call4agents
call4agents crashtest agents/refund_agent.py --test-accounts
```

Exit code: **0** nothing repeated at the points tested, **1** an action ran twice or without approval, **2** the
script could not run (or it was refused, see below). Any CI system that fails a job on a non-zero exit code can run
it as it is. Add `--json` for a machine-readable report, `--watch name,name` for more function names to treat as
actions, `--max-crashes N` to test fewer crash points.

**Every action of the script really runs, many times.** `--test-accounts` says the script talks only to fakes,
sandboxes or test accounts. Without it the command runs nothing. With it, it still refuses to start on a live
payment key in the environment or in the script, or a provider set to live mode (exit 2; the message names the
variable, never the key).

## GitHub Actions

Add a workflow file, for example `.github/workflows/crashtest.yml`:

```yaml
name: crashtest
on: [push, pull_request]
jobs:
  crashtest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: salahmakboul/call4agents@v0.7.3   # pin a release tag or a commit SHA
        with:
          script: agents/refund_agent.py
          test-accounts: "true"      # required: every action really runs, many times
          watch: refund              # optional: more function names to treat as actions
```

If your agent needs its own packages, install them in a step before the Action (`pip install -r
requirements.txt`). The Action sets up Python 3.12 and installs the call4agents release it names.

| input | required | default | what it does |
|---|---|---|---|
| `script` | yes | | the agent script to test; it must be able to restart (a fixed run id, state saved in its folder) |
| `test-accounts` | yes | | must be `"true"`; any other value fails the job before anything runs |
| `watch` | no | `""` | extra function names to treat as actions, comma separated |
| `max-crashes` | no | `"200"` | most crash points to test |
| `install` | no | the current release | what pip installs (a version pin) |
| `python-version` | no | `"3.12"` | Python version for the test |

Output `exit-code`: 0, 1 or 2, as above. The job fails on 1 and 2. On 1 the log has an error annotation, and the
job summary shows the full report: which call ran twice, in how many of the crashes, where the process was killed
first, and the one-line fix.

## What a failing job shows

The report of `call4agents crashtest` on its demo agent without the guard (`AGENT_GUARD=0`, real output):

```
call4agents crashtest (experimental): crashtest_agent.py
Clean run: 1 action(s), 7 crash points. Killed and restarted 7 time(s).

  RAN TWICE       refund(order='A-1042', amount=49)
    in 3 of 7 crashes, first when killed before a saved write

The one-line fix:

import call4agents as c4a
@c4a.effect                     # put it on the action, inside c4a.run(your_run_id)

Then run this test again.
```

## In pytest

Installing call4agents adds a fixture. The test fails on RAN TWICE or NOT APPROVED:

```python
def test_refund_survives_a_crash(c4a_crashtest):
    c4a_crashtest("agents/refund_agent.py", watch=["refund"], test_accounts=True)
```

## How the Action itself is tested

- The `action` job in [tests.yml](../.github/workflows/tests.yml) runs the real Action on Linux, macOS and Windows:
  the guarded demo agent passes; the unguarded one fails with exit-code 1; `test-accounts: "no"` fails before
  crashtest runs; a provider in live mode fails with exit-code 2.
- [tests/test_action.py](../tests/test_action.py) runs the steps of `action.yml` on any machine with bash, the way
  GitHub runs them, and checks the same cases plus the inputs that reach the command, the error annotation and the
  job summary.
- Bug re-insertion: `python -m tests.mutations.run_kit_mutations --suite tests.test_action` puts back nine bugs in
  `action.yml` one at a time (a repeat that does not fail the job, the exit status of `tee` used instead of the
  command's, no `exit-code`, no test-accounts check, `watch` or `max-crashes` dropped, no summary, no annotation, an
  unpinned install) and checks that the test catches each one. A no-op control must be missed.

## What a clean result does not show

- It tests only the path your script takes, at the crash points it reaches. A clean result covers that path and
  those points, nothing more.
- It does not see writes to PostgreSQL, Redis or a remote service as crash points.
- It runs on fakes and test accounts. A real provider's behaviour (for example removing duplicates by an
  idempotency key) is not tested.

To see the problem it finds in published framework releases, see the
[crash test leaderboard](https://salahmakboul.github.io/call4agents/leaderboard/).
