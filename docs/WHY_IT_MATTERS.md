# Why this matters

**In short:** AI agents now do real things, like paying, booking, refunding and sending emails. When a program crashes and the agent retries, an approved action can run twice. We measured it. Call4Agents makes it run once, and gives you one command to test your own agent.

## What agents do now

AI agents no longer only write text. They pay an invoice, book a room, refund an order, send an email and change a file. Each of those is an action in the outside world. You cannot take it back by restarting the program.

## What goes wrong

Programs crash, time out and restart, and agents retry. Frameworks save an agent's progress between steps, so after a crash they run again from the last saved step. If the crash comes just after an approved action ran and before the progress was saved, the same action runs again. That is the normal "at least once" behaviour of these systems, and the frameworks' own docs warn about it.

The result for a user: two payments, two emails, two bookings, for one yes.

## What we measured

One approved payment, a crash right after it, and the documented way to resume. Result: **2 payments, in every run**, in LangGraph, Pydantic AI and the OpenAI Agents SDK (30 runs each, 21 October to 5 November 2026), and in Google ADK and Microsoft Agent Framework (5 runs each). With a record of the action kept outside the framework's saved state: 1 payment, in every run. The script and every run's data are in [the write-up](finding/).

Other people have reproduced the same shape in other projects, and some have fixed it. Two examples from November 2026: a fix in a calendar tool for duplicate invites after a timeout (google-calendar-mcp PR 227), and a change to a web client that stopped it from silently re-sending writes (Mastra PR 30434).

## Who pays the price

- **The customer** who is charged twice, or gets two confirmation emails, or finds two bookings.
- **The company** that has to refund, explain and win the customer back. A repeated payment is a support ticket, and a repeated message to the wrong person is a loss of trust.
- **The developer** who has to find out, from a bank statement, that a retry ran an action the person approved once.

We do not know of a public report where an agent paid twice in production. The cases we found are reproductions and bug reports. One user did report 34 invitation emails sent during a single task by a coding agent (openai/codex issue 58151); the cause there is not established. So the honest size of the problem today is: real, easy to reproduce, and not yet a headline.

## What Call4Agents does

- **One yes, one payment, even after a crash.** The library writes down what it is about to do before it does it, and what happened after. After a crash it does not run the action blind: it either returns the recorded result, or stops with "in doubt" so a person or the provider settles it.
- **A yes is bound to the exact arguments.** A yes to `pay(10, alice)` cannot run `pay(9999, mallory)`, and it is spent by one action.
- **One command to test your own agent.** `call4agents crashtest your_agent.py --test-accounts` kills your agent at every step, restarts it, and tells you which action ran twice. Try the demo first: `pip install call4agents && call4agents crashtest --demo`.

It is Python, has no dependencies, and works next to the framework you already use.

## What is proven, and what is not

**Proven (with the data in this repository):**

- The double payment above, in five frameworks, with every run saved.
- The library's checks: unit and crash tests, a TLA+ model checked with TLC (24 configurations; it found a real gap in our own code), property tests with racing callers, and tests that put each bug back to see that a check catches it.
- We found and fixed four bugs in our own library in November 2026. One paid twice when two callers raced (version 0.7.1). It was published as a security advisory and fixed in 0.7.2.

**Not proven:**

- Real payment systems and production traffic. We use fake providers and test accounts.
- That the TLA+ model matches the code line by line. It models the protocol.
- Behaviour on code paths and crash points we did not test. The crash test says so itself.
- An independent review. We have asked for one and have not had it yet.
- Users: no team has told us it runs this in production.

See [GUARANTEES.md](GUARANTEES.md) for the five promises, their conditions, and what each one does not cover.

## If you manage a team that builds agents

Three questions worth asking this week:

1. Which of our agent's actions cannot be undone (money, messages, deletes)?
2. What happens to one of them if the process dies right after it runs? Has anyone tried?
3. Is the "yes" from a person tied to the exact action that finally runs?

If the answers are "we don't know", the crash test above takes about a minute on a demo agent and about an hour on yours.
