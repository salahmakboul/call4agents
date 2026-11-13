// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// OpenAI Agents SDK for JavaScript (@openai/agents) adapter.
//
//   import { tool } from "@openai/agents";
//   import * as c4aOa from "call4agents/adapters/openai-agents";
//
//   const sendEmail = tool({
//     name: "send_email", description: "Send an email", parameters: z.object({ to: z.string(), body: z.string() }),
//     needsApproval: true,                                  // the SDK asks a person first
//     execute: c4aOa.effect(async ({ to, body }) => mail.send(to, body), { name: "send_email", approval: true }),
//   });
//   await run(agent, "...", { context: { conversationId: "conv-42" } });
//
// The run id comes from an active c4a.run(...). Without one, it is read from the run's context (the
// RunContext the SDK passes to execute): a string, or an object with c4aRunId, conversationId, sessionId or
// threadId (snake_case works too). The SDK's tool call id is NOT used: a retried model call makes a new one,
// so the same action would get a new key and run again.
//
// approveItem / rejectItem: when a run stops on an approval (result.interruptions), they record the person's
// "yes" or "no" for exactly the waiting call's arguments in the run record AND tell the SDK's run state.

import * as c4a from "../index.js";

const FIELDS = ["c4aRunId", "c4a_run_id", "conversationId", "conversation_id", "sessionId", "session_id", "threadId", "thread_id"];

/** The run id from the SDK's RunContext (or from the plain context object), or undefined. */
export function runIdFromContext(runContext: unknown): string | undefined {
  const value = (runContext as { context?: unknown } | undefined)?.context ?? undefined;
  if (typeof value === "string" && value !== "") return value;
  if (value !== null && typeof value === "object") {
    for (const f of FIELDS) {
      const found = (value as Record<string, unknown>)[f];
      if (found !== undefined && found !== null && found !== "") return String(found);
    }
  }
  return undefined;
}

/** c4a.effect for the execute function of an @openai/agents tool: execute(input, runContext, details). */
export function effect<A extends object | undefined, R>(
  fn: (args: A, ...rest: any[]) => R | Promise<R>,
  options: c4a.EffectOptions<A, R>,
): c4a.Effect<A, Awaited<R>> {
  return c4a.effect(fn, { ...options, runId: options.runId ?? ((_args: A, runContext?: unknown) => runIdFromContext(runContext)) });
}

interface ApprovalItem {
  name?: string;
  toolName?: string;
  arguments?: string;
  rawItem?: { name?: string; arguments?: string };
}

interface StateLike {
  approve(item: any, options?: any): void;
  reject(item: any, options?: any): void;
  _context?: unknown;
}

function decision(state: StateLike, item: ApprovalItem, options: { name?: string; runId?: string }) {
  const name = options.name ?? item.name ?? item.toolName ?? item.rawItem?.name;
  if (!name) throw new TypeError("the approval item has no tool name; pass {name}");
  const text = item.arguments ?? item.rawItem?.arguments ?? "{}";
  const args = JSON.parse(text === "" ? "{}" : text);
  const runId = options.runId ?? c4a.activeRun() ?? runIdFromContext(state._context);
  if (!runId) throw new Error("no run id: pass {runId}, call this inside c4a.run(...), or put one in the run context");
  return { name, args, runId };
}

/** Record a "yes" for exactly this waiting call (tool name and arguments), then state.approve(item). */
export function approveItem(state: StateLike, item: ApprovalItem, options: { name?: string; runId?: string } = {}): void {
  const d = decision(state, item, options);
  c4a.approve(d.name, d.args, d.runId);
  state.approve(item);
}

/** Record a "no" for exactly this waiting call (it cancels earlier "yes" answers), then state.reject(item). */
export function rejectItem(state: StateLike, item: ApprovalItem, options: { name?: string; runId?: string } = {}): void {
  const d = decision(state, item, options);
  c4a.deny(d.name, d.args, d.runId);
  state.reject(item);
}
