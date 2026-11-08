# A "yes" is not exactly-once: what happens when an AI agent crashes after an approved action

*Author: Salah Eddine Makboul, software engineering student at EST Laayoune and maintainer of Call4Agents.
Disclosure: we build a library for this problem. Everything below can be rerun without our library, and the fixes
in section 9 work without it.*

*Update, 8 November 2026: the same test on Google ADK 2.14.0 and Microsoft Agent Framework 1.25.0 gave 2 payments in
every run too (5 runs each). Scripts and data: [more_frameworks/](more_frameworks/).*

## 1. The short version

A person approves one payment. The tool pays. The process dies before the framework saves its progress. A new
process resumes the way the framework documents. The payment runs a second time.

We measured this in the three Python agent frameworks we tested most closely: LangGraph, Pydantic AI and the OpenAI
Agents SDK. In all three, **one approval produced 2 payments, in every run** (30 runs per framework between 21 October
and 5 November, on the versions listed in section 4). A guard that records the action outside the framework's saved state brought that to
**1 payment, in every run**.

This is not a scandal, and we do not call it a framework bug. It is at-least-once execution, the normal behaviour of
any system that saves progress after doing work. LangGraph's docs already warn about it. OpenAI added a section to its
docs after we asked. Pydantic AI's triage reproduced it and the discussion is open.

The danger is in the gap between what "approved" sounds like and what it guarantees. People building approval flows
read "a human approved this" as "this happens once, exactly as approved". Neither part is promised.

## 2. Why this matters: four real, public cases

These are public reports. We did not write them and we have not verified them beyond what is linked.

**A long call runs two or three times.** In [langgraph#7417](https://github.com/langchain-ai/langgraph/issues/7417) a
team reports tool calls longer than about three minutes being dispatched again from the last checkpoint while the
original is still running, with both finishing, at 2-3x the work and cost. The thread has more than 80 comments.

**Retries with no guard.** [crewAI#5802](https://github.com/crewAIInc/crewAI/issues/5802) describes a tool firing again
when a task retries after the tool already succeeded, and names duplicate payments, emails and trades as the risk.
It has more than 200 comments.

**An approval that approves itself in a loop.** In
[microsoft/agent-framework#7472](https://github.com/microsoft/agent-framework/issues/7472), a sample with automatic
approval could loop on a tool call with no outer limit. The reporter says it used "100+ Millions" of tokens over 3 days.

**A destroyed database.** "An AI agent deleted our production database"
([Hacker News, 860 points, 1,032 comments, 2026-04-26](https://news.ycombinator.com/item?id=47911524)) is a different
failure, a destructive action, not a repeat. We include it for how readers reacted: the top replies did not blame the
model. They asked about permissions, backups and scoped credentials. That is the right instinct, and it is the
point of this paper: **treat agent actions as an engineering problem, with records and limits.**

**The one we caused ourselves.** The best story here is about our own code. On 2026-10-26 a reader,
[@krivonosoff161](https://github.com/salahmakboul/call4agents/issues/1), reported that in our library a "yes" for 10
could let 20 run. We reproduced it, and it was worse than reported: no race was needed, only a crash at the wrong
moment. A library whose whole purpose is approvals bound to exact arguments had an approval bypass in every release
from 0.1.0 to 0.5.0, while all of our own checks passed. It is fixed in 0.5.2
([advisory GHSA-gxg5-95g9-xw4w](https://github.com/salahmakboul/call4agents/security/advisories/GHSA-gxg5-95g9-xw4w)).
We tell this story because it shows how hard this is, and why outside testers matter more than our own tests.

## 3. The experiment

**Question:** when a process dies between "the approved tool ran" and "the framework saved that it ran", what does a
documented resume do?

**Setup:** the framework versions listed with each date in section 4, Python 3.12.10, Windows 10. Fake models, no
network, no API keys. Each step is a separate operating-system process, and each run uses a new temporary folder. The
crash is `os._exit`, so no cleanup code runs. "Payment" is a line appended to a local file.

**Steps, in each framework:**
1. The agent asks to call `pay(amount=10, to="alice")`, a tool that requires approval. The run pauses and its state is stored.
2. A person approves. The tool pays, and the process dies right after, before anything else is stored.
3. A new process resumes in the way the framework documents, from the stored state.
4. Count the payments.

**Second case, "edited after yes":** the stored call is changed to `pay(9999, "mallory")` after the pause and before
it runs. This models anyone with write access to the state store, or a client that sends the state back.

**How to rerun:** `python approve_then_crash.py` (published with this paper). It prints a JSON table.

## 4. Results

Every run gave the same payment counts:

- 3 of 3 on 2026-10-21 and 1 of 1 on 2026-10-30 (LangGraph 1.2.16, Pydantic AI 2.59.0, OpenAI Agents SDK 0.24.0).
- 10 of 10 on 2026-10-31 on the newest versions then available (LangGraph 1.2.19, Pydantic AI 2.63.0, OpenAI Agents
  SDK 0.24.0).
- 1 of 1 on 2026-11-01 (LangGraph 1.2.19, Pydantic AI 2.64.0, OpenAI Agents SDK 0.24.0).
- 5 of 5 on 2026-11-03 on the newest versions that day (LangGraph 1.2.19, Pydantic AI 2.65.0, OpenAI Agents SDK
  0.24.0), with the guard from the published `call4agents==0.6.0`.
- 10 of 10 on 2026-11-05: 5 on LangGraph 1.2.19 in the morning and 5 on LangGraph 1.2.20, released that afternoon,
  both with Pydantic AI 2.66.0, OpenAI Agents SDK 0.24.0 and the guard from the published `call4agents==0.6.0`.

| Framework | Crash after an approved payment | With a guard outside the framework |
|---|---|---|
| LangGraph | **2 payments** | 1 payment |
| Pydantic AI | **2 payments** | 1 payment |
| OpenAI Agents SDK | **2 payments** | 1 payment |

| Framework | Saved call edited to `pay(9999, mallory)` after the pause | With a guard |
|---|---|---|
| LangGraph | **paid 9999 to mallory** | nothing paid (refused) |
| Pydantic AI | **paid 9999 to mallory** | nothing paid (refused) |
| OpenAI Agents SDK | nothing paid: **the SDK itself refuses** (`ModelBehaviorError`) | nothing paid |

Three details matter, and leaving them out would make this look simpler than it is.

**LangGraph sometimes asks again, and we can't predict when.** Our script lets the person approve again if
LangGraph asks. On 1.2.16 it asked in 2 of 3 runs on 2026-10-21 and 1 of 1 on 2026-10-30. On 1.2.19 it asked in 2 of 10 runs on 2026-10-31, 0 of 1 on
2026-11-01, 2 of 5 on 2026-11-03 and 0 of 5 on 2026-11-05. On 1.2.20 it asked in 2 of 5 runs on
2026-11-05. When it asks, the second payment follows a second "yes" from a person who may not
know the first went through. When it doesn't, the resume pays again without asking. Payments were 2 every time
either way. We haven't verified why the runs differ. Our guess is checkpoint timing under the default durability
mode.

**The OpenAI SDK deserves credit.** Its refusal is real protection against changed arguments under the same call id,
and our guard did nothing in that case. It does not stop a call replaced with a new call id; a ticket does (T12 in Call4Agents'
`tests/test_tickets.py`).

**The "edited" case needs write access to stored state.** The OpenAI Agents SDK docs already say to deserialize
snapshots only from trusted storage. Pydantic AI's docs say its UI endpoints must sit behind your own authentication.
If you control the storage completely, this case does not apply to you. The crash case always does.

## 5. Why this happens

Doing the work and saving that you did it are two separate steps, and no process can make them one. If the crash
falls between them, the saved state still says "not done", so a resume does it again.

The framework cannot solve this alone, because the payment happens in someone else's system. So every framework
gives you at-least-once, and it has to. The honest answer is the one the OpenAI docs now give.

An approval adds one more trap. Approval state is usually a flag or a list next to the call: *this call id was
approved*. Nothing ties the "yes" to the exact arguments, to a single use, or to a real pause.

## 6. What a guard can promise, and when

A guard like ours can avoid duplicates. On its own, it can't make sure the action finishes. Database transaction
theory makes the line clear, so here it is in plain words. The test that checks each claim is in brackets, from
the Call4Agents test suite (`tests/test_c4a_kit.py` K, `tests/test_crash_matrix.py` M, `tests/test_idempotency.py`
F, `tests/test_undo.py` U).

**Two records, one transaction.** The guard keeps its own record, a local SQLite file. Each write to it is a
transaction, so it's atomic (all or nothing) and durable: it's flushed to disk before the action runs (WAL with
`synchronous=FULL`), as long as the disk honours the flush. That last part is an assumption we haven't tested.
The payment lives in someone else's system. No transaction covers both, so the ACID properties hold for the
record alone, never for "record plus payment" together. That gap is the whole problem in section 5.

**What it promises,** if (A1) every attempt goes through the guard under the same key, (A2) the record file
survives the crash (the process dies, not the disk), and (A3) the effect happens only inside the guarded call:

1. **No duplicate.** A call with a recorded result gets that result back instead of running again [K1]. Ten
   simultaneous callers produce one effect [K9]. A process killed before or after every write, 412 crashes in
   all, never charged twice [M1, M3, M6].
2. **Only the approved call.** A yes covers one call, with exactly those arguments, once [K4, K17, K17d, K22]. A
   call nobody approved was never charged [M2].
3. **Honest about the unknown.** If the process dies after the guard wrote "started" and before it wrote the
   result, nobody knows whether the payment happened. The guard reports `EffectInDoubt` and doesn't run it again
   [K2, K18d, K24, F2].

**What it doesn't promise:**

- **Completion.** Finishing an in-doubt action needs help from the provider. It needs either an idempotency key
  that the provider enforces, used within the provider's key lifetime [F3; after the lifetime it stays in doubt,
  F4], or a status query you can trust [K3]. A read that finds nothing can lag (section 7), so it's weaker than it
  looks. Without either, a person decides.
- **Anything outside A1 to A3:** a call that bypasses the guard, a record file that's lost or copied back from
  an old backup, a failing disk, or a payment that only partly happened.

**Compensation.** The Saga pattern (Garcia-Molina and Salem, 1987) undoes a failed sequence with compensating
steps. A compensation is one more effect in someone else's system, so it can fail or end in doubt too, and it
doesn't always restore the original state: a refund isn't an un-charge. We treat each compensation as one more
guarded action with its own record. An undo that fails stops the walk and stays in doubt until a person resolves
it [U3]. A crash in the middle of an undo reverses each action exactly once after a restart [U6].

## 7. What the projects said

| Project | Told | Response so far |
|---|---|---|
| Pydantic AI | [#10926](https://github.com/pydantic/pydantic-ai/issues/10926), 2026-10-21 | Triage bot reproduced it on main (`payments: 2`). Triage is marked complete, and the issue is labelled a feature request and a question, not a bug. @sattyamj-attri showed that the tool gets the same `ctx.tool_call_id` on resume. We measured 1 payment with it as an idempotency key, though the tool body still runs twice. @potatohoney-p then showed that a bare id wrongly drops a second operation that reuses the id, so the key must be scoped to your operation. |
| OpenAI Agents SDK | [#5409](https://github.com/openai/openai-agents-python/issues/5409), 2026-10-22 | @darklordVirtual pointed out that a read that finds nothing is not proof, because it can lag. A maintainer merged [PR #5424](https://github.com/openai/openai-agents-python/pull/5424), "Recovering after a crash", and closed the issue on 2026-10-26. |
| LangGraph | [#9240 comment](https://github.com/langchain-ai/langgraph/issues/9240#issuecomment-6347084622), 2026-10-22 | No maintainer reply yet. The thread is an open docs request on the same point. |

Each project had at least 14 days before this paper was published on 2026-11-05: Pydantic AI 15 days, the OpenAI
Agents SDK and LangGraph 14 days. All three threads were re-read that day.

## 8. Why you will need this, even if you have not been hit yet

This section is our argument, not a measurement.

- **Agents are moving from answering to acting.** A wrong answer can be read and ignored. A wrong payment cannot.
- **Runs are getting longer.** A chat reply lasts seconds. An agent with a human pause can wait for days, through
  restarts and deploys. Every restart is a chance to land between "did it" and "saved it".
- **More steps run on more machines.** Workers, retries and sub-agents multiply the chances, as #7417 shows.
- **Tests do not hit it.** Your test suite does not kill the process in that gap, so everything stays green. Ours did
  too, for every release from 0.1.0 to 0.5.0.
- **Auditors will ask.** "Who approved this, for exactly what, and did it run once?" is a fair question about any
  system that spends money. A framework's saved state may not answer it after a crash.

If your agent only reads data, you probably do not need any of this.

## 9. What to do about it, with or without our library

1. **Use an idempotency key the provider enforces**, stable across resumes and scoped to your operation, for example
   `(operation_id, tool_name, tool_call_id)`, with `operation_id` stored before the first attempt.
2. **Treat "maybe happened" as unknown.** Without a provider key, do not replay automatically. A read that finds
   nothing can lag.
3. **Store state before acting where you can**, and put side effects after the pause (LangGraph docs).
4. **Check the approval at the tool, against the arguments about to run**, not only at the resume.
5. **Crash your own agent on purpose.** Kill the process between the action and the save. It takes minutes, and it
   finds this whole class of bug.

Call4Agents packages 1 to 4 for LangGraph, Pydantic AI, the OpenAI Agents SDK, MCP and plain Python. Try it in five
seconds: `pip install call4agents && call4agents incident`.

## 10. Testing the guard itself: crash at every write

One crash point proves little, as our own bug showed. So we also tested the guard with a crash matrix: a real process
is killed just before and just after every write to its record, and inside the action, for four kinds of action. A new
process then returns with the same call or a different call under the same key. Then, for two kinds of action, the
recovery is killed too, at every point, followed by a clean run. That is 412 crashes in total
(`python -m tests.test_crash_matrix`).

Result: no double charge, and no call without an approval was ever paid. When we put the old approval bypass back into
the code, the matrix failed by itself: 20 was paid with no approval. **This test would have found our bug before any
reader did.** It is cheap, and any agent framework or application could run the same idea.

This is a check of our guard at its own write points, on one machine. It says nothing about crashes inside the
operating system or the disk, and it does not cover the tickets or the shared PostgreSQL record yet.

## 11. Limits

- Fake models, one machine, no real payment provider, no production traffic. Numbers are from a small, repeatable
  experiment: 30 runs per case in total, 1 to 10 on any one day. Every run's JSON output is published with the
  script.
- We tested three frameworks at the few versions listed in section 4. Other versions may differ, which is why the
  dates and versions are stated.
- In the frameworks, we tested the crash at one point: right after the tool ran. There are other points.
- The guard's own record is a dependency. Tickets and the shared PostgreSQL record are experimental and beta.
- Our guard had a serious bug until 0.5.2. Assume there are more.

## 12. Why we are asking for testers

Every number here comes from fake models. The bug that mattered most was found by one careful outsider, not by our
checks. So we are asking for three things:

1. **Run the demo and tell us what it gets wrong:** `call4agents incident`.
2. **If you run an approval flow, try to break it.** Crash it between the action and the save, and tell us what happens,
   with or without our library.
3. **Tell us if you have been hit.** A real incident, even anonymised, is worth more than our synthetic runs.

Issues and discussions: https://github.com/salahmakboul/call4agents

## How to cite

Makboul, S. E. (2026). *A "yes" is not exactly-once: what happens when an AI agent crashes after an approved
action.* Call4Agents, 5 November 2026. https://github.com/salahmakboul/call4agents/tree/main/docs/finding

The measurements are also a dataset: ghiuoi/agent-crash-recovery-tests on Hugging Face (CC BY 4.0). Citation
metadata is in `CITATION.cff` next to this file.

## Credits

@sattyamj-attri, @potatohoney-p and the Pydantic AI triage (pydantic-ai#10926); @darklordVirtual and @jbeckwith-oai
(openai-agents-python#5409 and #5424); @krivonosoff161 (call4agents#1). Zheng Chen (Tsinghua University) read a draft
and suggested section 6, a formal account of what the guard promises and what it assumes.

Related work that took up parts of this: the [hitl approvals spec](https://github.com/hitly-net/hitl-spec) by Derek
Colley states in section 3.8 that after a restart a used response must be treated as outcome unknown (PR #10), and
includes our replay-after-restart conformance check (PR #11). Dr Srinivas Padmanabhuni's
[agentic AI test checklist](https://huggingface.co/datasets/srinivaspadmanabhuni/agentic-ai-test-checklist-110)
added three crash-recovery variants from our measurements in v1.3.2 (T1-006-CR1, T1-006-CR2, T3-010-CR1). Our
measurements are also published as a dataset:
[ghiuoi/agent-crash-recovery-tests](https://huggingface.co/datasets/ghiuoi/agent-crash-recovery-tests) (CC BY 4.0).
