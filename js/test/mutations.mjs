// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// Put each bug back, one at a time, in a temporary copy of the BUILT kit (dist), and check that the tests
// catch it (the same idea as tests/mutations/run_kit_mutations.py). Run after `npm run build`:
//   node test/mutations.mjs            (all)        node test/mutations.mjs K30 CT8   (names containing these)
// No repository file is changed. The no-op control runs EVERY test file and must PASS (be missed): a control
// that "catches" means the copy itself is broken. A mutation whose old text is not found exactly once is stale.

import { spawnSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const JS = fileURLToPath(new URL("../", import.meta.url));
const REPO = fileURLToPath(new URL("../../", import.meta.url));
const ALL = ["core", "process", "canonical", "crosslang", "crashtest", "adapters"];

// [name, file in dist, [[old, new], ...], test files]
const MUTATIONS = [
  ["control (no-op)", "index.js", [["// marked running by its claim (Fix 19)", "// marked running by its claim (Fix 19)"]], ALL],
  ["approval not spent", "ledger.js", [['this.append(runId, "approval_used", usedKey, { effect });', 'this.append(runId, "approval_used_x", usedKey, { effect });']], ["core"]],
  ["deny ignored", "ledger.js", [['const denied = Math.max(0, ...this.matching(runId, tool, args, "denial"));\n                const approvals', "const denied = 0;\n                const approvals"]], ["core"]],
  ["a yes covers any arguments", "ledger.js", [["update(`${tool}\\x00${canonical(args)}`, \"utf8\")", "update(`${tool}`, \"utf8\")"]], ["core"]],
  ["admission not atomic (no transaction, no unique use index)", "ledger.js", [
    ['this.db.exec("BEGIN IMMEDIATE");', "void 0;"],
    ['                this.db.exec("COMMIT");', "                void 0;"],
    ["CREATE UNIQUE INDEX IF NOT EXISTS one_use", "CREATE INDEX IF NOT EXISTS one_use"]], ["process"]],
  ["canonical key order (UTF-16, not code points)", "canonical.js", [[".sort(compareCodePoints)", ".sort()"]], ["canonical"]],
  ["reconcile null treated as done", "index.js", [["            if (found !== null) {", "            if (true) {"]], ["core"]],
  ["UNKNOWN treated as null", "index.js", [["const found = await reconcile(callArgs);",
    "const found0 = await reconcile(callArgs); const found = found0 === UNKNOWN ? null : found0;"]], ["core"]],
  ["live key check removed", "crashtest.js", [["    if (live.length) {", "    if (false) {"]], ["crashtest"]],
  ["--test-accounts check removed", "crashtest.js", [["    if (!testAccounts) {", "    if (false) {"]], ["crashtest"]],
  ["K29 no owner check before reconcile", "index.js", [["                if (busy !== null)\n", "                if (false)\n"]], ["core"]],
  ["K30 marked running after the claim, not before (Fix 19)", "index.js", [
    ["            owner.started(running);\n", "\n"],
    ["                if (!won)\n                    owner.finished(running);", "                void 0;"],
    ["// marked running by its claim (Fix 19)", "\n        owner.started(running);"]], ["core"]],
  ["K29 an attempt is given up twice", "ledger.js", [["                    if (e.payload?.attempt === attempt)\n                        return false;",
    "                    if (e.payload?.attempt === attempt)\n                        void 0;"]], ["core"]],
  ["K29 attempts counted, not numbered", "ledger.js", [["                if (Number.isInteger(a) && a > newest)\n                    newest = a;",
    "                newest += 1;"]], ["core"]],
  ["K29 a live pid counts as gone", "owner.js", [["    if (pidAlive(owner.pid)) {", "    if (false) {"]], ["core"]],
  ["K29 the lease is ignored", "owner.js", [["return age >= lease ? null : `it may still be running on", "return true ? null : `it may still be running on"]], ["core"]],
  ["openai-agents run id = tool call id", "adapters/openai-agents.js", [["((_args, runContext) => runIdFromContext(runContext))",
    "((_args, runContext, details) => details?.toolCall?.callId)"]], ["adapters"]],
  ["ai-sdk run id = toolCallId", "adapters/ai-sdk.js", [["((_args, execOptions) => runIdFromOptions(execOptions))",
    "((_args, execOptions) => execOptions?.toolCallId)"]], ["adapters"]],
];

function copy(dst) {
  for (const d of ["dist", "test", "examples"]) cpSync(join(JS, d), join(dst, d), { recursive: true });
  cpSync(join(JS, "package.json"), join(dst, "package.json"));
  symlinkSync(join(JS, "node_modules"), join(dst, "node_modules"), "junction");
}

const only = process.argv.slice(2);
let bad = 0;
for (const [name, file, edits, suites] of MUTATIONS) {
  if (only.length && !only.some((o) => name.includes(o))) continue;
  const dst = mkdtempSync(join(tmpdir(), "c4a-js-mut-"));
  try {
    copy(dst);
    const path = join(dst, "dist", file);
    let text = readFileSync(path, "utf8");
    const stale = edits.find(([old]) => text.split(old).length !== 2);
    if (stale) {
      console.log(`  ??    ${name.padEnd(60)} pattern not found once (the code moved: update this script)`);
      bad += 1;
      continue;
    }
    for (const [old, now] of edits) text = text.replace(old, () => now);
    writeFileSync(path, text, "utf8");
    const files = suites.map((s) => `test/${s}.test.mjs`);
    const r = spawnSync(process.execPath, ["--test", "--test-concurrency=1", ...files], {
      cwd: dst, encoding: "utf8", env: { ...process.env, C4A_REPO: REPO }, timeout: 900_000,
    });
    const failed = [...r.stdout.matchAll(/^✖ (\S+)/gm)].map((m) => m[1]).filter((f) => f !== "failing");
    const caught = r.status !== 0;
    const expected = !name.startsWith("control");
    const shown = caught ? "CATCHES " + [...new Set(failed)].join(",") : "MISSES";
    console.log(`  ${caught === expected ? "ok  " : "BAD "}  ${name.padEnd(60)} ${shown}`);
    if (caught !== expected) bad += 1;
  } finally {
    unlinkSync(join(dst, "node_modules"));           // the link only, never the real node_modules
    rmSync(dst, { recursive: true, force: true, maxRetries: 5 });
  }
}
console.log(bad ? `\n${bad} mutation result(s) not as expected` : "\nall mutations behave as expected");
process.exitCode = bad ? 1 : 0;
