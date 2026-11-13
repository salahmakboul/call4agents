// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// Stable text for arguments, byte for byte the same as the Python kit's canonical():
//   json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
// so a key made here is the same key the Python kit makes for the same JSON value.
//
// Where JavaScript cannot know what Python would write, we refuse with a TypeError instead of making
// a different key: whole numbers above 2**53 - 1 (use a BigInt), -0, NaN, Infinity, strings with a lone
// surrogate, undefined inside an array, and objects that are not plain objects or arrays.

import { createHash } from "node:crypto";

export const MAX_DEPTH = 500;

const HINT = "; pass plain JSON values, or name the action with key=";

function refuse(message: string): never {
  throw new TypeError(message + HINT);
}

/** Order of two strings by Unicode code point (Python's str order), not by UTF-16 unit (JavaScript's). */
export function compareCodePoints(a: string, b: string): number {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    let x = a.charCodeAt(i);
    let y = b.charCodeAt(i);
    if (x === y) continue;
    // A surrogate pair stands for a code point above U+FFFF, so it must sort after U+E000..U+FFFF.
    if (x >= 0xd800) x = x < 0xe000 ? x + 0x2000 : x - 0x800;
    if (y >= 0xd800) y = y < 0xe000 ? y + 0x2000 : y - 0x800;
    return x - y;
  }
  return a.length - b.length;
}

/** Python's repr() of a float that is not a whole number (shortest digits that read back the same). */
function pythonFloat(x: number): string {
  const [mantissa, expText] = x.toExponential().split("e");
  const e = Number(expText);
  const negative = mantissa.startsWith("-");
  const digits = mantissa.replace("-", "").replace(".", "");
  let out: string;
  if (e < -4 || e >= 16) {
    const m = digits.length === 1 ? digits : digits[0] + "." + digits.slice(1);
    const a = Math.abs(e);
    out = m + "e" + (e < 0 ? "-" : "+") + (a < 10 ? "0" + a : String(a));
  } else if (e >= 0) {
    const whole = digits.length > e + 1 ? digits.slice(0, e + 1) : digits.padEnd(e + 1, "0");
    const fraction = digits.length > e + 1 ? digits.slice(e + 1) : "";
    out = whole + "." + (fraction || "0");
  } else {
    out = "0." + "0".repeat(-e - 1) + digits;
  }
  return (negative ? "-" : "") + out;
}

function numberText(x: number): string {
  if (Number.isNaN(x) || !Number.isFinite(x)) refuse(`${x} has no JSON form (Python would write ${x > 0 ? "Infinity" : x < 0 ? "-Infinity" : "NaN"}, which is not JSON)`);
  if (Object.is(x, -0)) refuse("-0 cannot be told apart from 0 in JavaScript, but Python writes -0.0 and 0 differently");
  if (Number.isInteger(x)) {
    if (!Number.isSafeInteger(x)) refuse(`the whole number ${x} is above 2**53 - 1: JavaScript cannot know its exact value (use a BigInt)`);
    return String(x);
  }
  return pythonFloat(x);
}

function stringText(s: string): string {
  if (!s.isWellFormed()) refuse("a string holds a lone surrogate (not valid Unicode); Python cannot encode it either");
  return JSON.stringify(s);
}

function isPlainObject(v: object): boolean {
  const proto = Object.getPrototypeOf(v);
  return proto === Object.prototype || proto === null;
}

function write(value: unknown, depth: number, out: string[]): void {
  if (depth > MAX_DEPTH) refuse(`arguments are nested deeper than ${MAX_DEPTH} levels`);
  if (value === null) {
    out.push("null");
    return;
  }
  switch (typeof value) {
    case "boolean":
      out.push(value ? "true" : "false");
      return;
    case "number":
      out.push(numberText(value));
      return;
    case "bigint":
      out.push(value.toString());
      return;
    case "string":
      out.push(stringText(value));
      return;
    case "object":
      break;
    default:
      refuse(`a ${typeof value} has no stable text form, so it cannot be part of an action's key`);
  }
  const v = value as object;
  if (Array.isArray(v)) {
    out.push("[");
    for (let i = 0; i < v.length; i++) {
      if (i) out.push(",");
      if (v[i] === undefined) refuse("an array holds undefined (Python has no such value)");
      write(v[i], depth + 1, out);
    }
    out.push("]");
    return;
  }
  if (v instanceof Uint8Array) {               // like Python bytes: {"__bytes__": hex}
    write({ __bytes__: Buffer.from(v.buffer, v.byteOffset, v.byteLength).toString("hex") }, depth, out);
    return;
  }
  if (!isPlainObject(v)) {
    refuse(`a ${v.constructor?.name ?? "object"} is not a plain object, so it has no text form we know is stable`);
  }
  const keys = Object.keys(v).filter((k) => (v as Record<string, unknown>)[k] !== undefined).sort(compareCodePoints);
  out.push("{");
  keys.forEach((k, i) => {
    if (i) out.push(",");
    out.push(stringText(k), ":");
    write((v as Record<string, unknown>)[k], depth + 1, out);
  });
  out.push("}");
}

/** Stable text for arguments: key order and spacing never change the hash. Same bytes as the Python kit. */
export function canonical(value: unknown): string {
  const out: string[] = [];
  write(value, 1, out);
  return out.join("");
}

function wellFormed(name: string, s: string): string {
  if (typeof s !== "string") throw new TypeError(`${name} must be a string`);
  if (!s.isWellFormed()) throw new TypeError(`${name} holds a lone surrogate (not valid Unicode)`);
  return s;
}

/** sha256 of run id, tool name and canonical arguments: the key of one action (same as Python's effect_key). */
export function effectKey(runId: string, tool: string, args: unknown): string {
  const text = `${wellFormed("the run id", runId)}\x00${wellFormed("the tool name", tool)}\x00${canonical(args)}`;
  return createHash("sha256").update(text, "utf8").digest("hex");
}

export function intentKey(base: string, attempt = 1): string {
  return `${base}#${attempt}`;
}

/** A decimal number's value as text that is equal for equal values ("10", 10.0 and "10.00" give the same). */
function decimalValue(v: unknown): string | null {
  let text: string;
  if (typeof v === "number") {
    if (!Number.isFinite(v)) return null;
    text = String(v);
  } else if (typeof v === "bigint") {
    text = v.toString();
  } else if (typeof v === "string") {
    text = v.trim();
  } else {
    return null;
  }
  const m = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(text);
  if (!m || (m[2] === "" && (m[3] ?? "") === "")) return null;
  const whole = m[2];
  const fraction = m[3] ?? "";
  let exp = Number(m[4] ?? "0") - fraction.length;
  let digits = (whole + fraction).replace(/^0+/, "");
  if (digits === "") return "0";
  const trailing = digits.length - digits.replace(/0+$/, "").length;
  digits = digits.slice(0, digits.length - trailing);
  exp += trailing;
  return `${m[1] === "-" ? "-" : ""}${digits}e${exp}`;
}

/** Same meaning: equal, with numbers compared by value (10, 10.0 and "10.00" are the same amount). */
export function sameArgs(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((x, i) => sameArgs(x, b[i]));
  }
  if (a && b && typeof a === "object" && typeof b === "object" && !Array.isArray(a) && !Array.isArray(b)) {
    const ka = Object.keys(a).sort();
    const kb = Object.keys(b).sort();
    return ka.length === kb.length && ka.every((k, i) => k === kb[i]
      && sameArgs((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]));
  }
  const na = decimalValue(a);
  const nb = decimalValue(b);
  if (na !== null && nb !== null && (typeof a !== "string" || typeof b !== "string")) return na === nb;
  return a === b;
}
