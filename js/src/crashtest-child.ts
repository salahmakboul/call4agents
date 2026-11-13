// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// Loaded with `node --import` into the process under test. It counts crash points, keeps the journal,
// and kills its own process (SIGKILL; on Windows the process is terminated) at the chosen point.
// Crash points: just before and just after each call of a watched function, and just before each write
// the script saves (a file in the working folder, or an SQLite write: a COMMIT, or a write outside a transaction).

import fs from "node:fs";
import { register, syncBuiltinESMExports } from "node:module";
import path from "node:path";
import sqlite from "node:sqlite";
import { fileURLToPath } from "node:url";

const journal = process.env.C4A_CRASHTEST_JOURNAL ?? "";
const crashAt = Number(process.env.C4A_CRASHTEST_AT ?? "0");
const watched = (process.env.C4A_CRASHTEST_WATCH ?? "").split(",").filter(Boolean);
const script = process.env.C4A_CRASHTEST_SCRIPT ?? "";

const realOpenSync = fs.openSync;
const realWriteSync = fs.writeSync;
const fd = realOpenSync(journal, "a");
let count = 0;
let busy = 0;                                   // inside a patched call: the calls it makes itself are not new points

function log(entry: Record<string, unknown>): void {
  realWriteSync(fd, JSON.stringify(entry) + "\n");
}

function die(): never {
  try {
    process.kill(process.pid, "SIGKILL");
  } finally {
    process.exit(137);
  }
}

function point(label: string): void {
  count += 1;
  log({ point: count, at: label });
  if (count === crashAt) die();
}

function isPlain(v: unknown): boolean {
  if (v === null || typeof v !== "object") return typeof v !== "function" && typeof v !== "symbol";
  if (Array.isArray(v)) return true;
  const proto = Object.getPrototypeOf(v);
  return proto === Object.prototype || proto === null;
}

function shown(args: unknown[]): string {
  return args.filter(isPlain).map((a) => {
    let text: string;
    try {
      text = a === undefined ? "undefined" : JSON.stringify(a, (_k, v) => (typeof v === "bigint" ? v.toString() : v));
    } catch {
      text = String(a);
    }
    return text.slice(0, 80);
  }).join(", ");
}

(globalThis as any).__c4aCrashtest = {
  watch(fn: unknown, name: string): unknown {
    if (typeof fn !== "function") return fn;
    const f = fn as (...a: unknown[]) => unknown;
    const wrapped = function (this: unknown, ...args: unknown[]): unknown {
      const call = `${name}(${shown(args)})`;
      point(`before ${call}`);
      log({ start: name, call });
      const finish = (): void => {
        log({ done: name, call });
        point(`after ${call}`);
      };
      const r = f.apply(this, args);
      if (r !== null && typeof r === "object" && typeof (r as Promise<unknown>).then === "function") {
        return (r as Promise<unknown>).then((v) => {
          finish();
          return v;
        });
      }
      finish();
      return r;
    };
    Object.defineProperty(wrapped, "name", { value: f.name });
    return wrapped;
  },
};

// ------------------------------------------------------------ writes the script saves
const cwds = [process.cwd(), fs.realpathSync(process.cwd())];
const RECORD_FILES = [".sqlite", ".sqlite-wal", ".sqlite-shm", ".db", ".journal", "-journal", "-wal", "-shm"];

function norm(p: string): string {
  return process.platform === "win32" ? p.toLowerCase() : p;
}

function savedFile(target: unknown): boolean {
  let p: string;
  if (typeof target === "string") p = target;
  else if (target instanceof URL) p = fileURLToPath(target);
  else if (Buffer.isBuffer(target)) p = target.toString();
  else return false;
  const full = norm(path.resolve(p));
  if (full === norm(path.resolve(journal)) || RECORD_FILES.some((x) => full.endsWith(x))) return false;
  return cwds.some((c) => full.startsWith(norm(c) + path.sep));
}

function writes(flags: unknown): boolean {
  if (typeof flags === "number") return (flags & (fs.constants.O_WRONLY | fs.constants.O_RDWR)) !== 0;
  if (typeof flags === "string") return /[wa+]/.test(flags);
  return false;
}

function guard<T extends (...a: any[]) => any>(owner: any, name: string, isWrite: (args: any[]) => boolean): void {
  const real = owner[name] as T;
  if (typeof real !== "function") return;
  owner[name] = function (this: unknown, ...args: any[]) {
    if (busy === 0 && isWrite(args)) point("before a saved write");
    busy += 1;
    try {
      return real.apply(this, args);
    } finally {
      busy -= 1;
    }
  };
}

const always = (args: any[]) => savedFile(args[0]);
const opened = (args: any[]) => savedFile(args[0]) && writes(args[1]);
for (const name of ["writeFileSync", "appendFileSync", "writeFile", "appendFile", "createWriteStream"]) guard(fs, name, always);
for (const name of ["openSync", "open"]) guard(fs, name, opened);
for (const name of ["writeFile", "appendFile"]) guard(fs.promises, name, always);
guard(fs.promises, "open", opened);
syncBuiltinESMExports();

// ------------------------------------------------------------ SQLite writes
const inTransaction = new WeakMap<object, boolean>();
const statementSql = new WeakMap<object, [object, string]>();

function sqlPoint(db: object, sql: string): void {
  const word = sql.trimStart().slice(0, 8).toUpperCase();
  if (word.startsWith("BEGIN")) {
    inTransaction.set(db, true);
  } else if (word.startsWith("COMMIT") || word.startsWith("END")) {
    point("before a saved write");
    inTransaction.set(db, false);
  } else if (word.startsWith("ROLLBACK")) {
    inTransaction.set(db, false);
  } else if (!inTransaction.get(db) && /^(INSERT|UPDATE|DELETE|REPLACE)/.test(word)) {
    point("before a saved write");
  }
}

function inMemory(db: any): boolean {
  try {
    return typeof db.location === "function" && db.location() === null;
  } catch {
    return false;
  }
}

const D = sqlite.DatabaseSync.prototype as any;
const S = sqlite.StatementSync.prototype as any;
const realExec = D.exec;
const realPrepare = D.prepare;
const realRun = S.run;
D.exec = function (this: object, sql: string) {
  if (!inMemory(this)) sqlPoint(this, String(sql));
  return realExec.call(this, sql);
};
D.prepare = function (this: object, sql: string, ...rest: unknown[]) {
  const st = realPrepare.call(this, sql, ...rest);
  if (!inMemory(this)) statementSql.set(st, [this, String(sql)]);
  return st;
};
S.run = function (this: object, ...args: unknown[]) {
  const known = statementSql.get(this);
  if (known) sqlPoint(known[0], known[1]);
  return realRun.apply(this, args);
};

register(new URL("./crashtest-loader.js", import.meta.url), {
  data: { script: path.resolve(script), watched },
});
