// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// The core semantics, in one process: once-only, replay, errors, reconcile, approvals, keys, owner rules.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

import { c4a, fresh, sleep } from "./helpers.mjs";

test("J1 once-only: the same call in the same run runs once and replays the recorded result", async () => {
  fresh();
  const sent = [];
  const send = c4a.effect(async ({ to, body }) => {
    sent.push(to);
    return { id: `m-${sent.length}`, to, body };
  }, { name: "j1.send" });
  const a = await c4a.run("r1", () => send({ to: "bob", body: "hi" }));
  const b = await c4a.run("r1", () => send({ body: "hi", to: "bob" }));        // key order does not matter
  assert.deepEqual(sent, ["bob"]);
  assert.deepEqual(b, { id: "m-1", to: "bob", body: "hi" });
  assert.deepEqual(a, b);
  await c4a.run("r2", () => send({ to: "bob", body: "hi" }));                   // another run: a new action
  await c4a.run("r1", () => send({ to: "bob", body: "hi!" }));                  // other arguments: a new action
  assert.deepEqual(sent, ["bob", "bob", "bob"]);
});

test("J2 the tool name is required and part of the key; a run id is required", async () => {
  fresh();
  assert.throws(() => c4a.effect(async () => 1, {}), TypeError);
  const n = [];
  const a = c4a.effect(async () => n.push("a"), { name: "email.send" });
  const b = c4a.effect(async () => n.push("b"), { name: "slack.send" });
  await c4a.run("r", async () => {
    await a({ x: 1 });
    await b({ x: 1 });
  });
  assert.deepEqual(n, ["a", "b"]);
  await assert.rejects(() => a({ x: 2 }), /no current run/);
  await assert.rejects(() => c4a.run("r", () => a("text")), TypeError);       // ONE object argument
  await assert.rejects(() => c4a.run("r", () => a({ big: 2 ** 60 })), TypeError);
  assert.deepEqual(n, ["a", "b"]);
});

test("J3 a function that throws: the next call is in doubt at once, naming the error; retryOn runs it again", async () => {
  fresh();
  let tries = 0;
  const flaky = c4a.effect(async () => {
    tries += 1;
    throw new Error("timeout after the charge");
  }, { name: "j3.pay" });
  await c4a.run("r", async () => {
    await assert.rejects(() => flaky({ amount: 5 }), /timeout after the charge/);
    const t0 = Date.now();
    await assert.rejects(() => flaky({ amount: 5 }), (e) => e instanceof c4a.EffectInDoubt && /it raised Error/.test(e.message));
    assert.ok(Date.now() - t0 < 1000, "no wait after a recorded error");
  });
  assert.equal(tries, 1);

  class Refused extends Error {}
  let n = 0;
  const safe = c4a.effect(async () => {
    n += 1;
    if (n === 1) throw new Refused("connection refused before sending");
    return "paid";
  }, { name: "j3.safe", retryOn: [Refused] });
  await c4a.run("r", async () => {
    await assert.rejects(() => safe({ amount: 5 }), Refused);
    assert.equal(await safe({ amount: 5 }), "paid");
    assert.equal(await safe({ amount: 5 }), "paid");
  });
  assert.equal(n, 2);
  let m = 0;
  const byTest = c4a.effect(async () => {
    m += 1;
    if (m === 1) throw new Error("E_REFUSED");
    return "ok";
  }, { name: "j3.test", retryOn: (e) => /E_REFUSED/.test(String(e?.message)) });
  await c4a.run("r", async () => {
    await assert.rejects(() => byTest({}), /E_REFUSED/);
    assert.equal(await byTest({}), "ok");
  });
});

/** An intent with no result, written by a process that is gone (a dead pid on this host). */
function orphanIntent(lg, runId, tool, args) {
  const dead = spawnSync(process.execPath, ["-e", "process.stdout.write(String(process.pid))"], { encoding: "utf8" });
  const key = c4a.effectKey(runId, tool, args);
  lg.append(runId, "tool_intent", c4a.intentKey(key, 1),
    { tool, args, owner: { host: c4a.owner.me().host, pid: Number(dead.stdout), token: "gone" } });
  return key;
}

test("J4 reconcile after a crash: a result is recorded, null runs once, UNKNOWN stays in doubt, undefined is refused", async () => {
  const { lg } = fresh();
  let sent = 0;
  let answer = c4a.UNKNOWN;
  const pay = c4a.effect(async () => {
    sent += 1;
    return "paid-now";
  }, { name: "j4.pay", reconcile: async () => answer, wait: 0.05 });
  orphanIntent(lg, "r", "j4.pay", { amount: 7 });
  await c4a.run("r", async () => {
    await assert.rejects(() => pay({ amount: 7 }), (e) => e instanceof c4a.EffectInDoubt && /could not tell yet/.test(e.message));
    answer = undefined;
    await assert.rejects(() => pay({ amount: 7 }), /returned undefined/);
    answer = "paid-before";
    assert.equal(await pay({ amount: 7 }), "paid-before");
    assert.equal(await pay({ amount: 7 }), "paid-before");
  });
  assert.equal(sent, 0, "UNKNOWN and a found result never run it");

  orphanIntent(lg, "r", "j4.pay", { amount: 8 });
  answer = null;
  await c4a.run("r", async () => {
    assert.equal(await pay({ amount: 8 }), "paid-now");
    assert.equal(await pay({ amount: 8 }), "paid-now");
  });
  assert.equal(sent, 1, "null (did not happen) runs it once");

  orphanIntent(lg, "r", "j4.none", { amount: 9 });
  const plain = c4a.effect(async () => "x", { name: "j4.none", wait: 0.05 });
  await assert.rejects(() => c4a.run("r", () => plain({ amount: 9 })), (e) => e instanceof c4a.EffectInDoubt && /crash during the effect/.test(e.message));
});

test("J5 approval is bound to the exact arguments; one yes = one action; deny revokes", async () => {
  fresh();
  const sent = [];
  let job = "a";
  const pay = c4a.effect(async ({ to, amount }) => {
    sent.push(`${to}:${amount}`);
    return sent.length;
  }, { name: "j5.pay", approval: true, key: ({ to, amount }) => `${to}/${amount}/${job}` });
  await c4a.run("r", async () => {
    await assert.rejects(() => pay({ to: "bob", amount: 10 }), c4a.ApprovalRequired);
    c4a.approve("j5.pay", { to: "bob", amount: 10 });
    await assert.rejects(() => pay({ to: "bob", amount: 11 }), c4a.ApprovalRequired);    // other arguments: no
    await assert.rejects(() => pay({ to: "eve", amount: 10 }), c4a.ApprovalRequired);
    assert.equal(await pay({ amount: 10, to: "bob" }), 1);
    assert.equal(await pay({ to: "bob", amount: 10 }), 1);                                 // a retry replays
    job = "b";                                                                             // a deliberate second action
    await assert.rejects(() => pay({ to: "bob", amount: 10 }), c4a.ApprovalRequired);
    c4a.approve(pay, { to: "bob", amount: 10 });
    assert.equal(await pay({ to: "bob", amount: 10 }), 2);
    job = "c";
    c4a.approve("j5.pay", { to: "bob", amount: 10 });
    c4a.deny("j5.pay", { to: "bob", amount: 10 });                                         // a "no" cancels the "yes"
    await assert.rejects(() => pay({ to: "bob", amount: 10 }), c4a.ApprovalRequired);
  });
  assert.deepEqual(sent, ["bob:10", "bob:10"]);
});

test("J6 deny also stops an action that is in doubt from being resumed by reconcile", async () => {
  const { lg } = fresh();
  let sent = 0;
  const pay = c4a.effect(async () => { sent += 1; return "ok"; }, { name: "j6.pay", approval: true, reconcile: () => null, wait: 0.05 });
  await c4a.run("r", async () => {
    c4a.approve("j6.pay", { amount: 3 });
  });
  // the approved first attempt was admitted and its process died
  const key = c4a.effectKey("r", "j6.pay", { amount: 3 });
  assert.equal(lg.claimApproved("r", "j6.pay", { amount: 3 }, key, c4a.intentKey(key, 1),
    { tool: "j6.pay", args: { amount: 3 }, owner: { host: c4a.owner.me().host, pid: 999999999, token: "x" } }), true);
  await c4a.run("r", async () => {
    c4a.deny("j6.pay", { amount: 3 });
    await assert.rejects(() => pay({ amount: 3 }), c4a.ApprovalRequired);
  });
  assert.equal(sent, 0);
});

test("J7 a named action reused with other arguments is a KeyConflict; the same amount written differently is not", async () => {
  fresh();
  const n = [];
  const pay = c4a.effect(async ({ amount }) => { n.push(amount); return "ok"; }, { name: "j7.pay", key: () => "order-1" });
  await c4a.run("r", async () => {
    await pay({ amount: 10 });
    assert.equal(await pay({ amount: "10.00" }), "ok");
    await assert.rejects(() => pay({ amount: 11 }), c4a.KeyConflict);
  });
  assert.deepEqual(n, [10]);
});

test("J8 idempotencyKey() is the same on every retry and differs per action", async () => {
  fresh();
  const keys = [];
  class Refused extends Error {}
  const pay = c4a.effect(async () => {
    keys.push(c4a.idempotencyKey());
    if (keys.length === 1) throw new Refused("x");
    return c4a.idempotencyHeaders()["Idempotency-Key"];
  }, { name: "j8.pay", retryOn: [Refused] });
  await c4a.run("r", async () => {
    await assert.rejects(() => pay({ a: 1 }), Refused);
    await pay({ a: 1 });
    await pay({ a: 2 });
  });
  assert.equal(keys[0], keys[1]);
  assert.notEqual(keys[1], keys[2]);
  assert.throws(() => c4a.idempotencyKey(), /only available inside/);
});

test("J9 two callers in one process race on the same action: one runs, the other gets its result", async () => {
  fresh();
  let n = 0;
  const pay = c4a.effect(async () => {
    n += 1;
    await sleep(150);
    return "paid";
  }, { name: "j9.pay" });
  const out = await c4a.run("r", () => Promise.all([pay({ a: 1 }), pay({ a: 1 }), pay({ a: 1 })]));
  assert.deepEqual(out, ["paid", "paid", "paid"]);
  assert.equal(n, 1);
});

test("K29a (Fix 17) a call still running in this process is not reconciled: 1 payment, the other caller is in doubt", async () => {
  fresh();
  const bank = [];
  const out = [];
  const slow = c4a.effect(async ({ amount }) => {
    await sleep(500);
    bank.push(amount);
    return "paid";
  }, { name: "k29.slow", reconcile: () => (bank.length ? "paid" : null), wait: 0.1 });
  const caller = async (delay) => {
    await sleep(delay);
    try {
      out.push(await c4a.run("k29a", () => slow({ amount: 10 })));
    } catch (e) {
      out.push(String(e.message));
    }
  };
  await Promise.all([caller(0), caller(50)]);
  assert.deepEqual(bank, [10], JSON.stringify(out));
  assert.ok(out.includes("paid"));
  assert.ok(out.some((o) => o.includes("still running in this process")), JSON.stringify(out));
});

test("K29b (Fix 17) the first try raised; two callers then reconcile at once and start ONE new attempt", async () => {
  const { lg } = fresh();
  const bank = [];
  let tries = 0;
  let arrived = 0;
  const flaky = c4a.effect(async ({ amount }) => {
    tries += 1;
    if (tries === 1) throw new Error("connection refused before sending");
    await sleep(200);
    bank.push(amount);
    return "paid";
  }, {
    name: "k29.flaky", wait: 10,
    reconcile: async () => {
      const found = bank.length ? "paid" : null;
      arrived += 1;
      while (arrived < 2) await sleep(5);          // both callers have asked before either acts
      return found;
    },
  });
  await c4a.run("k29b", async () => {
    await assert.rejects(() => flaky({ amount: 10 }), /refused before sending/);
  });
  const out = await c4a.run("k29b", () => Promise.all([flaky({ amount: 10 }), flaky({ amount: 10 })].map((p) => p.catch((e) => e.name))));
  const intents = lg.entries().filter((e) => e.kind === "tool_intent");
  assert.deepEqual(bank, [10]);
  assert.deepEqual(out, ["paid", "paid"]);
  assert.equal(intents.length, 2, "attempt 1 (raised) and ONE new attempt 2");
});

test("K29c an attempt is given up once; the next attempt is newest closed + 1", () => {
  const { lg } = fresh();
  assert.equal(lg.giveUp("k29c", "E", 1), true);
  assert.equal(lg.giveUp("k29c", "E", 1), false);
  assert.equal(lg.nextAttempt("E"), 2);
  lg.append("k29c", "tool_abandoned", "E", { attempt: 1 });          // a duplicate row, as old records can have
  assert.equal(lg.nextAttempt("E"), 2);
  lg.append("k29c", "tool_intent", "E#3", {});
  assert.equal(lg.giveUp("k29c", "E", 2), false, "a newer attempt exists");
});

test("K29d the owner check: running only while it can be (pid, token, host, lease); lease must be above 0", () => {
  const o = c4a.owner;
  const me = o.me();
  const now = Date.now() / 1000;
  const dead = Number(spawnSync(process.execPath, ["-e", "process.stdout.write(String(process.pid))"], { encoding: "utf8" }).stdout);
  const intent = (owner, age) => ({ payload: { owner }, t: now - age });
  const cases = {
    "other host, young": o.mayStillRun("i", intent({ ...me, host: me.host + "-x" }, 5), 600) !== null,
    "other host, past the lease": o.mayStillRun("i", intent({ ...me, host: me.host + "-x" }, 700), 600) === null,
    "our pid, another process before us": o.mayStillRun("i", intent({ ...me, token: "old" }, 5), 600) === null,
    "a dead process on this host": o.mayStillRun("i", intent({ ...me, pid: dead, token: "t" }, 5), 600) === null,
    "a live process on this host": o.mayStillRun("i", intent({ ...me, pid: process.ppid, token: "t" }, 5), 600) !== null,
    "a live process on this host, past the lease": o.mayStillRun("i", intent({ ...me, pid: process.ppid, token: "t" }, 700), 600) === null,
    "this process, call finished": o.mayStillRun("i", intent(me, 5), 600) === null,
    "written before 0.8 (no owner)": o.mayStillRun("i", { payload: {}, t: now }, 600) === null,
  };
  o.started("i");
  cases["this process, call in progress"] = o.mayStillRun("i", intent(me, 5), 600) !== null;
  o.finished("i");
  assert.deepEqual(Object.entries(cases).filter(([, v]) => !v).map(([k]) => k), []);
  assert.throws(() => c4a.effect(async () => 1, { name: "k29.bad", lease: 0 }), RangeError);
});

test("J10 idempotentFor: an in-doubt action of a dead owner is resent with the SAME key, once", async () => {
  const { lg } = fresh();
  const keys = [];
  const pay = c4a.effect(async () => { keys.push(c4a.idempotencyKey()); return "ok"; }, { name: "j10.pay", idempotentFor: 86400, wait: 0.05 });
  const key = orphanIntent(lg, "r", "j10.pay", { a: 1 });
  await c4a.run("r", async () => {
    assert.equal(await pay({ a: 1 }), "ok");
    assert.equal(await pay({ a: 1 }), "ok");
  });
  assert.deepEqual(keys, [key]);
  assert.equal(lg.entriesForKey(key, "tool_retry_same_key").length, 1);
});
