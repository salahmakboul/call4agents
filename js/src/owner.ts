// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// Who is running an attempt, and can it still be running? (Fix 17 of the Python kit, same rules.)
//
// An intent without a result means "crashed" OR "still running". Asking reconcile while the owner is still
// running gets a truthful "not done yet", and running the action again then pays twice. So before the kit
// asks reconcile (or resends with the same key), it checks the owner the intent names:
// - this very process (same host, pid and process token): running only while its call is in progress here;
// - another process on this host: running while that pid is alive (a reused pid counts as alive: safe side);
// - our pid with another token: the old owner is gone (a restarted container got the same pid);
// - another host: running until `lease` seconds after the intent, since we cannot see that machine;
// - an intent with no owner (written before Python kit 0.8): no owner check, as before.
//
// process.kill(pid, 0) sends no signal: on POSIX it only checks; on Windows Node (libuv) opens the
// process and reads its exit code, it does not end it (checked by a test on Windows).

import { randomUUID } from "node:crypto";
import { hostname } from "node:os";

import type { Entry } from "./ledger.js";

export const TOKEN = randomUUID().replace(/-/g, "");     // one per process: tells this process from a later one with the same pid
const ACTIVE = new Map<string, number>();                 // intent keys whose function is running in this process -> callers

export interface Owner {
  host: string;
  pid: number;
  token: string;
}

export function me(): Owner {
  return { host: hostname(), pid: process.pid, token: TOKEN };
}

export function started(intent: string): void {
  ACTIVE.set(intent, (ACTIVE.get(intent) ?? 0) + 1);
}

export function finished(intent: string): void {
  const n = (ACTIVE.get(intent) ?? 0) - 1;
  if (n > 0) ACTIVE.set(intent, n);
  else ACTIVE.delete(intent);
}

export function pidAlive(pid: unknown): boolean {
  if (typeof pid !== "number" || !Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (exc) {
    const code = (exc as NodeJS.ErrnoException).code;
    if (code === "ESRCH") return false;
    return true;                                         // EPERM: it exists; anything else: cannot tell, safe side
  }
}

/** null when the owner of this attempt is provably not running it any more; else why it may be. */
export function mayStillRun(intentKey: string, intent: Pick<Entry, "payload" | "t"> | null, lease: number): string | null {
  if (intent === null) return null;
  const owner = intent.payload?.owner;
  const age = Date.now() / 1000 - Number(intent.t ?? 0);
  const mine = me();
  if (owner === null || typeof owner !== "object" || Array.isArray(owner)) return null;   // written before 0.8
  if (owner.host !== mine.host) {
    return age >= lease ? null : `it may still be running on ${owner.host} (lease ${lease} s)`;
  }
  if (owner.pid === mine.pid) {
    if (owner.token !== TOKEN) return null;             // an earlier process with our pid: gone
    return ACTIVE.has(intentKey) ? "it is still running in this process" : null;
  }
  if (pidAlive(owner.pid)) {
    return age >= lease ? null : `it may still be running in process ${owner.pid} (lease ${lease} s)`;
  }
  return null;
}
