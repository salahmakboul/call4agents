# Research

**In short:** one question, how we tested it, what we found and what we have not shown. Each line below links to the data or to the public thread, so you can check it yourself.

## The question

When a person approves an action for an AI agent and the program then crashes, does the action run again after the agent resumes? And can a small library make it run exactly once, without hiding the cases where nobody can tell?

## The method

Approve one payment, let the tool pay, then kill the real process before the framework saves its state. Resume the documented way and count the payments. Repeat in each framework, with controls: the same run without a crash, and a run where the saved history was edited. Every run is saved as a file, and the script that made it is in the repository.

## What we did and found

| Work | What it shows | Where |
|---|---|---|
| **Write-up**, 5 November 2026: "A 'yes' is not exactly-once" | Two payments in every run, in LangGraph, Pydantic AI and the OpenAI Agents SDK (30 runs each), and in Google ADK and Microsoft Agent Framework (5 runs each). One payment when a record is kept outside the framework's saved state. Script and all runs included. | [docs/finding](https://github.com/salahmakboul/call4agents/tree/main/docs/finding) |
| **TLA+ model** | The protocol is modelled and checked with TLC: 24 configurations, 9 pass, 13 seeded bugs are caught, 2 findings. One finding was a real gap in our code and became a fix. | [Formal model](FORMAL_MODEL.md) |
| **Property tests** | Random sequences of calls, approvals, crashes and racing callers, to find cases nobody wrote by hand. | [Property tests](PROPERTIES.md) |
| **Guarantees** | Five promises, each with its conditions, the named checks behind it, and what it does not cover. | [Guarantees](GUARANTEES.md) |
| **Our own bugs, published** | In version 0.7.1 an action could run twice when two callers raced (5 of 5 runs). It was fixed in 0.7.2 and published as a security advisory. | [GHSA-256j-96j9-r2ff](https://github.com/salahmakboul/call4agents/security/advisories/GHSA-256j-96j9-r2ff) |
| **The crash test** | A command that kills your own agent at every step and tells you which action ran twice. | `call4agents crashtest --demo` |

An open benchmark that runs the same crash scenarios against several frameworks is not published yet.

## Taken up by others

- The [hitl-spec](https://github.com/hitly-net/hitl-spec) draft merged our single-use rule ([PR 10](https://github.com/hitly-net/hitl-spec/pull/10)) and our replay-after-restart conformance check ([PR 11](https://github.com/hitly-net/hitl-spec/pull/11)).
- The OpenAI Agents SDK merged a docs change on approved tool recovery after a crash ([PR 5424](https://github.com/openai/openai-agents-python/pull/5424)), after our [issue 5409](https://github.com/openai/openai-agents-python/issues/5409).
- The proposed OWASP MCP Security Testing Guide test for approval replay includes our restart step ([PR 63](https://github.com/OWASP/MCP-Security-Testing-Guide/pull/63), under review).
- The author of a Google Calendar MCP server changed his fix for duplicate invites after our comments ([PR 227](https://github.com/nspady/google-calendar-mcp/pull/227), open).
- The author of an agentic commerce protocol proposal said he will add our crash-after-charge case ([SEP 325](https://github.com/agentic-commerce-protocol/agentic-commerce-protocol/issues/325), not merged).

None of these depends on this library.

## What we have not shown

- Real payment systems and production traffic. We use fake providers and test accounts.
- That the model matches the code line by line. It models the protocol.
- Behaviour on code paths and crash points we did not test.
- An independent review. We have asked for one and have not had it yet.
- Any team running this in production.

## Reproduce it

```
pip install call4agents
call4agents crashtest --demo
```

The write-up's script and every run's data are in [docs/finding](https://github.com/salahmakboul/call4agents/tree/main/docs/finding). To cite it, see `CITATION.cff` in that folder.

Why this matters to a developer or a manager, in plain words: [Why this matters](WHY_IT_MATTERS.md).
