// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// Recorded results, in the same format as the Python kit's codec: JSON values as they are, and a few
// tagged forms ({"__c4a__": "dict" | "tuple" | "bytes", "v": ...}). Anything else is refused when it is
// RECORDED (a TypeError), never changed silently when it is replayed. No code is ever loaded from a record.

export const TAG = "__c4a__";

export class NotRecordable extends TypeError {}
NotRecordable.prototype.name = "NotRecordable";

function isPlainObject(v: object): boolean {
  const proto = Object.getPrototypeOf(v);
  return proto === Object.prototype || proto === null;
}

export function encode(value: unknown): unknown {
  if (value === undefined || value === null) return null;     // a tool that returns nothing is recorded as null
  const t = typeof value;
  if (t === "boolean" || t === "number" || t === "string" || t === "bigint") return value;
  if (Array.isArray(value)) return value.map((v) => encode(v));
  if (value instanceof Uint8Array) {
    return { [TAG]: "bytes", v: Buffer.from(value.buffer, value.byteOffset, value.byteLength).toString("base64") };
  }
  if (t === "object" && isPlainObject(value as object)) {
    const entries = Object.entries(value as Record<string, unknown>).filter(([, v]) => v !== undefined);
    if (entries.some(([k]) => k === TAG)) {
      return { [TAG]: "dict", v: entries.map(([k, v]) => [k, encode(v)]) };
    }
    return Object.fromEntries(entries.map(([k, v]) => [k, encode(v)]));
  }
  const name = t === "object" ? ((value as object).constructor?.name ?? "object") : t;
  throw new NotRecordable(`cannot record a ${name} exactly; return JSON values (or convert it yourself)`);
}

export function decode(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((v) => decode(v));
  if (value === null || typeof value !== "object") return value;
  const obj = value as Record<string, unknown>;
  const tag = obj[TAG];
  if (tag === undefined) {
    return Object.fromEntries(Object.entries(obj).map(([k, v]) => [k, decode(v)]));
  }
  const v = obj.v;
  if (tag === "dict") {
    const out: Record<string, unknown> = {};
    for (const [k, x] of v as [unknown, unknown][]) {
      if (typeof k !== "string") throw new TypeError("a recorded dict has a key that is not a string; JavaScript cannot rebuild it");
      out[k] = decode(x);
    }
    return out;
  }
  if (tag === "tuple") return (v as unknown[]).map((x) => decode(x));
  if (tag === "bytes") return new Uint8Array(Buffer.from(String(v), "base64"));
  throw new TypeError(`this result was recorded by the Python kit as a ${String(tag)}; the JavaScript kit cannot rebuild it`);
}
