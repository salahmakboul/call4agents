# Call4Agents for JavaScript and TypeScript (0.1.0, not published yet)

One yes, one payment, even after a crash. This is a TypeScript port of the core of the Python kit
`call4agents`: approved tool calls of an AI agent run at most once, also after a crash, a retry or a resume.
It uses the same run record (one SQLite file), so a JavaScript service and a Python service can share it.

No runtime dependencies. It needs Node.js 22.13 or newer (it uses the built-in `node:sqlite`).

## Install (from source for now)

The package is not on npm yet. Build it from this repository:

```sh
git clone https://github.com/salahmakboul/call4agents.git
cd call4agents/js
npm ci
npm run build
npm test
```

Then, in your project: `npm install <path to call4agents/js>`.

## How to use it

```ts
import * as c4a from "call4agents";

c4a.init({ path: ".c4a/ledger.sqlite" });            // the secret for approvals is created next to it

const sendEmail = c4a.effect(async ({ to, body }) => mail.send(to, body), {
  name: "send_email",          // required: part of the key
  approval: true,              // runs only after a "yes" for exactly these arguments
});

await c4a.run("thread-42", async () => {             // the id your app resumes with
  c4a.approve("send_email", { to: "bob", body: "hi" });
  await sendEmail({ to: "bob", body: "hi" });          // runs once
  await sendEmail({ to: "bob", body: "hi" });          // replays the recorded result
});
```

A tool takes ONE object argument, like the tools of the agent frameworks. The key of an action is
(run id, tool name, exact arguments). Other options: `reconcile`, `retryOn`, `idempotentFor`, `key`,
`wait`, `lease`. Errors: `EffectInDoubt`, `ApprovalRequired`, `KeyConflict`.

### OpenAI Agents SDK (`@openai/agents`)

```ts
import { Agent, run, tool } from "@openai/agents";
import { z } from "zod";
import * as c4a from "call4agents";
import * as c4aOa from "call4agents/adapters/openai-agents";

c4a.init();
const sendEmail = tool({
  name: "send_email", description: "Send an email",
  parameters: z.object({ to: z.string(), body: z.string() }),
  needsApproval: true,
  execute: c4aOa.effect(async ({ to, body }) => mail.send(to, body), { name: "send_email", approval: true }),
});
const agent = new Agent({ name: "mailer", instructions: "...", tools: [sendEmail] });
let result = await run(agent, "email bob", { context: { conversationId: "conv-42" } });
for (const item of result.interruptions) c4aOa.approveItem(result.state, item);   // the person said yes
result = await run(agent, result.state);
```

The run id comes from `c4a.run(...)`, or else from the run context: a string, or an object with
`c4aRunId`, `conversationId`, `sessionId` or `threadId`. The SDK's tool call id is not used: a retried model
call makes a new one. `approveItem` / `rejectItem` record the person's answer for exactly the waiting call's
arguments and tell the SDK's run state.

### Vercel AI SDK (`ai`)

```ts
import { generateText, stepCountIs, tool } from "ai";
import { z } from "zod";
import * as c4a from "call4agents";
import * as c4aAi from "call4agents/adapters/ai-sdk";

c4a.init();
const tools = {
  send_email: tool({
    inputSchema: z.object({ to: z.string(), body: z.string() }),
    execute: c4aAi.effect(async ({ to, body }) => mail.send(to, body), { name: "send_email" }),
  }),
};
await c4a.run(chatId, () => generateText({ model, tools, prompt: "email bob", stopWhen: stepCountIs(5) }));
```

The run id comes from `c4a.run(...)` (your chat or job id), or else from the tool `context`
(`c4aRunId`, `chatId`, `conversationId`, `sessionId` or `threadId`). The `toolCallId` is not used: a retried
request gets a new tool call id from the model.

## Crash test

```sh
npx call4agents crashtest my-agent.mjs --test-accounts
npx call4agents crashtest --demo
```

It runs your agent script (an ES module) in an empty folder, kills the process (SIGKILL; on Windows the
process is terminated) just before and after each action and before each saved write, restarts it, and
says what ran twice. Actions are the script's top-level `function` declarations with action-like names
(send, pay, refund, ...); add others with `--watch name`. Arrow functions and methods are not watched yet.
Every action really runs, many times: it needs `--test-accounts`, and it refuses to start when it sees a
live payment key in the environment or in the script (`sk_live_`, `rk_live_`, `rzp_live_`) or a provider in
live mode (`PAYPAL_MODE=live` and others). The message names the variable, never the key.

## What is the same as in the Python kit

- The record: the same table, entry kinds, indexes and payloads; WAL mode; `BEGIN IMMEDIATE` for admission.
- `canonical()` and the effect key: the same bytes and the same sha256 for JSON values (tested, see below).
- Approvals: the same HMAC over (tool, canonical arguments) and the same secret file; one "yes" is spent by
  one action; a "no" cancels earlier "yes" answers, also for an action in doubt.
- Crash between intent and result: `EffectInDoubt`, unless `reconcile` says what happened (`UNKNOWN` keeps it
  in doubt). An error: in doubt at once, unless `retryOn` says nothing happened.
- The owner rules of the Python kit's Fix 17 and Fix 19: each intent names its owner (host, pid, token);
  `reconcile` and same-key resends are used only when that owner is provably gone; the attempt is marked as
  running in this process before its claim; an attempt is given up once; the next attempt is the newest
  closed one + 1. The pid check is `process.kill(pid, 0)`; a test shows that it does not end the process on
  Windows.

## Not ported yet

Approval tickets (`pause` / `decide`), `Limit`, `Rules`, `retries` / `backoff`, `undo`, the audit export,
spans, replay of model calls (`recorded`, `diff`, `prove`), the PostgreSQL store, the `scan`, `doubt` and
`resolve` commands, the pytest fixture and the GitHub Action. A tool's default argument values are not
filled in (JavaScript cannot read them): approve with the same object the tool receives.

## Where JavaScript and Python differ

- A whole number written with a decimal point (`10.0`) is a float in Python and gives the text `10.0`;
  JavaScript reads it as `10`. The same model output can then give two different keys in the two kits.
- JavaScript refuses with a `TypeError`, instead of making a different key: whole numbers above 2^53 - 1 as
  numbers (use a `BigInt`), `-0`, `NaN`, `Infinity`, strings with a lone surrogate, `undefined` in an array,
  and objects that are not plain objects (Date, Map, class instances). A property set to `undefined` is left
  out, like `JSON.stringify` does.
- A tool that returns `undefined` is recorded as `null`; a replay returns `null`.
- Results the Python kit recorded with a type JavaScript does not have (a `Decimal`, a dataclass) are refused
  when replayed; a tuple comes back as an array, bytes as a `Uint8Array`.

## The cross-language test

`test/fixtures/vectors.json` holds 35 argument vectors (unicode, escapes, key order by code point, floats,
big whole numbers). `test/fixtures/python_keys.py` prints the Python kit's canonical text and key for each;
`test/canonical.test.mjs` checks that JavaScript gives the same bytes. `test/crosslang.test.mjs` checks one
record used by both kits: a "yes" signed by Python is spent by JavaScript, a finished call recorded by one
kit is replayed by the other, and a JavaScript "no" cancels a Python "yes". Set `C4A_PYTHON` to a Python
that can import `call4agents`; without one these tests are skipped (`C4A_REQUIRE_PYTHON=1` makes that a failure).

## Tests and mutation check

```sh
npm run build
npm test                    # node:test, about 1 minute
npm run mutations           # puts bugs back in a copy of dist, one at a time; each must be caught
```
