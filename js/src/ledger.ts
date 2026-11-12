// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// The run record: one append-only SQLite table, the same file format as the Python kit's ledger.py, so
// one record can be used by both kits. Entries are never updated or deleted.
//   tool_intent    written BEFORE a side effect runs (one per effect and attempt: a UNIQUE index)
//   tool_done      written right after it returns (payload = the recorded result)
//   tool_error / tool_abandoned / tool_retry_same_key / late_result
//   approval       an HMAC over (tool, canonical arguments), signed with a local secret
//   denial         a "no" for exact arguments; cancels earlier approvals for them
//   approval_used  one approval spent by one effect (UNIQUE: a "yes" is used once)
//   effect_approved  which approval an effect spent

import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { closeSync, existsSync, fsyncSync, linkSync, mkdirSync, openSync, readFileSync, unlinkSync, writeSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";

import { canonical } from "./canonical.js";

export const SCHEMA = `CREATE TABLE IF NOT EXISTS entries (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    payload TEXT,
    t REAL NOT NULL
)`;
export const SCHEMA_VERSION = 1;

export class LedgerError extends Error {}
LedgerError.prototype.name = "LedgerError";

export interface Entry {
  seq: number;
  run_id: string;
  kind: string;
  key: string;
  payload: any;
  t: number;
}

const NETWORK_FS = new Set(["nfs", "nfs4", "cifs", "smbfs", "smb3", "9p", "fuse.sshfs", "afpfs"]);

/** SQLite's write-ahead log needs shared memory on ONE machine; on a network drive locking can break
 * silently. Windows: UNC shares. Linux: the mount type in /proc/mounts. Other systems: not detected. */
export function onNetworkDrive(folder: string, mounts?: string): boolean {
  const full = resolve(folder);
  if (mounts === undefined && process.platform === "win32") return full.startsWith("\\\\");
  if (mounts === undefined) {
    if (process.platform !== "linux") return false;
    try {
      mounts = readFileSync("/proc/mounts", "utf8");
    } catch {
      return false;
    }
  }
  const p = full.replace(/\\/g, "/");
  let best = ["", ""];
  for (const line of mounts.split("\n")) {
    const m = line.split(/\s+/);
    if (m.length > 2 && (p === m[1] || p.startsWith(m[1].replace(/\/+$/, "") + "/")) && m[1].length > best[0].length) {
      best = [m[1], m[2]];
    }
  }
  return NETWORK_FS.has(best[1]);
}

function sleepSync(ms: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

/** JSON.parse that keeps whole numbers above 2**53 exact (as BigInt), where Node gives the source text. */
function parseJson(text: string): any {
  return JSON.parse(text, function (this: unknown, _k: string, v: unknown, ctx?: { source?: string }) {
    if (typeof v === "number" && Number.isInteger(v) && !Number.isSafeInteger(v) && ctx?.source && /^-?\d+$/.test(ctx.source)) {
      return BigInt(ctx.source);
    }
    return v;
  } as (this: unknown, key: string, value: unknown) => unknown);
}

export function isDuplicate(err: unknown): boolean {
  const e = err as { errcode?: number; message?: string };
  return e?.errcode === 2067 || e?.errcode === 1555 || /UNIQUE constraint failed/.test(String(e?.message ?? ""));
}

export interface LedgerOptions {
  path?: string;
  secret?: string | Uint8Array;
  oldSecrets?: (string | Uint8Array)[];
  allowNetworkDrive?: boolean;
}

function bytes(s: string | Uint8Array): Buffer {
  return typeof s === "string" ? Buffer.from(s, "utf8") : Buffer.from(s);
}

export class Ledger {
  readonly path: string;
  readonly secret: Buffer;
  readonly oldSecrets: Buffer[];
  private db: DatabaseSync;

  constructor(options: LedgerOptions = {}) {
    this.path = options.path ?? ".c4a/ledger.sqlite";
    if (!options.allowNetworkDrive && onNetworkDrive(dirname(resolve(this.path)))) {
      throw new LedgerError(`${this.path} is on a network drive: SQLite locking is not reliable there, so once-only `
        + "cannot be guaranteed. Put the run record on a local disk (or pass allowNetworkDrive: true if you know the drive is safe).");
    }
    mkdirSync(dirname(resolve(this.path)), { recursive: true });
    try {
      this.db = new DatabaseSync(this.path);
      this.setupWhenFree();
    } catch (exc) {
      if (exc instanceof LedgerError) throw exc;
      throw new LedgerError(`${this.path} is not a usable Call4Agents run record (${(exc as Error).message}). `
        + "Point init() at a new file or at an existing run record.");
    }
    this.secret = options.secret !== undefined ? bytes(options.secret) : this.loadSecret();
    if (this.secret.length === 0) {
      throw new LedgerError("the approval secret is empty; pass at least 16 random bytes (or let the kit create one)");
    }
    if (this.secret.length < 16) {
      process.emitWarning("the approval secret is shorter than 16 bytes: fine for tests, too weak for production");
    }
    const envOld = process.env.C4A_OLD_SECRETS ?? "";
    this.oldSecrets = [...(options.oldSecrets ?? []).map(bytes), ...envOld.split(",").filter(Boolean).map((x) => Buffer.from(x, "utf8"))];
  }

  close(): void {
    try {
      this.db.close();
    } catch {
      /* already closed */
    }
  }

  /** Processes that open a new record together can get "database is locked" at once: a wait, not a broken record. */
  private setupWhenFree(patience = 30_000): void {
    const deadline = Date.now() + patience;
    for (;;) {
      try {
        this.setup();
        return;
      } catch (exc) {
        const text = String((exc as Error).message);
        if (!(text.includes("locked") || text.includes("busy")) || Date.now() > deadline) throw exc;
        sleepSync(50);
      }
    }
  }

  private setup(): void {
    this.db.exec("PRAGMA busy_timeout = 30000");
    this.db.exec("PRAGMA journal_mode=WAL");
    this.db.exec("PRAGMA synchronous=FULL");            // an entry that returned is on disk before the effect runs
    this.db.exec("PRAGMA journal_size_limit=1048576");
    this.db.exec("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)");
    this.db.prepare("INSERT OR IGNORE INTO meta (k, v) VALUES ('schema_version', ?)").run(String(SCHEMA_VERSION));
    const found = Number((this.db.prepare("SELECT v FROM meta WHERE k = 'schema_version'").get() as { v: string }).v);
    if (found > SCHEMA_VERSION) {
      throw new LedgerError(`${this.path} was written by a newer Call4Agents (format ${found}, this kit reads up to `
        + `${SCHEMA_VERSION}). Upgrade call4agents; never downgrade a run record.`);
    }
    this.db.exec(SCHEMA);
    this.db.exec("CREATE INDEX IF NOT EXISTS by_key ON entries(kind, key)");
    // Only ONE intent per (effect, attempt): two callers can never both start the same effect.
    this.db.exec("CREATE UNIQUE INDEX IF NOT EXISTS one_intent ON entries(key) WHERE kind = 'tool_intent'");
    // Each "yes" is spent by ONE action: a deliberate second identical action needs a new "yes".
    this.db.exec("CREATE UNIQUE INDEX IF NOT EXISTS one_use ON entries(key) WHERE kind = 'approval_used'");
    // Each approval ticket is decided once (tickets are not ported yet; the index keeps the format the same).
    this.db.exec("CREATE UNIQUE INDEX IF NOT EXISTS one_decision ON entries(key) WHERE kind = 'ticket_decided'");
  }

  private loadSecret(): Buffer {
    const env = process.env.C4A_SECRET;
    if (env) return Buffer.from(env, "utf8");
    const p = join(dirname(resolve(this.path)), "secret");
    if (!existsSync(p)) {
      // Write the full secret to a private temp file, then hard-link it into place: the link either creates
      // `secret` complete or fails because another process won. A reader never sees a half-written secret.
      const tmp = join(dirname(p), `.secret.${process.pid}.${randomBytes(4).toString("hex")}`);
      const fd = openSync(tmp, "wx", 0o600);
      try {
        writeSync(fd, randomBytes(32));
        fsyncSync(fd);
      } finally {
        closeSync(fd);
      }
      try {
        linkSync(tmp, p);
      } catch (exc) {
        if ((exc as NodeJS.ErrnoException).code !== "EEXIST") throw exc;   // another process created it first
      } finally {
        unlinkSync(tmp);
      }
    }
    const data = readFileSync(p);
    if (data.length < 16) {
      throw new LedgerError(`${p} is shorter than 16 bytes; delete it to create a new secret `
        + "(approvals signed with the old one then stop counting)");
    }
    return data;
  }

  // ------------------------------------------------------------ append / read
  append(runId: string, kind: string, key: string, payload: unknown = null): number {
    const text = payload === null || payload === undefined ? null : canonical(payload);
    const r = this.db.prepare("INSERT INTO entries (run_id, kind, key, payload, t) VALUES (?, ?, ?, ?, ?)")
      .run(runId, kind, key, text, Date.now() / 1000);
    return Number(r.lastInsertRowid);
  }

  /** Atomically record an intent. true = this caller owns the effect; false = someone already does. */
  claim(runId: string, key: string, payload: unknown = null): boolean {
    try {
      this.append(runId, "tool_intent", key, payload);
      return true;
    } catch (exc) {
      if (isDuplicate(exc)) return false;
      throw exc;
    }
  }

  /** Run fn in one write transaction, taken before any read (BEGIN IMMEDIATE). */
  private admission<T>(fn: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    let value: T;
    try {
      value = fn();
    } catch (exc) {
      this.db.exec("ROLLBACK");
      throw exc;
    }
    this.db.exec("COMMIT");
    return value;
  }

  /** One transaction for a live "yes" and its intent: true owns it, false already claimed, null needs approval.
   * No application code runs until the transaction has committed. */
  claimApproved(runId: string, tool: string, args: unknown, effect: string, key: string, payload: unknown = null): boolean | null {
    try {
      return this.admission(() => {
        if (this.latest("tool_done", effect) !== null) return false;
        const spent = this.latest("effect_approved", effect);
        const denied = Math.max(0, ...this.matching(runId, tool, args, "denial"));
        const approvals = this.matching(runId, tool, args, "approval");
        if (spent !== null) {
          if (denied > spent.seq || !approvals.includes(spent.payload.approval)) return null;
        }
        if (this.latest("tool_intent", key) !== null) return false;
        if (spent === null) {
          let found = false;
          for (const seq of approvals) {
            const usedKey = `${runId}\x00${tool}\x00${seq}`;
            if (seq <= denied || this.latest("approval_used", usedKey) !== null) continue;
            this.append(runId, "approval_used", usedKey, { effect });
            this.append(runId, "effect_approved", effect, { approval: seq });
            found = true;
            break;
          }
          if (!found) return null;
        }
        this.append(runId, "tool_intent", key, payload);
        return true;
      });
    } catch (exc) {
      if (isDuplicate(exc)) return false;
      throw exc;
    }
  }

  firstIntentTime(baseKey: string): number | null {
    const row = this.db.prepare("SELECT MIN(t) AS t FROM entries WHERE kind = 'tool_intent' AND key LIKE ?")
      .get(baseKey + "#%") as { t: number | null } | undefined;
    return row && row.t !== null ? row.t : null;
  }

  count(kind: string, key: string): number {
    const row = this.db.prepare("SELECT COUNT(*) AS n FROM entries WHERE kind = ? AND key = ?").get(kind, key) as { n: number };
    return Number(row.n);
  }

  latest(kind: string, key: string): Entry | null {
    const row = this.db.prepare("SELECT seq, run_id, kind, key, payload, t FROM entries WHERE kind = ? AND key = ? "
      + "ORDER BY seq DESC LIMIT 1").get(kind, key) as Record<string, SQLInputValue> | undefined;
    return row ? this.entry(row) : null;
  }

  private entry(row: Record<string, SQLInputValue>): Entry {
    let payload: unknown = null;
    if (row.payload !== null) {
      try {
        payload = parseJson(String(row.payload));
      } catch (exc) {
        throw new LedgerError(`row ${row.seq} of ${this.path} is damaged (its payload is not valid JSON: ${(exc as Error).message}). `
          + "The record was edited by hand or corrupted.");
      }
    }
    return { seq: Number(row.seq), run_id: String(row.run_id), kind: String(row.kind), key: String(row.key), payload, t: Number(row.t) };
  }

  entriesForKey(key: string, kind?: string): Entry[] {
    const sql = "SELECT seq, run_id, kind, key, payload, t FROM entries WHERE key = ?" + (kind ? " AND kind = ?" : "") + " ORDER BY seq";
    const rows = (kind ? this.db.prepare(sql).all(key, kind) : this.db.prepare(sql).all(key)) as Record<string, SQLInputValue>[];
    return rows.map((r) => this.entry(r));
  }

  entries(runId?: string): Entry[] {
    const sql = "SELECT seq, run_id, kind, key, payload, t FROM entries" + (runId ? " WHERE run_id = ?" : "") + " ORDER BY seq";
    const rows = (runId ? this.db.prepare(sql).all(runId) : this.db.prepare(sql).all()) as Record<string, SQLInputValue>[];
    return rows.map((r) => this.entry(r));
  }

  // ------------------------------------------------------------ approvals
  sign(tool: string, args: unknown, key?: Buffer): string {
    return createHmac("sha256", key ?? this.secret).update(`${tool}\x00${canonical(args)}`, "utf8").digest("hex");
  }

  recordApproval(runId: string, tool: string, args: unknown): string {
    const mac = this.sign(tool, args);
    this.append(runId, "approval", `${runId}\x00${tool}`, { mac, args });
    return mac;
  }

  /** A "no" for exactly these arguments: it cancels every earlier "yes" for them. */
  recordDenial(runId: string, tool: string, args: unknown): string {
    const mac = this.sign(tool, args);
    this.append(runId, "denial", `${runId}\x00${tool}`, { mac, args });
    return mac;
  }

  /** seq of every entry of this kind whose MAC matches THESE exact arguments (current or old secrets). */
  matching(runId: string, tool: string, args: unknown, kind: string): number[] {
    const wants = [this.secret, ...this.oldSecrets].map((k) => Buffer.from(this.sign(tool, args, k), "utf8"));
    const rows = this.db.prepare("SELECT seq, payload FROM entries WHERE kind = ? AND key = ? ORDER BY seq")
      .all(kind, `${runId}\x00${tool}`) as { seq: number; payload: string | null }[];
    const out: number[] = [];
    for (const r of rows) {
      if (!r.payload) continue;
      const mac = Buffer.from(String(parseJson(r.payload)?.mac ?? ""), "utf8");
      if (wants.some((w) => w.length === mac.length && timingSafeEqual(w, mac))) out.push(Number(r.seq));
    }
    return out;
  }

  /** True only if an approval matches THESE exact arguments and no later "no" cancelled it. */
  approved(runId: string, tool: string, args: unknown): boolean {
    const denied = Math.max(0, ...this.matching(runId, tool, args, "denial"));
    return this.matching(runId, tool, args, "approval").some((seq) => seq > denied);
  }
}
