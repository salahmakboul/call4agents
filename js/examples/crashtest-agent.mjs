// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// A small agent loop for `call4agents crashtest --demo`. It saves its progress after each step, as a
// framework does. The refund goes to a local file, so nothing real is paid. AGENT_GUARD=1 adds the fix.

import { appendFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";

import * as c4a from "../dist/index.js";

c4a.init({ path: "record.sqlite", secret: "crashtest-demo-0" });

async function refund({ order, amount }) {
  appendFileSync("bank.txt", `refund ${amount} for ${order}\n`);
  return "refunded";
}

const doRefund = process.env.AGENT_GUARD === "1" ? c4a.effect(refund, { name: "refund" }) : refund;

const done = existsSync("progress.json") ? JSON.parse(readFileSync("progress.json", "utf8")) : [];
await c4a.run("ticket-481", async () => {
  for (const step of ["read the ticket", "refund", "reply to the customer"]) {
    if (done.includes(step)) continue;
    if (step === "refund") await doRefund({ order: "A-1042", amount: 49 });
    done.push(step);
    writeFileSync("progress.json", JSON.stringify(done));
  }
});
