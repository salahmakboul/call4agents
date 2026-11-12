// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// Call4Agents for JavaScript and TypeScript: once-only side effects and approvals bound to exact
// arguments, on the same run record as the Python kit. No runtime dependencies (node:sqlite).
//
//   import * as c4a from "call4agents";
//   c4a.init({ path: ".c4a/ledger.sqlite" });
//   const sendEmail = c4a.effect(async ({ to, body }) => mail.send(to, body), { name: "send_email", approval: true });
//   await c4a.run("thread-42", async () => {
//     c4a.approve("send_email", { to: "bob", body: "hi" });
//     await sendEmail({ to: "bob", body: "hi" });
//   });

import { AsyncLocalStorage } from "node:async_hooks";

import { canonical, effectKey, intentKey, sameArgs } from "./canonical.js";
import { decode, encode } from "./codec.js";
import { Ledger, type LedgerOptions } from "./ledger.js";

export { canonical, effectKey, intentKey, sameArgs } from "./canonical.js";
export { Ledger, LedgerError, type Entry, type LedgerOptions } from "./ledger.js";
export { NotRecordable } from "./codec.js";

let LEDGER: Ledger | null = null;
const RUN = new AsyncLocalStorage<string>();
const EFFECT_KEY = new AsyncLocalStorage<string>();

/** What a reconcile function returns when the outside world cannot tell yet. Nothing is recorded, nothing
 * runs, and the call stays in doubt: the next try asks reconcile again. Returning null means "it did not
 * happen" and runs the action once more, so null must only be returned when the provider can confirm it. */
export const UNKNOWN: unique symbol = Symbol.for("call4agents.UNKNOWN");
export type Unknown = typeof UNKNOWN;

export class ApprovalRequired extends Error {
  readonly tool: string;
  readonly callArgs: unknown;
  constructor(tool: string, args: unknown) {
    super(`${tool} needs approval for exactly ${canonical(args)}`);
    this.tool = tool;
    this.callArgs = args;
  }
}
ApprovalRequired.prototype.name = "ApprovalRequired";

/** The intent was recorded but no result: the process died DURING the effect (or it raised). We cannot
 * know if it happened, so we refuse to repeat it (no silent duplicate). */
export class EffectInDoubt extends Error {
  readonly tool: string;
  readonly callArgs: unknown;
  constructor(tool: string, args: unknown, why = "crash during the effect") {
    super(`${tool}${canonical(args)} may already have run (${why}); pass reconcile to check the outside world, `
      + "or resolve it by hand");
    this.tool = tool;
    this.callArgs = args;
  }
}
EffectInDoubt.prototype.name = "EffectInDoubt";

/** A named action (key=) was called again with arguments that mean something else. Nothing ran. */
export class KeyConflict extends Error {
  readonly tool: string;
  readonly key: string;
  readonly recorded: unknown;
  readonly callArgs: unknown;
  constructor(tool: string, name: string, recorded: unknown, args: unknown) {
    super(`${tool}: the key ${JSON.stringify(name)} already names the call ${canonical(recorded)}, not `
      + `${canonical(args)}. Nothing ran. Use a new key for a different action.`);
    this.tool = tool;
    this.key = name;
    this.recorded = recorded;
    this.callArgs = args;
  }
}
KeyConflict.prototype.name = "KeyConflict";

export interface InitOptions extends LedgerOptions {}

/** Open (or create) the run record. Default path: ./.c4a/ledger.sqlite (the Python kit's default too). */
export function init(options: InitOptions = {}): Ledger {
  LEDGER?.close();
  LEDGER = new Ledger(options);
  return LEDGER;
}

export function currentLedger(): Ledger {
  if (LEDGER === null) throw new Error("call c4a.init() first");
  return LEDGER;
}

/** Close the run record (tests, or before deleting its folder). */
export function close(): void {
  LEDGER?.close();
  LEDGER = null;
}

/** Run fn with this run id: the id your framework resumes with (a thread, session or conversation id). */
export function run<T>(runId: string, fn: () => T): T {
  if (typeof runId !== "string" || runId === "") throw new TypeError("the run id must be a non-empty string");
  return RUN.run(runId, fn);
}

/** The run id set by run(...). Outside a run this is an error, not a shared default run. */
export function currentRun(): string {
  const id = RUN.getStore();
  if (id === undefined) {
    throw new Error("no current run: call this inside c4a.run(<your thread/session id>, async () => ...)");
  }
  return id;
}

/** The run id set by run(...), or undefined. */
export function activeRun(): string | undefined {
  return RUN.getStore();
}

type Named = string | { c4aName: string };

function toolName(tool: Named): string {
  if (typeof tool === "string") return tool;
  if (tool && typeof tool.c4aName === "string") return tool.c4aName;
  throw new TypeError("pass the tool's name or a function made by c4a.effect");
}

function plainArgs(args: unknown): Record<string, unknown> {
  if (args === undefined) return {};
  if (args === null || typeof args !== "object" || Array.isArray(args)) {
    throw new TypeError("a tool takes ONE object argument (like {to, body}); got " + (Array.isArray(args) ? "an array" : typeof args));
  }
  canonical(args);                                   // refuses values without a stable key, before anything runs
  return args as Record<string, unknown>;
}

/** Record a "yes" for this tool with exactly these arguments (in the current run). */
export function approve(tool: Named, args: Record<string, unknown> = {}, runId?: string): string {
  return currentLedger().recordApproval(runId ?? currentRun(), toolName(tool), plainArgs(args));
}

/** Record a "no" for this tool with exactly these arguments: earlier "yes" answers no longer count. */
export function deny(tool: Named, args: Record<string, unknown> = {}, runId?: string): string {
  return currentLedger().recordDenial(runId ?? currentRun(), toolName(tool), plainArgs(args));
}

/** Inside an effect: the stable key of this effect, the same on every retry, resume or re-run of the same
 * (run, tool, arguments). Send it to the outside API as its idempotency key. */
export function idempotencyKey(): string {
  const k = EFFECT_KEY.getStore();
  if (k === undefined) throw new Error("idempotencyKey() is only available inside a function made by c4a.effect");
  return k;
}

export function idempotencyHeaders(header = "Idempotency-Key"): Record<string, string> {
  return { [header]: idempotencyKey() };
}

type ErrorClass = abstract new (...args: any[]) => unknown;

export interface EffectOptions<A, R> {
  /** The tool name, part of the key. Required: use the same name as in the Python kit to share a record. */
  name: string;
  /** Runs only if approve(...) recorded a "yes" for these exact arguments. One "yes" covers ONE action. */
  approval?: boolean;
  /** After a crash during the effect: ask the outside world. Return the result (it happened), null (it did
   * not; it then runs once) or UNKNOWN (cannot tell yet; it stays in doubt). */
  reconcile?: (args: A) => R | null | Unknown | Promise<R | null | Unknown>;
  /** An explicit key: a deliberate second identical action uses a new key. Default: run + tool + arguments. */
  key?: (args: A) => string;
  /** Seconds a racing caller waits for the owner's result before EffectInDoubt (default 2). */
  wait?: number;
  /** Seconds the outside API removes duplicate requests with idempotencyKey(): an in-doubt effect is then
   * resent with the SAME key inside that time, instead of raising. */
  idempotentFor?: number;
  /** Errors that prove nothing happened (classes, or a test function): after them the next call runs again. */
  retryOn?: ErrorClass[] | ((error: unknown) => boolean);
  /** Run id when no run(...) is active: (args, ...rest) => id. The framework adapters set this. */
  runId?: (args: A, ...rest: any[]) => string | undefined;
}

export type Effect<A, R> = ((args: A, ...rest: any[]) => Promise<R>) & {
  readonly c4aName: string;
  readonly c4aFn: (args: A, ...rest: any[]) => R | Promise<R>;
};

interface Plan {
  replay?: unknown;
  hasReplay: boolean;
  key?: string;
  runId?: string;
  attempt?: number;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function errorText(exc: unknown): string {
  if (exc instanceof Error) return `${exc.name}(${JSON.stringify(exc.message)})`.slice(0, 200);
  try {
    return String(exc).slice(0, 200);
  } catch {
    return "an error without text";
  }
}

/** Mark a function that changes the outside world. It takes ONE object argument (like a tool's input)
 * and returns an async function with the same argument.
 * - Once-only per (run, tool, exact arguments): a repeat returns the recorded result.
 * - Crash between intent and result: EffectInDoubt, unless reconcile says what happened.
 * - approval: true: needs approve(name, args) for these exact arguments; one "yes" covers ONE action;
 *   deny(...) cancels earlier "yes" answers; racing callers with one "yes": one runs.
 * - The function throws: the action may have happened, so the next call raises EffectInDoubt at once,
 *   naming the error, unless retryOn says the error proves nothing happened.
 * Extra arguments (a framework's context) are passed through and are not part of the key. */
export function effect<A extends object | undefined, R>(
  fn: (args: A, ...rest: any[]) => R | Promise<R>,
  options: EffectOptions<A, R>,
): Effect<A, Awaited<R>> {
  if (typeof fn !== "function") throw new TypeError("c4a.effect needs a function");
  const tool = options?.name;
  if (typeof tool !== "string" || tool === "") {
    throw new TypeError("c4a.effect needs {name: ...}: the tool name is part of the key (two tools must not share one)");
  }
  if (fn.constructor?.name === "AsyncGeneratorFunction" || fn.constructor?.name === "GeneratorFunction") {
    throw new TypeError(`c4a.effect cannot wrap the generator ${tool}: its work would run after the result is recorded`);
  }
  const approval = options.approval === true;
  const { reconcile, key, idempotentFor, retryOn } = options;
  const wait = options.wait ?? 2;

  function baseKey(runId: string, callArgs: Record<string, unknown>): string {
    if (key !== undefined) return effectKey(runId, tool, { __c4a_key__: String(key(callArgs as A)) });
    return effectKey(runId, tool, callArgs);
  }

  function refuseConflict(lg: Ledger, runId: string, callArgs: Record<string, unknown>): void {
    // A named action reused with arguments that mean something else is a conflict, not a cache hit.
    if (key === undefined) return;
    const first = lg.latest("tool_intent", intentKey(baseKey(runId, callArgs), 1));
    if (first !== null && !sameArgs(first.payload.args, JSON.parse(canonical(callArgs)))) {
      throw new KeyConflict(tool, String(key(callArgs as A)), first.payload.args, callArgs);
    }
  }

  async function decideRun(lg: Ledger, runId: string, callArgs: Record<string, unknown>): Promise<Plan> {
    const k = baseKey(runId, callArgs);

    function claim(attempt: number, payload: unknown): boolean {
      if (!approval) return lg.claim(runId, intentKey(k, attempt), payload);
      const owned = lg.claimApproved(runId, tool, callArgs, k, intentKey(k, attempt), payload);
      if (owned === null) {
        refuseConflict(lg, runId, callArgs);
        throw new ApprovalRequired(tool, callArgs);
      }
      return owned;
    }

    for (let round = 0; round < 2; round++) {
      let done = lg.latest("tool_done", k);
      if (done !== null) return { hasReplay: true, replay: decode(done.payload.result) };
      const attempt = lg.count("tool_abandoned", k) + lg.count("tool_retry_same_key", k) + 1;
      if (claim(attempt, { tool, args: callArgs })) return { hasReplay: false, key: k, runId, attempt };
      const err = lg.latest("tool_error", k);
      const failed = err !== null && err.payload?.attempt === attempt;
      const why = failed ? `it raised ${err!.payload.error}` : "crash during the effect";
      const deadline = Date.now() + (failed ? 0 : wait * 1000);   // someone else may hold it: wait
      while (Date.now() < deadline) {
        done = lg.latest("tool_done", k);
        if (done !== null) return { hasReplay: true, replay: decode(done.payload.result) };
        await sleep(20);
      }
      if (idempotentFor !== undefined && reconcile === undefined) {
        const first = lg.firstIntentTime(k);
        if (first !== null && Date.now() / 1000 - first <= idempotentFor) {
          // The outside API still remembers this key: resending with the SAME key cannot create a second effect.
          lg.append(runId, "tool_retry_same_key", k, { attempt });
          if (claim(attempt + 1, { tool, args: callArgs, same_key_retry: true })) {
            return { hasReplay: false, key: k, runId, attempt: attempt + 1 };
          }
        }
        throw new EffectInDoubt(tool, callArgs, why);
      }
      if (reconcile === undefined) throw new EffectInDoubt(tool, callArgs, why);
      const found = await reconcile(callArgs as A);
      if (found === UNKNOWN) throw new EffectInDoubt(tool, callArgs, "reconcile could not tell yet");
      if (found === undefined) {
        throw new TypeError(`${tool}: reconcile returned undefined. Return the result (it happened), null (it did not) `
          + "or c4a.UNKNOWN (cannot tell yet)");
      }
      if (found !== null) {
        lg.append(runId, "tool_done", k, { result: encode(found), reconciled: true });
        return { hasReplay: true, replay: found };
      }
      lg.append(runId, "tool_abandoned", k, { attempt });          // it did not happen: one new try
    }
    throw new EffectInDoubt(tool, callArgs);
  }

  async function check(runId: string, callArgs: Record<string, unknown>): Promise<Plan> {
    const lg = currentLedger();
    refuseConflict(lg, runId, callArgs);
    const plan = await decideRun(lg, runId, callArgs);
    if (plan.hasReplay) refuseConflict(lg, runId, callArgs);       // a caller that lost the race to another call of this key
    return plan;
  }

  function recordResult(plan: Plan, result: unknown): void {
    const lg = currentLedger();
    let payload: unknown;
    try {
      payload = { result: encode(result), attempt: plan.attempt };
      canonical(payload);
    } catch (exc) {
      lg.append(plan.runId!, "tool_error", plan.key!, { attempt: plan.attempt, error: "result could not be recorded" });
      throw exc;
    }
    lg.append(plan.runId!, "tool_done", plan.key!, payload);
    const givenUp = lg.entriesForKey(plan.key!, "tool_abandoned").filter((e) => e.payload?.attempt === plan.attempt);
    if (givenUp.length) {
      lg.append(plan.runId!, "late_result", plan.key!, { attempt: plan.attempt });
      process.emitWarning(`${tool}: attempt ${plan.attempt} returned AFTER it was given up; the action may have happened twice`);
    }
  }

  function provesNothingHappened(exc: unknown): boolean {
    if (retryOn === undefined) return false;
    if (Array.isArray(retryOn)) return retryOn.some((c) => exc instanceof (c as any));
    return retryOn(exc) === true;
  }

  function failed(plan: Plan, exc: unknown): void {
    const lg = currentLedger();
    if (provesNothingHappened(exc)) {      // the caller says: this error means "did not happen"
      lg.append(plan.runId!, "tool_abandoned", plan.key!, { attempt: plan.attempt, error: errorText(exc) });
    } else {
      lg.append(plan.runId!, "tool_error", plan.key!, { attempt: plan.attempt, error: errorText(exc) });
    }
  }

  const wrapper = async function (this: unknown, args: A, ...rest: any[]): Promise<Awaited<R>> {
    const callArgs = plainArgs(args);
    const runId = RUN.getStore() ?? options.runId?.(args, ...rest);
    if (runId === undefined || runId === "") currentRun();          // throws the "no current run" error
    const plan = await check(runId!, callArgs);
    if (plan.hasReplay) return plan.replay as Awaited<R>;
    return EFFECT_KEY.run(plan.key!, async () => {
      let result: Awaited<R>;
      try {
        result = (await fn.call(this, args, ...rest)) as Awaited<R>;
      } catch (exc) {
        failed(plan, exc);
        throw exc;
      }
      recordResult(plan, result);
      return result;
    });
  } as Effect<A, Awaited<R>>;
  Object.defineProperty(wrapper, "c4aName", { value: tool });
  Object.defineProperty(wrapper, "c4aFn", { value: fn });
  Object.defineProperty(wrapper, "name", { value: fn.name || tool });
  return wrapper;
}
