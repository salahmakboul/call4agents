// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// Shared test helpers. Tests import the BUILT kit from ../dist, so the mutation runner can test a changed copy.

import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const DIST = new URL("../dist/", import.meta.url);
export const KIT = new URL("index.js", DIST).href;
export const REPO = process.env.C4A_REPO ?? fileURLToPath(new URL("../../", import.meta.url));

export const c4a = await import(KIT);

export function tempDir(prefix = "c4a-js-") {
  return mkdtempSync(join(tmpdir(), prefix));
}

export function fresh(secret = "test-secret-0000") {
  const dir = tempDir();
  const lg = c4a.init({ path: join(dir, "ledger.sqlite"), secret });
  return { dir, lg, path: join(dir, "ledger.sqlite") };
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Write an ES module script into dir; `KIT` in the text is replaced by the kit's file URL. */
export function script(dir, name, text) {
  const p = join(dir, name);
  writeFileSync(p, text.replaceAll("KIT_URL", KIT).replaceAll("DIST_URL", DIST.href), "utf8");
  return p;
}

/** Start a node child process; resolves {code, signal, stdout, stderr}. */
export function nodeChild(file, args = [], options = {}) {
  const child = spawn(process.execPath, [file, ...args], { stdio: ["ignore", "pipe", "pipe"], ...options });
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (b) => (stdout += b));
  child.stderr.on("data", (b) => (stderr += b));
  const done = new Promise((resolve) => child.on("close", (code, signal) => resolve({ code, signal, stdout, stderr })));
  return { child, done, out: () => stdout };
}

export async function waitFor(test, ms = 15000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await test()) return true;
    await sleep(20);
  }
  return false;
}

/** A Python that can import the repository's call4agents, or null. C4A_REQUIRE_PYTHON=1 makes "none" a failure. */
export function python() {
  const candidates = [process.env.C4A_PYTHON, "python3", "python"].filter(Boolean);
  for (const exe of candidates) {
    const r = spawnSync(exe, ["-c", "import call4agents, sys; print(sys.version_info >= (3, 9))"], {
      env: { ...process.env, PYTHONPATH: REPO }, encoding: "utf8",
    });
    if (r.status === 0 && r.stdout.trim() === "True") return exe;
  }
  return null;
}

export function needPython(t) {
  const exe = python();
  if (exe === null) {
    if (process.env.C4A_REQUIRE_PYTHON === "1") throw new Error("no Python with call4agents found (C4A_PYTHON), and C4A_REQUIRE_PYTHON=1");
    t.skip("no Python with call4agents found: set C4A_PYTHON (skipped)");
  }
  return exe;
}

export function runPython(exe, code, args = [], cwd) {
  const r = spawnSync(exe, ["-c", code, ...args], { env: { ...process.env, PYTHONPATH: REPO, PYTHONIOENCODING: "utf-8" }, encoding: "utf8", cwd });
  if (r.status !== 0) throw new Error(`python failed (${r.status}): ${r.stderr}`);
  return r.stdout;
}

export { pathToFileURL };
