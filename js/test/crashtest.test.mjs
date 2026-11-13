// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// The crash test command: it finds a repeat without the guard and none with it, and it refuses a real account
// (the same cases as CT8 in tests/test_crashtest.py).

import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { DIST, script, tempDir } from "./helpers.mjs";

const ct = await import(new URL("crashtest.js", DIST).href);

const quiet = () => {
  const lines = [];
  return { lines, print: (s) => lines.push(s) };
};

function baseEnv(extra = {}) {
  const env = { ...process.env };
  for (const k of ["STRIPE_SECRET_KEY", "PAYPAL_MODE"]) delete env[k];
  return { ...env, ...extra };
}

test("CT1 without the guard a kill makes the refund run twice (exit 1); with it nothing runs twice (exit 0)", async () => {
  const dir = tempDir();
  const agent = (guard) => script(dir, `agent-${guard}.mjs`, `
import { appendFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import * as c4a from "KIT_URL";
c4a.init({ path: "record.sqlite", secret: "crashtest-test-00" });
async function refund({ order, amount }) {
  appendFileSync("bank.txt", "refund " + amount + " for " + order + "\\n");
  return "refunded";
}
const doRefund = ${guard ? 'c4a.effect(refund, { name: "refund" })' : "refund"};
const done = existsSync("progress.json") ? JSON.parse(readFileSync("progress.json", "utf8")) : [];
await c4a.run("ticket-1", async () => {
  for (const step of ["read", "refund", "reply"]) {
    if (done.includes(step)) continue;
    if (step === "refund") await doRefund({ order: "A-1", amount: 49 });
    done.push(step);
    writeFileSync("progress.json", JSON.stringify(done));
  }
});
`);
  const without = await ct.crashtest(agent(false), { env: baseEnv() });
  const twice = ct.problems(without).twice;
  assert.deepEqual(Object.keys(twice), ['refund({"order":"A-1","amount":49})'], JSON.stringify(without).slice(0, 2000));
  assert.equal(without.reached, without.tested, "every crash point was reached by a real kill");
  const out = quiet();
  assert.equal(await ct.main([agent(false), "--test-accounts"], { print: out.print, env: baseEnv() }), 1);
  assert.match(out.lines.join("\n"), /RAN TWICE {7}refund/);

  const guarded = await ct.crashtest(agent(true), { env: baseEnv() });
  const found = ct.problems(guarded);
  assert.deepEqual([Object.keys(found.twice), Object.keys(found.unapproved)], [[], []], JSON.stringify(guarded).slice(0, 2000));
  assert.ok(guarded.results.some((r) => r.in_doubt), "a kill between the refund and its record stops the restart as in doubt");
});

test("CT6 --demo finds the repeat without the guard and none with it", async () => {
  assert.equal(await ct.main(["--demo"], { print: quiet().print }), 0);
});

test("CT8 it refuses a real account: --test-accounts needed; a live key or live mode stops it, naming the variable, never the key", async () => {
  const dir = tempDir();
  const marker = join(dir, "ran.txt");
  const body = `import { writeFileSync } from "node:fs";\nfunction pay(amount) {\n  writeFileSync(${JSON.stringify(marker)}, "ran");\n}\npay(1);\n`;
  const agent = script(dir, "agent_live.mjs", body);
  const liveKey = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc";          // built here so no file holds a key-shaped text

  const out = quiet();
  assert.equal(await ct.main([agent, "--watch", "pay"], { print: out.print, env: baseEnv() }), 2);
  assert.match(out.lines.join("\n"), /crashtest refused/);
  assert.equal(existsSync(marker), false, "without --test-accounts the script never runs");

  const liveEnv = baseEnv({ STRIPE_SECRET_KEY: liveKey });
  const r = await ct.crashtest(agent, { watch: ["pay"], env: liveEnv });
  assert.deepEqual(r.live, ["STRIPE_SECRET_KEY holds a live key"]);
  const said = quiet();
  assert.equal(await ct.main([agent, "--test-accounts", "--watch", "pay"], { print: said.print, env: liveEnv }), 2);
  assert.equal(existsSync(marker), false, "a live key in the environment stops it, even with --test-accounts");
  assert.ok(!JSON.stringify(r).includes(liveKey) && !said.lines.join("\n").includes(liveKey), "the key is never shown");
  assert.match(said.lines.join("\n"), /STRIPE_SECRET_KEY holds a live key/);

  assert.deepEqual((await ct.crashtest(agent, { watch: ["pay"], env: baseEnv({ PAYPAL_MODE: "live" }) })).live, ["PAYPAL_MODE=live"]);
  assert.equal(existsSync(marker), false, "a provider in live mode stops it");

  const inCode = script(dir, "agent_live_code.mjs", body + `const KEY = ${JSON.stringify(liveKey)};\n`);
  assert.deepEqual((await ct.crashtest(inCode, { watch: ["pay"], env: baseEnv() })).live, ["the script contains a live key"]);
  assert.equal(existsSync(marker), false, "a live key written in the script stops it");

  const testEnv = baseEnv({ STRIPE_SECRET_KEY: "sk_" + "test_" + "4eC39HqLyjWDarjtT1zdp7dc", PAYPAL_MODE: "sandbox" });
  const ok = await ct.crashtest(agent, { watch: ["pay"], env: testEnv });
  assert.equal(ok.error, undefined, String(ok.error));
  assert.equal(existsSync(marker), true, "test keys and a sandbox mode still run (the refusal is not a blanket no)");
});

test("CT9 the scan: risky names are actions, reads and the loop around an action are not", () => {
  const src = `
async function sendEmail(args) {}
function refund_order(x) {}
function getPayments() {}
async function main() { await sendEmail({}); }
async function runRefund() { refund_order(1); }
  function nested_pay() {}
`;
  assert.deepEqual([...ct.actionsIn(src)].sort(), ["refund_order", "sendEmail"]);
  const one = ct.instrument("#!/usr/bin/env node\nfunction pay() {}\npay();\n", ["pay"]);
  assert.equal(one.split("\n").length, 4, "line numbers stay the same");
  assert.match(one.split("\n")[1], /^pay = globalThis.__c4aCrashtest.watch\(pay, "pay"\);/);
});
