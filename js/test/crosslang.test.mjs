// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// One run record, two kits: a "yes" signed by one kit is spent by the other, and a finished call recorded
// by one kit is replayed (not run again) by the other. Skipped without Python (C4A_REQUIRE_PYTHON=1: failure).

import assert from "node:assert/strict";
import { hostname } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { c4a, needPython, runPython, tempDir } from "./helpers.mjs";

const SECRET = "xlang-secret-0000";
const PY = `
import json, sys
import call4agents as c4a
from call4agents import owner
path, step = sys.argv[1], sys.argv[2]
c4a.init(path, secret=b"${SECRET}")
ran = []
def pay(to, amount):
    ran.append(1)
    return {"paid": amount, "by": "python"}
pay = c4a.effect(pay, name="xlang.pay", approval=True)
with c4a.run("run-\\u00fc"):
    if step == "approve":
        c4a.approve("xlang.pay", to="bob", amount=10.5)
        print(json.dumps({"ok": True}))
    elif step == "host":
        print(json.dumps({"host": owner.me()["host"]}))
    else:
        try:
            print(json.dumps({"result": pay(to="bob", amount=10.5), "ran": len(ran)}))
        except c4a.ApprovalRequired:
            print(json.dumps({"error": "ApprovalRequired", "ran": len(ran)}))
        except c4a.EffectInDoubt:
            print(json.dumps({"error": "EffectInDoubt", "ran": len(ran)}))
`;

function jsPay(ran) {
  return c4a.effect(async ({ to, amount }) => {
    ran.push(to);
    return { paid: amount, by: "javascript" };
  }, { name: "xlang.pay", approval: true });
}

test("X2 a yes signed by Python is spent by JavaScript, once; Python then replays the JavaScript result", async (t) => {
  const exe = needPython(t);
  if (!exe) return;
  const path = join(tempDir(), "rec.sqlite");
  assert.deepEqual(JSON.parse(runPython(exe, PY, [path, "approve"])), { ok: true });
  c4a.init({ path, secret: SECRET });
  const ran = [];
  const pay = jsPay(ran);
  const r = await c4a.run("run-ü", () => pay({ to: "bob", amount: 10.5 }));
  assert.deepEqual(r, { paid: 10.5, by: "javascript" });
  c4a.close();
  const py = JSON.parse(runPython(exe, PY, [path, "call"]));
  assert.deepEqual(py, { result: { paid: 10.5, by: "javascript" }, ran: 0 });
  assert.deepEqual(ran, ["bob"]);
});

test("X3 a yes and a finished call recorded by JavaScript are read by Python; Python's yes is spent by Python only once", async (t) => {
  const exe = needPython(t);
  if (!exe) return;
  const path = join(tempDir(), "rec.sqlite");
  c4a.init({ path, secret: SECRET });
  const ran = [];
  const pay = jsPay(ran);
  await c4a.run("run-ü", async () => {
    c4a.approve("xlang.pay", { amount: 10.5, to: "bob" });
    await pay({ to: "bob", amount: 10.5 });
  });
  c4a.close();
  assert.deepEqual(JSON.parse(runPython(exe, PY, [path, "call"])), { result: { paid: 10.5, by: "javascript" }, ran: 0 });

  // the other way: Python records the finished call, JavaScript replays it
  const path2 = join(tempDir(), "rec.sqlite");
  runPython(exe, PY, [path2, "approve"]);
  assert.deepEqual(JSON.parse(runPython(exe, PY, [path2, "call"])), { result: { paid: 10.5, by: "python" }, ran: 1 });
  c4a.init({ path: path2, secret: SECRET });
  const ran2 = [];
  const r = await c4a.run("run-ü", () => jsPay(ran2)({ to: "bob", amount: 10.5 }));
  assert.deepEqual(r, { paid: 10.5, by: "python" });
  assert.deepEqual(ran2, []);
  c4a.close();
  // a JavaScript "no" cancels a Python "yes" (the JS denial's MAC matches Python's)
  const path3 = join(tempDir(), "rec.sqlite");
  runPython(exe, PY, [path3, "approve"]);
  c4a.init({ path: path3, secret: SECRET });
  c4a.run("run-ü", () => c4a.deny("xlang.pay", { to: "bob", amount: 10.5 }));
  c4a.close();
  assert.deepEqual(JSON.parse(runPython(exe, PY, [path3, "call"])), { error: "ApprovalRequired", ran: 0 });
});

test("X4 both kits name this machine the same way (an owner on 'another host' would wait for the lease)", (t) => {
  const exe = needPython(t);
  if (!exe) return;
  const path = join(tempDir(), "rec.sqlite");
  assert.equal(JSON.parse(runPython(exe, PY, [path, "host"])).host, hostname());
});
