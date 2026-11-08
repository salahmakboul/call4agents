# The same crash test on Google ADK and Microsoft Agent Framework

Follow-up to [the finding](../README.md), run on 7 and 8 November 2026 on the newest releases. Same test: an agent
gets one approval for a payment, the payment happens, the process dies before the framework saves the result, and a
new process resumes the documented way. Each script uses a fake model (no API keys, no network), runs every phase in
its own OS process, and counts lines in a payments file.

## Results

Five runs per row, and every run in a row gave the same count.

**Google ADK 2.14.0** (Python 3.12.10, Windows 10): `FunctionTool(pay, require_confirmation=True)`, a resumable `App`,
`SqliteSessionService`, resume with `runner.run_async(..., invocation_id=...)`.

| Case | Payments | Asked again on resume |
|---|---|---|
| No crash (control) | 1 | - |
| Crash right after the approved payment | **2** | no |
| Same crash, `pay` wrapped with `call4agents` 0.6.0 `c4a.effect` | 1 | no: the resume stops with `EffectInDoubt` |
| Same, with `reconcile=` reading the provider's record | 1 | no: the run finishes |

ADK's [Resume page](https://adk.dev/runtime/resume/) already says tools run at least once on resume and may run more
than once, so this matches the documented behaviour.

**Microsoft Agent Framework 1.25.0** (`agent-framework-core`, Python 3.12.10, Windows 10): `@tool(approval_mode=
"always_require")`, the agent as start executor of a workflow with `FileCheckpointStorage`, restore with
`workflow.run(checkpoint_id=...)`.

| Case | Payments | Asked again on restore |
|---|---|---|
| No crash (control) | 1 | - |
| Crash right after the approved payment, restore the newest checkpoint | **2** | no |
| Same crash, restore the checkpoint before the approval | **2** | yes, and the second yes pays again |
| Both crash cases, tool checks for an earlier identical payment first (`GUARD=1`) | 1 | as above |

The newest checkpoint is the entry checkpoint written when the approval response is delivered. `inspect_ckpt.py`
decodes the files: it has no pending request, holds the approval response, and has no tool result, so restoring it
hands the same approval to the same call again. Reported on
[microsoft/agent-framework#3938](https://github.com/microsoft/agent-framework/issues/3938#issuecomment-6724071334).

## Edited stored state

Both scripts also change the stored call to another amount and recipient. When the change happens before the
approval, the person is shown the changed call, so that is not a bypass. In Agent Framework, a checkpoint changed
after the approval and before the restore paid the changed call with no new approval (5 of 5 runs). Both frameworks'
docs say stored state must come from trusted storage; this case needs write access to it. The crash case does not.

## Files and how to rerun

| File | What it is |
|---|---|
| `adk_confirm_crash.py`, `adk_result.json` | ADK alone: control, crash, edited before the approval |
| `adk_c4a_crash.py`, `adk_c4a_result_c4a.json`, `adk_c4a_result_c4a_check.json` | ADK with `c4a.effect` (`MODE=c4a` or `MODE=c4a+check`) |
| `maf_approve_crash.py`, `maf_result.json`, `maf_result_guard.json` | Agent Framework: all cases, without and with `GUARD=1` |
| `inspect_ckpt.py` | Decodes the Agent Framework checkpoints after the crash |

```bash
pip install google-adk==2.14.0            # Python 3.11+
python adk_confirm_crash.py
pip install call4agents==0.6.0
MODE=c4a python adk_c4a_crash.py

pip install agent-framework-core==1.25.0  # Python 3.11+
python maf_approve_crash.py
GUARD=1 python maf_approve_crash.py
```

`N=10` runs ten per case instead of five. The tests and this note were written with AI help (Claude) and checked
against the released packages.
