# Results

Measured on **Windows only** (Windows 10, AMD64), Python 3.12.10, on 2026-11-13. Raw data: [results/2026-11-13-windows.json](results/2026-11-13-windows.json).

Other systems are not measured yet. The CI workflow (.github/workflows/bench.yml) runs the same benchmark on Linux, macOS and Windows and keeps the JSON as an artifact; it does not change this file.

Each cell is `status repeats: count`. The count is what the fake bank recorded: all actions (C1, C2, S1, S2, S3, S5, S6, S7), actions with amount 5000 (S4), actions in run B (S8). Crash cells (S1, S3, S7) ran 5 times each, the others once. The pass rules are in [scenarios.md](scenarios.md).

Framework versions: langgraph 1.2.16, langgraph-checkpoint-sqlite 3.1.2, openai 3.36.0, openai-agents 0.24.0, pydantic 2.14.1, pydantic-ai-slim 2.59.0.

- **call4agents 0.7.1 (release)**: call4agents 0.7.1 from git tag v0.7.1 (00ad418).
- **call4agents 0.7.2 (release)**: call4agents 0.7.2 from git tag v0.7.2 (2da008a).
- **call4agents head (cd75e22)**: call4agents 0.7.2 from this checkout (cd75e22).

| subject | variant | C1 | C2 | S1 | S2 | S3 | S4 | S5 | S6 | S7 | S8 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Plain Python loop | default | pass 1/1: 1 | pass 1/1: 1 | FAIL 5/5: 2 | FAIL 1/1: 2 | FAIL 5/5: 2 | FAIL 1/1: 1 | FAIL 1/1: 2 | N/A | FAIL 5/5: 2 | FAIL 1/1: 1 |
| Plain Python loop | call4agents 0.7.1 (release) | pass 1/1: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 1 | FAIL 5/5: 2 | pass 1/1: 0 | pass 1/1: 1 | pass 1/1: 0 | pass 5/5: 1 | pass 1/1: 0 |
| Plain Python loop | call4agents 0.7.2 (release) | pass 1/1: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 0 | pass 1/1: 1 | pass 1/1: 0 | pass 5/5: 1 | pass 1/1: 0 |
| Plain Python loop | call4agents head (cd75e22) | pass 1/1: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 0 | pass 1/1: 1 | pass 1/1: 0 | pass 5/5: 1 | pass 1/1: 0 |
| LangGraph | default | pass 1/1: 1 | pass 1/1: 1 | FAIL 5/5: 2 | FAIL 1/1: 2 | FAIL 5/5: 2 | FAIL 1/1: 1 | pass 1/1: 1 | N/A | FAIL 5/5: 2 | FAIL 1/1: 1 |
| LangGraph | call4agents 0.7.1 (release) | pass 1/1: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 1 | FAIL 5/5: 2 | pass 1/1: 0 | pass 1/1: 1 | pass 1/1: 0 | pass 5/5: 1 | pass 1/1: 0 |
| LangGraph | call4agents 0.7.2 (release) | pass 1/1: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 0 | pass 1/1: 1 | pass 1/1: 0 | pass 5/5: 1 | pass 1/1: 0 |
| LangGraph | call4agents head (cd75e22) | pass 1/1: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 0 | pass 1/1: 1 | pass 1/1: 0 | pass 5/5: 1 | pass 1/1: 0 |
| OpenAI Agents SDK | default | pass 1/1: 1 | pass 1/1: 1 | FAIL 5/5: 2 | FAIL 1/1: 2 | FAIL 5/5: 2 | pass 1/1: 0 | pass 1/1: 1 | pass 1/1: 0 | FAIL 5/5: 2 | N/A |
| OpenAI Agents SDK | call4agents 0.7.1 (release) | pass 1/1: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 1 | FAIL 5/5: 2 | pass 1/1: 0 | pass 1/1: 1 | pass 1/1: 0 | pass 5/5: 1 | N/A |
| OpenAI Agents SDK | call4agents 0.7.2 (release) | pass 1/1: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 0 | pass 1/1: 1 | pass 1/1: 0 | pass 5/5: 1 | N/A |
| OpenAI Agents SDK | call4agents head (cd75e22) | pass 1/1: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 0 | pass 1/1: 1 | pass 1/1: 0 | pass 5/5: 1 | N/A |
| Pydantic AI | default | pass 1/1: 1 | pass 1/1: 1 | FAIL 5/5: 2 | FAIL 1/1: 2 | FAIL 5/5: 2 | FAIL 1/1: 1 | pass 1/1: 1 | N/A | FAIL 5/5: 2 | pass 1/1: 0 |
| Pydantic AI | call4agents 0.7.1 (release) | pass 1/1: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 1 | FAIL 5/5: 2 | pass 1/1: 0 | pass 1/1: 1 | pass 1/1: 0 | pass 5/5: 1 | pass 1/1: 0 |
| Pydantic AI | call4agents 0.7.2 (release) | pass 1/1: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 0 | pass 1/1: 1 | pass 1/1: 0 | pass 5/5: 1 | pass 1/1: 0 |
| Pydantic AI | call4agents head (cd75e22) | pass 1/1: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 1 | pass 5/5: 1 | pass 1/1: 0 | pass 1/1: 1 | pass 1/1: 0 | pass 5/5: 1 | pass 1/1: 0 |

Scenarios: **C1** control: a run with no fault makes one action; **C2** control: an approved call runs once; **S1** crash after the action, before the save; resume; **S2** timeout after the action; retry; **S3** two workers resume the same run at once; **S4** yes for 50, the call changes to 5000; **S5** one yes, the agent calls the tool twice; **S6** no after yes, before it runs; **S7** crash inside the action, the bank cannot tell yet; **S8** the yes of run A delivered to run B.

## Passed without call4agents

These framework defaults pass a scenario on their own. call4agents is not needed for them:

- **LangGraph**, S5 (one yes, the agent calls the tool twice): pass 1/1: 1.
- **OpenAI Agents SDK**, S4 (yes for 50, the call changes to 5000): pass 1/1: 0.
- **OpenAI Agents SDK**, S5 (one yes, the agent calls the tool twice): pass 1/1: 1.
- **OpenAI Agents SDK**, S6 (no after yes, before it runs): pass 1/1: 0.
- **Pydantic AI**, S5 (one yes, the agent calls the tool twice): pass 1/1: 1.
- **Pydantic AI**, S8 (the yes of run A delivered to run B): pass 1/1: 0.

## Failed with call4agents

- **Plain Python loop**, call4agents 0.7.1 (release), S3 (two workers resume the same run at once): FAIL 5/5: 2.
- **LangGraph**, call4agents 0.7.1 (release), S3 (two workers resume the same run at once): FAIL 5/5: 2.
- **OpenAI Agents SDK**, call4agents 0.7.1 (release), S3 (two workers resume the same run at once): FAIL 5/5: 2.
- **Pydantic AI**, call4agents 0.7.1 (release), S3 (two workers resume the same run at once): FAIL 5/5: 2.

## Different between call4agents 0.7.1 (release) and call4agents 0.7.2 (release)

- **Plain Python loop**, S3: FAIL 5/5: 2 -> pass 5/5: 1.
- **LangGraph**, S3: FAIL 5/5: 2 -> pass 5/5: 1.
- **OpenAI Agents SDK**, S3: FAIL 5/5: 2 -> pass 5/5: 1.
- **Pydantic AI**, S3: FAIL 5/5: 2 -> pass 5/5: 1.

## Different between call4agents 0.7.2 (release) and call4agents head (cd75e22)

No cell changed.

## How each run ended

A pass on the count does not mean the run finished. A run that stops in doubt waits for a person or for the provider to answer.

- Plain Python loop / default / C1: pass 1/1: 1; ended: finished (1/1)
- Plain Python loop / default / C2: pass 1/1: 1; ended: finished (1/1)
- Plain Python loop / default / S1: FAIL 5/5: 2; ended: finished (5/5)
- Plain Python loop / default / S2: FAIL 1/1: 2; ended: finished (1/1)
- Plain Python loop / default / S3: FAIL 5/5: 2; ended: finished (5/5)
- Plain Python loop / default / S4: FAIL 1/1: 1; ended: finished (1/1)
- Plain Python loop / default / S5: FAIL 1/1: 2; ended: finished (1/1)
- Plain Python loop / default / S6: N/A. the yes is only the resume message; the loop keeps no record of decisions, so a later no is never seen by it
- Plain Python loop / default / S7: FAIL 5/5: 2; ended: finished (5/5)
- Plain Python loop / default / S8: FAIL 1/1: 1; ended: finished (1/1)
- Plain Python loop / call4agents 0.7.1 (release) / C1: pass 1/1: 1; ended: finished (1/1)
- Plain Python loop / call4agents 0.7.1 (release) / C2: pass 1/1: 1; ended: finished (1/1)
- Plain Python loop / call4agents 0.7.1 (release) / S1: pass 5/5: 1; ended: finished (5/5)
- Plain Python loop / call4agents 0.7.1 (release) / S2: pass 1/1: 1; ended: finished (1/1)
- Plain Python loop / call4agents 0.7.1 (release) / S3: FAIL 5/5: 2; ended: finished (5/5)
- Plain Python loop / call4agents 0.7.1 (release) / S4: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- Plain Python loop / call4agents 0.7.1 (release) / S5: pass 1/1: 1; ended: finished (1/1)
- Plain Python loop / call4agents 0.7.1 (release) / S6: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- Plain Python loop / call4agents 0.7.1 (release) / S7: pass 5/5: 1; ended: stopped: EffectInDoubt (5/5)
- Plain Python loop / call4agents 0.7.1 (release) / S8: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- Plain Python loop / call4agents 0.7.2 (release) / C1: pass 1/1: 1; ended: finished (1/1)
- Plain Python loop / call4agents 0.7.2 (release) / C2: pass 1/1: 1; ended: finished (1/1)
- Plain Python loop / call4agents 0.7.2 (release) / S1: pass 5/5: 1; ended: finished (5/5)
- Plain Python loop / call4agents 0.7.2 (release) / S2: pass 1/1: 1; ended: finished (1/1)
- Plain Python loop / call4agents 0.7.2 (release) / S3: pass 5/5: 1; ended: finished (5/5)
- Plain Python loop / call4agents 0.7.2 (release) / S4: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- Plain Python loop / call4agents 0.7.2 (release) / S5: pass 1/1: 1; ended: finished (1/1)
- Plain Python loop / call4agents 0.7.2 (release) / S6: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- Plain Python loop / call4agents 0.7.2 (release) / S7: pass 5/5: 1; ended: stopped: EffectInDoubt (5/5)
- Plain Python loop / call4agents 0.7.2 (release) / S8: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- Plain Python loop / call4agents head (cd75e22) / C1: pass 1/1: 1; ended: finished (1/1)
- Plain Python loop / call4agents head (cd75e22) / C2: pass 1/1: 1; ended: finished (1/1)
- Plain Python loop / call4agents head (cd75e22) / S1: pass 5/5: 1; ended: finished (5/5)
- Plain Python loop / call4agents head (cd75e22) / S2: pass 1/1: 1; ended: finished (1/1)
- Plain Python loop / call4agents head (cd75e22) / S3: pass 5/5: 1; ended: finished (5/5)
- Plain Python loop / call4agents head (cd75e22) / S4: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- Plain Python loop / call4agents head (cd75e22) / S5: pass 1/1: 1; ended: finished (1/1)
- Plain Python loop / call4agents head (cd75e22) / S6: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- Plain Python loop / call4agents head (cd75e22) / S7: pass 5/5: 1; ended: stopped: EffectInDoubt (5/5)
- Plain Python loop / call4agents head (cd75e22) / S8: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- LangGraph / default / C1: pass 1/1: 1; ended: finished (1/1)
- LangGraph / default / C2: pass 1/1: 1; ended: finished (1/1)
- LangGraph / default / S1: FAIL 5/5: 2; ended: finished (5/5)
- LangGraph / default / S2: FAIL 1/1: 2; ended: finished (1/1)
- LangGraph / default / S3: FAIL 5/5: 2; ended: finished (5/5)
- LangGraph / default / S4: FAIL 1/1: 1; ended: finished (1/1)
- LangGraph / default / S5: pass 1/1: 1; ended: waiting for a new yes (1/1)
- LangGraph / default / S6: N/A. the yes is the resume value itself (Command(resume=...)); LangGraph keeps no record of a decision, so a later no is never given to it
- LangGraph / default / S7: FAIL 5/5: 2; ended: finished (5/5)
- LangGraph / default / S8: FAIL 1/1: 1; ended: finished (1/1)
- LangGraph / call4agents 0.7.1 (release) / C1: pass 1/1: 1; ended: finished (1/1)
- LangGraph / call4agents 0.7.1 (release) / C2: pass 1/1: 1; ended: finished (1/1)
- LangGraph / call4agents 0.7.1 (release) / S1: pass 5/5: 1; ended: finished (5/5)
- LangGraph / call4agents 0.7.1 (release) / S2: pass 1/1: 1; ended: finished (1/1)
- LangGraph / call4agents 0.7.1 (release) / S3: FAIL 5/5: 2; ended: finished (5/5)
- LangGraph / call4agents 0.7.1 (release) / S4: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- LangGraph / call4agents 0.7.1 (release) / S5: pass 1/1: 1; ended: waiting for a new yes (1/1)
- LangGraph / call4agents 0.7.1 (release) / S6: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- LangGraph / call4agents 0.7.1 (release) / S7: pass 5/5: 1; ended: stopped: EffectInDoubt (5/5)
- LangGraph / call4agents 0.7.1 (release) / S8: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- LangGraph / call4agents 0.7.2 (release) / C1: pass 1/1: 1; ended: finished (1/1)
- LangGraph / call4agents 0.7.2 (release) / C2: pass 1/1: 1; ended: finished (1/1)
- LangGraph / call4agents 0.7.2 (release) / S1: pass 5/5: 1; ended: finished (5/5)
- LangGraph / call4agents 0.7.2 (release) / S2: pass 1/1: 1; ended: finished (1/1)
- LangGraph / call4agents 0.7.2 (release) / S3: pass 5/5: 1; ended: finished (5/5)
- LangGraph / call4agents 0.7.2 (release) / S4: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- LangGraph / call4agents 0.7.2 (release) / S5: pass 1/1: 1; ended: waiting for a new yes (1/1)
- LangGraph / call4agents 0.7.2 (release) / S6: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- LangGraph / call4agents 0.7.2 (release) / S7: pass 5/5: 1; ended: stopped: EffectInDoubt (5/5)
- LangGraph / call4agents 0.7.2 (release) / S8: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- LangGraph / call4agents head (cd75e22) / C1: pass 1/1: 1; ended: finished (1/1)
- LangGraph / call4agents head (cd75e22) / C2: pass 1/1: 1; ended: finished (1/1)
- LangGraph / call4agents head (cd75e22) / S1: pass 5/5: 1; ended: finished (5/5)
- LangGraph / call4agents head (cd75e22) / S2: pass 1/1: 1; ended: finished (1/1)
- LangGraph / call4agents head (cd75e22) / S3: pass 5/5: 1; ended: finished (5/5)
- LangGraph / call4agents head (cd75e22) / S4: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- LangGraph / call4agents head (cd75e22) / S5: pass 1/1: 1; ended: waiting for a new yes (1/1)
- LangGraph / call4agents head (cd75e22) / S6: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- LangGraph / call4agents head (cd75e22) / S7: pass 5/5: 1; ended: stopped: EffectInDoubt (5/5)
- LangGraph / call4agents head (cd75e22) / S8: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- OpenAI Agents SDK / default / C1: pass 1/1: 1; ended: finished (1/1)
- OpenAI Agents SDK / default / C2: pass 1/1: 1; ended: finished (1/1)
- OpenAI Agents SDK / default / S1: FAIL 5/5: 2; ended: finished (5/5)
- OpenAI Agents SDK / default / S2: FAIL 1/1: 2; ended: finished (1/1)
- OpenAI Agents SDK / default / S3: FAIL 5/5: 2; ended: finished (5/5)
- OpenAI Agents SDK / default / S4: pass 1/1: 0; ended: stopped: ModelBehaviorError (1/1)
- OpenAI Agents SDK / default / S5: pass 1/1: 1; ended: waiting for a new yes (1/1)
- OpenAI Agents SDK / default / S6: pass 1/1: 0; ended: finished (1/1)
- OpenAI Agents SDK / default / S7: FAIL 5/5: 2; ended: finished (5/5)
- OpenAI Agents SDK / default / S8: N/A. each run keeps its decisions in its own RunState, matched by call id; the SDK has no decision message that could be handed to another run
- OpenAI Agents SDK / call4agents 0.7.1 (release) / C1: pass 1/1: 1; ended: finished (1/1)
- OpenAI Agents SDK / call4agents 0.7.1 (release) / C2: pass 1/1: 1; ended: finished (1/1)
- OpenAI Agents SDK / call4agents 0.7.1 (release) / S1: pass 5/5: 1; ended: finished (5/5)
- OpenAI Agents SDK / call4agents 0.7.1 (release) / S2: pass 1/1: 1; ended: finished (1/1)
- OpenAI Agents SDK / call4agents 0.7.1 (release) / S3: FAIL 5/5: 2; ended: finished (5/5)
- OpenAI Agents SDK / call4agents 0.7.1 (release) / S4: pass 1/1: 0; ended: stopped: ModelBehaviorError (1/1)
- OpenAI Agents SDK / call4agents 0.7.1 (release) / S5: pass 1/1: 1; ended: waiting for a new yes (1/1)
- OpenAI Agents SDK / call4agents 0.7.1 (release) / S6: pass 1/1: 0; ended: finished (1/1)
- OpenAI Agents SDK / call4agents 0.7.1 (release) / S7: pass 5/5: 1; ended: finished (5/5)
- OpenAI Agents SDK / call4agents 0.7.1 (release) / S8: N/A. each run keeps its decisions in its own RunState, matched by call id; the SDK has no decision message that could be handed to another run
- OpenAI Agents SDK / call4agents 0.7.2 (release) / C1: pass 1/1: 1; ended: finished (1/1)
- OpenAI Agents SDK / call4agents 0.7.2 (release) / C2: pass 1/1: 1; ended: finished (1/1)
- OpenAI Agents SDK / call4agents 0.7.2 (release) / S1: pass 5/5: 1; ended: finished (5/5)
- OpenAI Agents SDK / call4agents 0.7.2 (release) / S2: pass 1/1: 1; ended: finished (1/1)
- OpenAI Agents SDK / call4agents 0.7.2 (release) / S3: pass 5/5: 1; ended: finished (5/5)
- OpenAI Agents SDK / call4agents 0.7.2 (release) / S4: pass 1/1: 0; ended: stopped: ModelBehaviorError (1/1)
- OpenAI Agents SDK / call4agents 0.7.2 (release) / S5: pass 1/1: 1; ended: waiting for a new yes (1/1)
- OpenAI Agents SDK / call4agents 0.7.2 (release) / S6: pass 1/1: 0; ended: finished (1/1)
- OpenAI Agents SDK / call4agents 0.7.2 (release) / S7: pass 5/5: 1; ended: finished (5/5)
- OpenAI Agents SDK / call4agents 0.7.2 (release) / S8: N/A. each run keeps its decisions in its own RunState, matched by call id; the SDK has no decision message that could be handed to another run
- OpenAI Agents SDK / call4agents head (cd75e22) / C1: pass 1/1: 1; ended: finished (1/1)
- OpenAI Agents SDK / call4agents head (cd75e22) / C2: pass 1/1: 1; ended: finished (1/1)
- OpenAI Agents SDK / call4agents head (cd75e22) / S1: pass 5/5: 1; ended: finished (5/5)
- OpenAI Agents SDK / call4agents head (cd75e22) / S2: pass 1/1: 1; ended: finished (1/1)
- OpenAI Agents SDK / call4agents head (cd75e22) / S3: pass 5/5: 1; ended: finished (5/5)
- OpenAI Agents SDK / call4agents head (cd75e22) / S4: pass 1/1: 0; ended: stopped: ModelBehaviorError (1/1)
- OpenAI Agents SDK / call4agents head (cd75e22) / S5: pass 1/1: 1; ended: waiting for a new yes (1/1)
- OpenAI Agents SDK / call4agents head (cd75e22) / S6: pass 1/1: 0; ended: finished (1/1)
- OpenAI Agents SDK / call4agents head (cd75e22) / S7: pass 5/5: 1; ended: finished (5/5)
- OpenAI Agents SDK / call4agents head (cd75e22) / S8: N/A. each run keeps its decisions in its own RunState, matched by call id; the SDK has no decision message that could be handed to another run
- Pydantic AI / default / C1: pass 1/1: 1; ended: finished (1/1)
- Pydantic AI / default / C2: pass 1/1: 1; ended: finished (1/1)
- Pydantic AI / default / S1: FAIL 5/5: 2; ended: finished (5/5)
- Pydantic AI / default / S2: FAIL 1/1: 2; ended: finished (1/1)
- Pydantic AI / default / S3: FAIL 5/5: 2; ended: finished (5/5)
- Pydantic AI / default / S4: FAIL 1/1: 1; ended: finished (1/1)
- Pydantic AI / default / S5: pass 1/1: 1; ended: waiting for a new yes (1/1)
- Pydantic AI / default / S6: N/A. the yes is only in the DeferredToolResults given to the next run; Pydantic AI keeps no record of a decision, so a later no is never given to it
- Pydantic AI / default / S7: FAIL 5/5: 2; ended: finished (5/5)
- Pydantic AI / default / S8: pass 1/1: 0; ended: stopped: UserError (1/1)
- Pydantic AI / call4agents 0.7.1 (release) / C1: pass 1/1: 1; ended: finished (1/1)
- Pydantic AI / call4agents 0.7.1 (release) / C2: pass 1/1: 1; ended: finished (1/1)
- Pydantic AI / call4agents 0.7.1 (release) / S1: pass 5/5: 1; ended: finished (5/5)
- Pydantic AI / call4agents 0.7.1 (release) / S2: pass 1/1: 1; ended: finished (1/1)
- Pydantic AI / call4agents 0.7.1 (release) / S3: FAIL 5/5: 2; ended: finished (5/5)
- Pydantic AI / call4agents 0.7.1 (release) / S4: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- Pydantic AI / call4agents 0.7.1 (release) / S5: pass 1/1: 1; ended: waiting for a new yes (1/1)
- Pydantic AI / call4agents 0.7.1 (release) / S6: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- Pydantic AI / call4agents 0.7.1 (release) / S7: pass 5/5: 1; ended: stopped: EffectInDoubt (5/5)
- Pydantic AI / call4agents 0.7.1 (release) / S8: pass 1/1: 0; ended: stopped: UserError (1/1)
- Pydantic AI / call4agents 0.7.2 (release) / C1: pass 1/1: 1; ended: finished (1/1)
- Pydantic AI / call4agents 0.7.2 (release) / C2: pass 1/1: 1; ended: finished (1/1)
- Pydantic AI / call4agents 0.7.2 (release) / S1: pass 5/5: 1; ended: finished (5/5)
- Pydantic AI / call4agents 0.7.2 (release) / S2: pass 1/1: 1; ended: finished (1/1)
- Pydantic AI / call4agents 0.7.2 (release) / S3: pass 5/5: 1; ended: finished (5/5)
- Pydantic AI / call4agents 0.7.2 (release) / S4: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- Pydantic AI / call4agents 0.7.2 (release) / S5: pass 1/1: 1; ended: waiting for a new yes (1/1)
- Pydantic AI / call4agents 0.7.2 (release) / S6: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- Pydantic AI / call4agents 0.7.2 (release) / S7: pass 5/5: 1; ended: stopped: EffectInDoubt (5/5)
- Pydantic AI / call4agents 0.7.2 (release) / S8: pass 1/1: 0; ended: stopped: UserError (1/1)
- Pydantic AI / call4agents head (cd75e22) / C1: pass 1/1: 1; ended: finished (1/1)
- Pydantic AI / call4agents head (cd75e22) / C2: pass 1/1: 1; ended: finished (1/1)
- Pydantic AI / call4agents head (cd75e22) / S1: pass 5/5: 1; ended: finished (5/5)
- Pydantic AI / call4agents head (cd75e22) / S2: pass 1/1: 1; ended: finished (1/1)
- Pydantic AI / call4agents head (cd75e22) / S3: pass 5/5: 1; ended: finished (5/5)
- Pydantic AI / call4agents head (cd75e22) / S4: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- Pydantic AI / call4agents head (cd75e22) / S5: pass 1/1: 1; ended: waiting for a new yes (1/1)
- Pydantic AI / call4agents head (cd75e22) / S6: pass 1/1: 0; ended: stopped: ApprovalRequired (1/1)
- Pydantic AI / call4agents head (cd75e22) / S7: pass 5/5: 1; ended: stopped: EffectInDoubt (5/5)
- Pydantic AI / call4agents head (cd75e22) / S8: pass 1/1: 0; ended: stopped: UserError (1/1)

## What the subjects do

- **Plain Python loop** (bench/subjects/plain_loop.py). Default: a while loop, state saved to a JSON file after each step; a tool error goes to the caller. With call4agents: the bank call wrapped in @c4a.effect(approval=..., reconcile=bank lookup).
- **LangGraph** (bench/subjects/langgraph_graph.py). Default: StateGraph + SqliteSaver, resume with invoke(None, config); approval with interrupt() in a node before the tool and Command(resume=...); no RetryPolicy. With call4agents: the bank call wrapped in call4agents.adapters.langgraph.effect(approval=..., reconcile=bank lookup); the yes and no also recorded with c4a_lg.approve / c4a_lg.deny.
- **OpenAI Agents SDK** (bench/subjects/openai_agents_sdk.py). Default: Runner.run with an SQLiteSession, a crash resumed by running the session again; tool errors go to the model (default failure_error_function); approval with needs_approval=True and a saved RunState (state.approve / state.reject, then Runner.run(agent, state)). With call4agents: the bank call wrapped in call4agents.adapters.openai_agents.effect(approval=..., reconcile=bank lookup) inside c4a.run(run id); the yes and no also recorded with c4a.approve / c4a.deny.
- **Pydantic AI** (bench/subjects/pydantic_ai_agent.py). Default: agent.iter with the message history saved after each step, continued with message_history=; approval with requires_approval=True, DeferredToolRequests and DeferredToolResults. With call4agents: the bank call wrapped in call4agents.adapters.pydantic_ai.effect(approval=..., reconcile=bank lookup) inside c4a.run(run id); the yes and no also recorded with c4a.approve / c4a.deny.
