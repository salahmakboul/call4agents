// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// canonical() and the effect key: the same bytes as the Python kit, or a loud refusal.
// X1 compares with the real Python kit (skipped without Python; C4A_REQUIRE_PYTHON=1 makes that a failure).

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { c4a, needPython, REPO } from "./helpers.mjs";

const VECTORS = fileURLToPath(new URL("./fixtures/vectors.json", import.meta.url));
const PY_KEYS = fileURLToPath(new URL("./fixtures/python_keys.py", import.meta.url));

function value(v) {
  if (Array.isArray(v)) return v.map(value);
  if (v !== null && typeof v === "object") {
    const keys = Object.keys(v);
    if (keys.length === 1 && keys[0] === "$bigint") return BigInt(v.$bigint);
    return Object.fromEntries(keys.map((k) => [k, value(v[k])]));
  }
  return v;
}

test("C1 floats are written like Python's repr", () => {
  const cases = [[0.1, "0.1"], [1e-5, "1e-05"], [5e-5, "5e-05"], [0.0001, "0.0001"], [-2.5e-7, "-2.5e-07"],
    [1e-100, "1e-100"], [123456.789, "123456.789"], [5e-324, "5e-324"], [1.5, "1.5"], [0.30000000000000004, "0.30000000000000004"]];
  for (const [x, want] of cases) assert.equal(c4a.canonical(x), want, String(x));
  assert.equal(c4a.canonical(9007199254740991), "9007199254740991");
  assert.equal(c4a.canonical(2n ** 64n), "18446744073709551616");
});

test("C2 keys sorted by code point (Python order), not by UTF-16 unit; spacing and key order never matter", () => {
  assert.equal(c4a.canonical({ "￿": 1, "\u{1F600}": 2 }), '{"￿":1,"\u{1F600}":2}');
  assert.equal(c4a.canonical({ b: 1, a: { d: 2, c: 3 } }), c4a.canonical({ a: { c: 3, d: 2 }, b: 1 }));
  assert.equal(c4a.canonical({ "10": 1, "9": 2, a: 3, B: 4 }), '{"10":1,"9":2,"B":4,"a":3}');
});

test("C3 values JavaScript cannot key like Python are refused loudly (TypeError), never keyed differently", () => {
  const refused = {
    "2**53 as a number": { x: 2 ** 53 }, "1e16": { x: 1e16 }, "-0": { x: -0 }, NaN: { x: NaN }, Infinity: { x: Infinity },
    "a lone surrogate": { s: "\ud800" }, "a lone surrogate key": { "\udc00": 1 }, "undefined in an array": { a: [undefined] },
    "a Date": { d: new Date(0) }, "a Map": { m: new Map() }, "a function": { f() {} }, "a symbol": { s: Symbol("x") },
    "a class instance": { o: new (class Thing {})() },
  };
  for (const [name, args] of Object.entries(refused)) {
    assert.throws(() => c4a.canonical(args), TypeError, name);
    assert.throws(() => c4a.effectKey("r", "t", args), TypeError, name);
  }
  let deep = "x";
  for (let i = 0; i < 500; i++) deep = [deep];
  assert.throws(() => c4a.canonical(deep), /nested deeper than 500/);
  const cycle = {};
  cycle.self = cycle;
  assert.throws(() => c4a.canonical(cycle), TypeError);
  assert.equal(c4a.canonical({ a: undefined, b: 1 }), '{"b":1}', "an undefined property is left out, like JSON.stringify");
});

test("X1 cross-language: canonical text and effect key equal the Python kit's for every agreeing vector", (t) => {
  const exe = needPython(t);
  if (!exe) return;
  const data = JSON.parse(readFileSync(VECTORS, "utf8"));
  const r = spawnSync(exe, [PY_KEYS, VECTORS], { env: { ...process.env, PYTHONPATH: REPO }, encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  const py = r.stdout.trim().split("\n").map((l) => JSON.parse(l));
  const agree = py.filter((p) => p.group === "agree");
  assert.equal(agree.length, data.agree.length);
  assert.ok(agree.length >= 30, `at least 30 vectors (${agree.length})`);
  data.agree.forEach((v, i) => {
    const args = value(v);
    assert.equal(c4a.canonical(args), agree[i].canonical, `vector ${i}: ${JSON.stringify(v)}`);
    assert.equal(c4a.effectKey(data.run, data.tool, args), agree[i].key, `vector ${i}`);
  });
  // The known differences: a whole number written with a decimal point is a float in Python (10.0) and
  // a plain number in JavaScript; JavaScript writes 10 or refuses (1e16), never a third form.
  const differ = py.filter((p) => p.group === "differ");
  assert.equal(differ[0].canonical, '{"x":10.0}');
  assert.equal(c4a.canonical(value(data.differ[0])), '{"x":10}');
  assert.equal(differ[1].canonical, '{"x":1e+16}');
  assert.throws(() => c4a.canonical(value(data.differ[1])), TypeError);
});
