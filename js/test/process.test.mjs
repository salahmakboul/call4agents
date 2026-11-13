// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// Real processes: a child killed (SIGKILL; on Windows the process is terminated) between the outside action
// and the record, and child processes racing on one record.

import assert from "node:assert/strict";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { c4a, nodeChild, script, sleep, tempDir, waitFor } from "./helpers.mjs";

const CHILD = `
import { appendFileSync, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import * as c4a from "KIT_URL";
const [work, mode] = process.argv.slice(2);
c4a.init({ path: join(work, "rec.sqlite"), secret: "process-test-secret" });
const outbox = join(work, "outbox.txt");
const lines = () => (existsSync(outbox) ? readFileSync(outbox, "utf8").trim().split("\\n").filter(Boolean) : []);
const send = c4a.effect(async ({ to, amount }) => {
  if (mode === "die") process.kill(process.pid, "SIGKILL");     // killed inside the call: nothing was sent
  if (mode === "race" || mode === "resume") await new Promise((r) => setTimeout(r, 300));
  appendFileSync(outbox, to + " " + amount + " " + process.pid + "\\n");
  if (mode === "hang") {
    console.log("SENT");
    await new Promise(() => setInterval(() => {}, 1000));     // the parent kills us here: sent, not recorded
  }
  return "sent-" + process.pid;
}, {
  name: "proc.send", approval: process.env.APPROVAL === "1", wait: 2,
  reconcile: process.env.RECONCILE === "1" ? () => (lines().length ? "found-in-outbox" : null) : undefined,
});
if (mode === "race" || mode === "resume") {
  while (!existsSync(join(work, "go"))) await new Promise((r) => setTimeout(r, 5));
}
try {
  const r = await c4a.run("job-1", () => send({ to: "bob", amount: 10 }));
  console.log("RESULT " + r);
} catch (e) {
  console.log("ERROR " + e.name);
}
`;

function outboxLines(work) {
  const p = join(work, "outbox.txt");
  return existsSync(p) ? readFileSync(p, "utf8").trim().split("\n").filter(Boolean) : [];
}

async function killedAfterSending(work, file, env = {}) {
  const p = nodeChild(file, [work, "hang"], { env: { ...process.env, ...env } });
  assert.ok(await waitFor(() => p.out().includes("SENT")), "the child sent");
  p.child.kill("SIGKILL");
  const r = await p.done;
  assert.notEqual(r.code, 0, "the child was killed, it did not finish");
  assert.equal(outboxLines(work).length, 1);
}

test("P1 a child killed between the send and the record: the resume is in doubt and does not send again", async () => {
  const work = tempDir();
  const file = script(work, "child.mjs", CHILD);
  await killedAfterSending(work, file);
  const again = await nodeChild(file, [work, "plain"]).done;
  assert.match(again.stdout, /ERROR EffectInDoubt/);
  assert.equal(outboxLines(work).length, 1);
});

test("P2 the same crash with reconcile: the resume asks the outbox (the owner is dead) and records what it found", async () => {
  const work = tempDir();
  const file = script(work, "child.mjs", CHILD);
  await killedAfterSending(work, file, { RECONCILE: "1" });
  const again = await nodeChild(file, [work, "plain"], { env: { ...process.env, RECONCILE: "1" } }).done;
  assert.match(again.stdout, /RESULT found-in-outbox/);
  const third = await nodeChild(file, [work, "plain"], { env: { ...process.env, RECONCILE: "1" } }).done;
  assert.match(third.stdout, /RESULT found-in-outbox/);
  assert.equal(outboxLines(work).length, 1);
});

test("P3 two child processes on one record call the same approved action at once: one runs, both get its result", async () => {
  const work = tempDir();
  const file = script(work, "child.mjs", CHILD);
  c4a.init({ path: join(work, "rec.sqlite"), secret: "process-test-secret" });
  c4a.run("job-1", () => c4a.approve("proc.send", { to: "bob", amount: 10 }));      // ONE yes
  c4a.close();
  const env = { ...process.env, APPROVAL: "1" };
  const ps = [nodeChild(file, [work, "race"], { env }), nodeChild(file, [work, "race"], { env })];
  await sleep(1000);
  writeFileSync(join(work, "go"), "go");
  const rs = await Promise.all(ps.map((p) => p.done));
  assert.equal(outboxLines(work).length, 1, rs.map((r) => r.stdout + r.stderr).join("|"));
  const results = rs.map((r) => r.stdout.trim());
  assert.equal(new Set(results).size, 1, results.join("|"));
  assert.match(results[0], /^RESULT sent-/);
});

test("K29e a kill inside the call, then two processes resume at once with a truthful reconcile: 1 charge, twice", async () => {
  const charges = [];
  for (let i = 0; i < 2; i++) {
    const work = tempDir();
    const file = script(work, "child.mjs", CHILD);
    const env = { ...process.env, RECONCILE: "1" };
    const died = await nodeChild(file, [work, "die"], { env }).done;
    assert.notEqual(died.code, 0);
    const ps = [nodeChild(file, [work, "resume"], { env }), nodeChild(file, [work, "resume"], { env })];
    await sleep(1000);
    writeFileSync(join(work, "go"), "go");
    await Promise.all(ps.map((p) => p.done));
    charges.push(outboxLines(work).length);
  }
  assert.deepEqual(charges, [1, 1]);
});

test("P4 the owner check process.kill(pid, 0) does not end a live process (Windows too) and sees a dead one", async () => {
  const work = tempDir();
  const marker = join(work, "alive.txt");
  const file = script(work, "sleeper.mjs", `
import { writeFileSync } from "node:fs";
setTimeout(() => writeFileSync(${JSON.stringify(marker)}, "still here"), 1500);
`);
  const p = nodeChild(file);
  await sleep(300);
  const seen = [];
  for (let i = 0; i < 5; i++) {
    seen.push(c4a.owner.pidAlive(p.child.pid));
    await sleep(50);
  }
  const r = await p.done;
  assert.deepEqual(seen, [true, true, true, true, true]);
  assert.equal(r.code, 0, `the checked process ended normally (signal ${r.signal})`);
  assert.equal(readFileSync(marker, "utf8"), "still here");
  assert.equal(c4a.owner.pidAlive(p.child.pid), false);
});
