// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// The parts of the crash test that only read text: live-account signs, which functions look like
// actions, and the one line that lets the child process see each call of them.

export const LIVE_PREFIXES = ["sk_live_", "rk_live_", "rzp_live_"];          // Stripe secret and restricted keys, Razorpay
export const LIVE_MODES: Record<string, string[]> = {
  PAYPAL_MODE: ["live"], BRAINTREE_ENVIRONMENT: ["production"], PLAID_ENV: ["production"],
  ADYEN_ENVIRONMENT: ["live"], SQUARE_ENVIRONMENT: ["production"],
};
const LIVE_IN_SOURCE = /\b(?:sk|rk|rzp)_live_[A-Za-z0-9]{6,}/;

/** What looks like a live payment account: names only, never a key's value. Any sign stops the crash test
 * before the script runs, because every action would really run, many times. It knows only these few signs:
 * a clean result says nothing about other providers, so test accounts stay your job. */
export function liveSigns(source: string, env: Record<string, string | undefined>): string[] {
  const found: string[] = [];
  for (const [name, value] of Object.entries(env)) {
    if (typeof value === "string" && LIVE_PREFIXES.some((p) => value.trim().startsWith(p))) found.push(`${name} holds a live key`);
  }
  for (const [name, modes] of Object.entries(LIVE_MODES)) {
    const value = String(env[name] ?? "").trim();
    if (modes.includes(value.toLowerCase())) found.push(`${name}=${value}`);
  }
  if (LIVE_IN_SOURCE.test(source)) found.push("the script contains a live key");
  return found.sort();
}

const RISKY = new Set(["send", "pay", "charge", "refund", "transfer", "delete", "remove", "drop", "purchase", "buy",
  "cancel", "book", "publish", "deploy", "wire", "email", "payment"]);
const READS = new Set(["get", "read", "list", "search", "find", "fetch", "check", "lookup", "show", "preview", "draft",
  "estimate", "calculate", "validate", "grep", "query", "ask"]);
const DECLARATION = /^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(/gm;

function words(name: string): string[] {
  return name.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase().split(/[_$]+/).filter(Boolean);
}

/** Top-level function declarations of the script (written at the start of a line), with their text. */
export function declarations(source: string): Map<string, string> {
  const out = new Map<string, string>();
  const starts: [string, number][] = [];
  for (const m of source.matchAll(DECLARATION)) starts.push([m[1], m.index]);
  starts.forEach(([name, at], i) => {
    const end = i + 1 < starts.length ? starts[i + 1][1] : source.length;
    if (!out.has(name)) out.set(name, source.slice(at, end));
  });
  return out;
}

/** Functions whose names look like actions (send, pay, refund...), not reads. A function that calls
 * another of them is the loop around the action, not the action. Add others with --watch. */
export function actionsIn(source: string): Set<string> {
  const all = declarations(source);
  const found = new Set<string>();
  for (const name of all.keys()) {
    const w = words(name);
    if (w.length && !READS.has(w[0]) && w.some((x) => RISKY.has(x))) found.add(name);
  }
  return new Set([...found].filter((name) => ![...found].some((other) => other !== name
    && new RegExp(`\\b${other.replace(/\$/g, "\\$")}\\s*\\(`).test(all.get(name)!.slice(all.get(name)!.indexOf("(")) ))));
}

/** The script with one statement added on its first line (line numbers stay the same): each watched
 * top-level function is replaced by a copy that reports its calls to the crash test. */
export function instrument(source: string, watched: Iterable<string>): string {
  const want = new Set(watched);
  const names = [...declarations(source).keys()].filter((n) => want.has(n));
  if (!names.length) return source;
  const line = names.map((n) => `${n} = globalThis.__c4aCrashtest.watch(${n}, ${JSON.stringify(n)});`).join(" ");
  if (source.startsWith("#!")) {
    const nl = source.indexOf("\n");
    return nl < 0 ? source : source.slice(0, nl + 1) + line + " " + source.slice(nl + 1);
  }
  return line + " " + source;
}

export function fixHint(source: string): string {
  if (/from\s+["']@openai\/agents["']/.test(source)) {
    return 'import * as c4aOa from "call4agents/adapters/openai-agents";\n'
      + 'execute: c4aOa.effect(yourFunction, { name: "your_tool" }),   // in tool({...}), on the action';
  }
  if (/from\s+["']ai["']/.test(source)) {
    return 'import * as c4aAi from "call4agents/adapters/ai-sdk";\n'
      + 'execute: c4aAi.effect(yourFunction, { name: "your_tool" }),   // in tool({...}), inside c4a.run(yourChatId, ...)';
  }
  return 'import * as c4a from "call4agents";\n'
    + 'const action = c4a.effect(yourFunction, { name: "your_action" });   // call it inside c4a.run(yourRunId, ...)';
}
