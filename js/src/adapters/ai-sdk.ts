// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// Vercel AI SDK (ai) adapter.
//
//   import { generateText, tool } from "ai";
//   import * as c4a from "call4agents";
//   import * as c4aAi from "call4agents/adapters/ai-sdk";
//
//   const tools = {
//     send_email: tool({
//       inputSchema: z.object({ to: z.string(), body: z.string() }),
//       execute: c4aAi.effect(async ({ to, body }) => mail.send(to, body), { name: "send_email" }),
//     }),
//   };
//   await c4a.run(chatId, () => generateText({ model, tools, prompt }));
//
// The run id comes from an active c4a.run(...) (your chat or job id). Without one, it is read from the
// tool's context option (execute(input, { context })): an object with c4aRunId, chatId, conversationId,
// sessionId or threadId. The toolCallId is NOT used as the run id: when a request is retried, the model
// makes a new tool call with a new id, and the action would run again.

import * as c4a from "../index.js";

const FIELDS = ["c4aRunId", "c4a_run_id", "chatId", "chat_id", "conversationId", "conversation_id", "sessionId", "session_id",
  "threadId", "thread_id"];

/** The run id from the AI SDK tool execution options (their `context`), or undefined. */
export function runIdFromOptions(options: unknown): string | undefined {
  const value = (options as { context?: unknown } | undefined)?.context;
  if (typeof value === "string" && value !== "") return value;
  if (value !== null && typeof value === "object") {
    for (const f of FIELDS) {
      const found = (value as Record<string, unknown>)[f];
      if (found !== undefined && found !== null && found !== "") return String(found);
    }
  }
  return undefined;
}

/** c4a.effect for the execute function of an AI SDK tool: execute(input, { toolCallId, messages, context }). */
export function effect<A extends object | undefined, R>(
  fn: (args: A, ...rest: any[]) => R | Promise<R>,
  options: c4a.EffectOptions<A, R>,
): c4a.Effect<A, Awaited<R>> {
  return c4a.effect(fn, { ...options, runId: options.runId ?? ((_args: A, execOptions?: unknown) => runIdFromOptions(execOptions)) });
}
