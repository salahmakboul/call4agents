// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// Module hook for the crash test child: adds one statement to the script under test so each call of a
// watched top-level function is reported (see instrument()). No other module is changed.

import { fileURLToPath } from "node:url";

import { instrument } from "./crashtest-scan.js";

let target = "";
let watched: string[] = [];

function same(url: string): boolean {
  if (!url.startsWith("file:")) return false;
  let p: string;
  try {
    p = fileURLToPath(url);
  } catch {
    return false;
  }
  return process.platform === "win32" ? p.toLowerCase() === target.toLowerCase() : p === target;
}

export async function initialize(data: { script: string; watched: string[] }): Promise<void> {
  target = data.script;
  watched = data.watched;
}

export async function load(url: string, context: any, nextLoad: (url: string, context: any) => Promise<any>): Promise<any> {
  const r = await nextLoad(url, context);
  if (!same(url) || r.format !== "module" || r.source == null) return r;
  const text = typeof r.source === "string" ? r.source : Buffer.from(r.source).toString("utf8");
  return { ...r, source: instrument(text, watched) };
}
