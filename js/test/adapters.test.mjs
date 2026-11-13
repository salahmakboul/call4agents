// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// The real packages, offline: @openai/agents with a scripted Model, and the AI SDK with its MockLanguageModelV4.
// A realistic retry or resume sends twice without the adapter and once with it.

import assert from "node:assert/strict";
import test from "node:test";

import { c4a, DIST, fresh } from "./helpers.mjs";

const c4aOa = await import(new URL("adapters/openai-agents.js", DIST).href);
const c4aAi = await import(new URL("adapters/ai-sdk.js", DIST).href);

let oa = null;
let ai = null;
let z = null;
try {
  z = (await import("zod")).z;
} catch {
  z = null;
}
try {
  oa = await import("@openai/agents");
  oa.setTracingDisabled(true);
} catch {
  oa = null;
}
try {
  ai = { ...(await import("ai")), test: await import("ai/test") };
} catch {
  ai = null;
}

function need(t, pkg, name) {
  if (pkg !== null) return true;
  if (process.env.C4A_REQUIRE_FRAMEWORKS === "1") throw new Error(`${name} is not installed and C4A_REQUIRE_FRAMEWORKS=1`);
  t.skip(`${name} is not installed (skipped)`);
  return false;
}

// ------------------------------------------------------------ @openai/agents
function oaScripted(steps) {
  return {
    async getResponse() {
      const next = steps.shift();
      if (next instanceof Error) throw next;
      return next;
    },
    async *getStreamedResponse() {
      throw new Error("not scripted");
    },
  };
}
const oaCall = (id) => ({
  usage: new oa.Usage(),
  output: [{ type: "function_call", callId: id, name: "send_email", arguments: JSON.stringify({ to: "bob", body: "hi" }), status: "completed" }],
});
const oaFinal = () => ({
  usage: new oa.Usage(),
  output: [{ type: "message", role: "assistant", status: "completed", content: [{ type: "output_text", text: "done" }] }],
});

function oaAgent(guarded, steps, sent, approval = false) {
  const send = async ({ to, body }) => {
    sent.push(`${to}:${body}`);
    return "sent";
  };
  const sendEmail = oa.tool({
    name: "send_email", description: "Send an email", parameters: z.object({ to: z.string(), body: z.string() }),
    needsApproval: approval,
    execute: guarded ? c4aOa.effect(send, { name: "send_email", approval }) : send,
  });
  return new oa.Agent({ name: "mailer", instructions: "send the email", model: oaScripted(steps), tools: [sendEmail] });
}

async function oaRetry(guarded) {
  // The model asks for the tool; the tool sends; the next model call fails; the app runs the turn again.
  const sent = [];
  const agent = oaAgent(guarded, [oaCall("call_1"), new Error("network down"), oaCall("call_2"), oaFinal()], sent);
  const context = { conversationId: "conv-1" };          // no c4a.run: the adapter reads the run id from here
  await assert.rejects(() => oa.run(agent, "email bob", { context }), /network down/);
  const r = await oa.run(agent, "email bob", { context });
  assert.equal(r.finalOutput, "done");
  return sent.length;
}

test("A1 @openai/agents: a turn retried after a model error sends twice without the adapter, once with it", async (t) => {
  if (!need(t, oa, "@openai/agents")) return;
  fresh();
  assert.equal(await oaRetry(false), 2);
  assert.equal(await oaRetry(true), 1);
});

async function oaResume(guarded) {
  // needsApproval: the run stops; the app saves the state; a person approves; the tool sends; the process dies
  // before the app saves anything else; after the restart the app loads the saved state and approves again.
  const sent = [];
  const agent = oaAgent(guarded, [oaCall("call_1"), oaFinal(), oaFinal()], sent, true);
  const first = await oa.run(agent, "email bob", { context: { conversationId: "conv-2" } });
  assert.equal(first.interruptions.length, 1);
  const saved = first.state.toString();
  const decide = (state, item) => (guarded ? c4aOa.approveItem(state, item) : state.approve(item));
  decide(first.state, first.interruptions[0]);
  assert.equal((await oa.run(agent, first.state)).finalOutput, "done");
  const restored = await oa.RunState.fromString(agent, saved);
  decide(restored, restored.getInterruptions()[0]);
  assert.equal((await oa.run(agent, restored)).finalOutput, "done");
  return sent.length;
}

test("A2 @openai/agents: an approved run resumed from its saved state sends twice without the adapter, once with it", async (t) => {
  if (!need(t, oa, "@openai/agents")) return;
  fresh();
  assert.equal(await oaResume(false), 2);
  assert.equal(await oaResume(true), 1);
});

test("A3 @openai/agents: the yes is bound to the waiting call's arguments; rejectItem records a no", async (t) => {
  if (!need(t, oa, "@openai/agents")) return;
  const { lg } = fresh();
  const sent = [];
  const agent = oaAgent(true, [oaCall("call_1"), oaFinal()], sent, true);
  const first = await oa.run(agent, "email bob", { context: { conversationId: "conv-3" } });
  const item = first.interruptions[0];
  c4aOa.approveItem(first.state, item);
  assert.equal(lg.approved("conv-3", "send_email", { to: "bob", body: "hi" }), true);
  assert.equal(lg.approved("conv-3", "send_email", { to: "bob", body: "hi!" }), false);
  c4aOa.rejectItem(first.state, item);
  assert.equal(lg.approved("conv-3", "send_email", { to: "bob", body: "hi" }), false);
  assert.equal(c4aOa.runIdFromContext({ context: "thread-9" }), "thread-9");
  assert.equal(c4aOa.runIdFromContext({ context: { session_id: 7 } }), "7");
  assert.equal(c4aOa.runIdFromContext({ context: {} }), undefined);
});

// ------------------------------------------------------------ Vercel AI SDK
const usage = { inputTokens: { total: 1, noCache: 1, cacheRead: 0, cacheWrite: 0 }, outputTokens: { total: 1, text: 1, reasoning: 0 } };
const aiCall = (id) => ({
  content: [{ type: "tool-call", toolCallId: id, toolName: "send_email", input: JSON.stringify({ to: "bob", body: "hi" }) }],
  finishReason: { unified: "tool-calls", raw: "tool_calls" }, usage, warnings: [],
});
const aiFinal = () => ({ content: [{ type: "text", text: "done" }], finishReason: { unified: "stop", raw: "stop" }, usage, warnings: [] });

async function aiRetry(guarded, viaContext = false) {
  const steps = [aiCall("c1"), new Error("network down"), aiCall("c2"), aiFinal()];
  const model = new ai.test.MockLanguageModelV4({
    doGenerate: async () => {
      const next = steps.shift();
      if (next instanceof Error) throw next;
      return next;
    },
  });
  const sent = [];
  const send = async ({ to, body }) => {
    sent.push(`${to}:${body}`);
    return "sent";
  };
  const tools = {
    send_email: ai.tool({
      inputSchema: z.object({ to: z.string(), body: z.string() }),
      ...(viaContext ? { contextSchema: z.object({ chatId: z.string() }) } : {}),
      execute: guarded ? c4aAi.effect(send, { name: "send_email" }) : send,
    }),
  };
  const extra = viaContext ? { toolsContext: { send_email: { chatId: "chat-ctx" } } } : {};
  const ask = () => ai.generateText({ model, tools, prompt: "email bob", stopWhen: ai.stepCountIs(5), maxRetries: 0, ...extra });
  const both = async () => {
    await assert.rejects(ask, /network down/);
    assert.equal((await ask()).text, "done");
  };
  if (viaContext) await both();                          // no c4a.run: the run id comes from the tool context
  else await c4a.run("chat-1", both);
  return sent.length;
}

test("A4 AI SDK: a request retried after a model error sends twice without the adapter, once with it (new toolCallId each time)", async (t) => {
  if (!need(t, ai, "ai") || !need(t, z, "zod")) return;
  fresh();
  assert.equal(await aiRetry(false), 2);
  assert.equal(await aiRetry(true), 1);
  assert.equal(c4aAi.runIdFromOptions({ toolCallId: "c9", context: { chatId: "chat-7" } }), "chat-7");
  assert.equal(c4aAi.runIdFromOptions({ toolCallId: "c9" }), undefined, "the toolCallId is never the run id");
});

test("A5 AI SDK: without c4a.run the run id comes from the tool context (toolsContext), not from the toolCallId", async (t) => {
  if (!need(t, ai, "ai") || !need(t, z, "zod")) return;
  const { lg } = fresh();
  assert.equal(await aiRetry(true, true), 1);
  assert.deepEqual([...new Set(lg.entries().map((e) => e.run_id))], ["chat-ctx"]);
});
