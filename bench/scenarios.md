# Scenarios

Every scenario uses the same small agent: a scripted model asks for one tool call, `pay(amount=50, to="alice")`.
The tool's body calls a fake bank. The bank is an SQLite file in the scenario's own folder, and every process
writes to it. **The benchmark counts rows in the bank.** It never counts what a model or a subject says.

Each step of a scenario (start a run, give a yes, resume, ...) runs in a **new Python process**, so whatever the
next step needs must really be saved, the way the framework documents it. A crash is a real `os._exit` of that
process, at a fixed point. Code: [run.py](run.py) (`play`, `judge`), the faults in [subject.py](subject.py) (`Env`).

A scenario passes or fails only on the bank's count. If its setup did not happen as designed (the kill was not
reached, the run did not stop to wait for a yes, ...), the result is **INVALID**, never a pass. A subject whose
control (C1 or C2) does not pass gets INVALID in the scenarios that control stands for, because a run that
never acts would pass S4, S6 and S8 for nothing.

Crash scenarios (S1, S3, S7) run 5 times each by default (`--repeat`), the others once.

## Controls

**C1. A run with no fault makes one action.** Start a run; nothing goes wrong. Pass: exactly 1 action. It proves
the subject really calls the bank, so a 1 in S1, S2, S3 and S7 means something.

**C2. An approved call runs once.** Start a run that needs a yes; the person says yes to what they saw; resume.
Pass: exactly 1 action. It proves the approval path really runs the call, so a 0 in S4, S6 and S8 means something.

## S1. Crash after the action, before the framework saves

- Story: the bank charged, and the process died before the framework wrote its checkpoint. A new process resumes
  the run the documented way.
- Fault: `os._exit` right after the tool's bank call returned, still inside the tool (`env.after_action()`).
- Setup check: the first process was killed and the bank has 1 row.
- Counted: all actions. Pass: exactly 1.

## S2. Timeout after the action; retry

- Story: the bank charged, but its answer was lost: the call raised `TimeoutError`. The framework or the caller
  retries.
- Fault: the bank writes its row, then raises `TimeoutError` (once per process).
- Who retries: if the framework gives tool errors to the model, the scripted model asks for the same call once
  more (as models do); if the error reaches the caller, the benchmark resumes the run once, the documented way.
- Setup check: the bank has at least 1 row after the first process.
- Counted: all actions. Pass: exactly 1.

## S3. Two workers resume the same run at once

- Story: a run died before its tool call finished; two workers (a queue that delivered twice, a cron and a person)
  resume it at the same moment.
- Fault: `os._exit` inside the bank call, before it writes. Then two processes start, wait at a barrier until both
  are ready (after their imports), and resume together; the bank takes 1 s per call so they overlap.
- Setup check: the first process was killed with 0 rows; both workers reached the barrier.
- Counted: all actions. Pass: exactly 1 (0 means the action was lost; 2 means it ran twice).

## S4. A yes for 50, then the call changes to 5000

- Story: a person approved `pay(50, alice)`. Before the run resumed, the saved call became `pay(5000, alice)`
  (an edit through the state API, a bug, a migration, someone with write access to the state store).
- Fault: the subject's `tamper()` changes the waiting call's arguments in the saved state, with the framework's
  own API where it has one (LangGraph `update_state`, Agent Framework checkpoint storage `load` / `save`), else
  in the saved file (OpenAI Agents SDK RunState JSON, Pydantic AI message history JSON, the Google ADK session
  database's events, the plain loop's state file).
- Counted: actions with amount 5000. Pass: 0.

## S5. One yes, the agent calls the tool twice

- Story: the person said yes once. The model then asks for the same call again in a loop.
- Setup: the scripted model asks for `pay(50, alice)` twice in a row. Nobody gives a second yes; a run that stops
  to ask again is fine.
- Counted: all actions. Pass: exactly 1.

## S6. No after yes, before it runs

- Story: the person clicked yes, the resume was queued, and the person clicked no before the worker ran it.
- Steps: record the yes; record the no the framework's way; then the queued resume runs with the yes message.
- N/A when the framework keeps no record of decisions (the yes is only the resume message), because then the
  framework is never told about the no. With call4agents the no is recorded with `c4a.deny`, so it is measured.
- Counted: all actions. Pass: 0.

## S7. Crash inside the action; the bank cannot tell yet

- Story: the process died inside the bank call. The bank did charge, but its status check says "unknown" for now
  (a pending transaction, a search index that lags).
- Fault: the bank writes its row, then `os._exit` before the call returns. On resume the bank's `lookup()` answers
  `"unknown"`.
- Counted: all actions. Pass: exactly 1 (it did not run again blind). A run that stops "in doubt" passes on the
  count; the table says how it ended.

## S8. The yes of run A delivered to run B

- Story: two runs wait for a yes on the same call. The decision for run A is delivered to run B (a replayed
  webhook, a wrong thread id).
- Steps: start run A and run B; record the yes for A; resume B with A's decision message.
- N/A when the framework has no decision message that could be handed to another run.
- Counted: actions in run B. Pass: 0.

## What is not tested

- Only these faults, at these points, with one tool and a scripted model. A pass says nothing about other points.
- Durable-execution add-ons that need a server (Temporal, DBOS, Prefect, and similar) are not used.
- The fake bank has no idempotency keys. A real provider that removes duplicate requests by key can make S1, S2
  and S3 pass with any framework, if the tool sends a stable key.
- Timing: S3 depends on two processes overlapping; the 1 s bank delay makes that likely, not certain. Results
  can differ between runs and between systems; that is why crash cells are repeated.
