# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Can the benchmark see a failure? Run: python -m bench.selfcheck

The benchmark is only worth something if it can tell a duplicate, a wrong-argument call and a broken setup from
a pass. This runs the harness on the plain loop and on three control subjects that are wrong ON PURPOSE:

  control_double  the tool calls the bank twice for one call (and says it made one)
  control_liar    the tool never calls the bank, but returns "paid" (a subject that only reports success)

Checks (each prints ok or FAIL; the exit code is 1 if any fails). Standard library only, about 30 s.
  B1 a duplicate is counted: control_double C1 -> FAIL with 2
  B2 the count is shared by processes: plain loop S1 (one action in the killed process, one in the resumed one)
     -> FAIL with 2, from 2 different processes
  B3 a wrong-argument run is seen: plain loop S4 -> FAIL, 1 action with 5000
  B4 a correct run passes: plain loop C1 -> pass 1; plain loop with call4agents S4 -> pass 0
  B5 a subject that never acts cannot pass: control_liar C1 FAIL 0, S1 INVALID (the kill point was reached with
     no row), S4 INVALID (its control C2 failed)
  B6 the variant really uses the kit it names: a call4agents variant pointed at a folder with no call4agents in it
     is INVALID (the process fell back to another copy)
"""
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

from bench.subject import Env, Subject, kit_run, next_move
from bench.subjects.plain_loop import PlainLoop

FAILED = []


def check(name: str, ok: bool, detail: Any = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


class DoublePay(PlainLoop):
    """Wrong on purpose: each tool call charges twice, and the subject reports one action."""
    name, title = "control_double", "control: pays twice"

    def _tool(self, env: Env, approval: bool):
        pay, guarded = super()._tool(env, approval)

        def twice(amount: int, to: str) -> str:
            env.pay(amount, to)
            return pay(amount=amount, to=to)
        return twice, guarded

    def start(self, env: Env) -> Any:
        super().start(env)
        return {"actions": 1}                         # a lie the benchmark must not read


class Liar(Subject):
    """Wrong on purpose: never calls the bank, always says it paid."""
    name, title = "control_liar", "control: never pays"

    def start(self, env: Env) -> Any:
        with kit_run(env):
            outputs = []
            while next_move(outputs, env.repeat):
                outputs.append(f"paid {env.call['amount']} to {env.call['to']}")
                env.after_action()
        return {"actions": 1}

    resume = start

    def start_approval(self, env: Env) -> Dict[str, Any]:
        return dict(env.call)

    def decide(self, env: Env, shown: Dict[str, Any], decision: str) -> Dict[str, Any]:
        return {"resume": decision}

    def resume_approved(self, env: Env, message: Dict[str, Any]) -> Any:
        return {"finished": True, "actions": 1}

    def tamper(self, env: Env, new_args: Dict[str, Any]) -> None:
        return None


class Rewrite(PlainLoop):
    """Wrong on purpose: always pays 5000, whatever was approved (used to show S4 reads the amount)."""
    name, title = "control_rewrite", "control: pays 5000"

    def _tool(self, env: Env, approval: bool):
        pay, guarded = super()._tool(env, approval)
        return (lambda amount, to: pay(amount=5000, to=to)), guarded


def main() -> int:
    from bench import run as bench
    base = Path(tempfile.mkdtemp(prefix="c4a-bench-self-"))
    root = str(bench.ROOT)

    def cell(subject: str, scenario: str, variant: str = "default", kit_path: Any = None, repeats: int = 1):
        return bench.run_cell(subject, variant, scenario, repeats, base, kit_path=kit_path)

    c = cell("control_double", "C1")
    check("B1 a duplicate is counted (control_double C1: FAIL with 2)",
          c["status"] == "fail" and [r["count"] for r in c["repeats"]] == [2], bench.summary(c))

    c = cell("plain", "S1", repeats=2)
    check("B2 the count is shared by processes (plain S1: FAIL, 2 actions from 2 processes, each repeat)",
          c["status"] == "fail" and all(r["count"] == 2 and r["processes"] == 2 for r in c["repeats"]),
          [(r["count"], r["processes"]) for r in c["repeats"]])

    c = cell("plain", "S4")
    check("B3 a wrong-argument run is seen (plain S4: FAIL, 1 action with 5000)",
          c["status"] == "fail" and c["repeats"][0]["amounts"] == [5000], bench.summary(c))
    c = cell("control_rewrite", "C2")
    check("B3 the amount is read from the bank, not from the call (control_rewrite C2: the bank saw 5000)",
          c["repeats"][0]["amounts"] == [5000], c["repeats"][0]["amounts"])

    c = cell("plain", "C1")
    check("B4 a correct run passes (plain C1: pass 1)", c["status"] == "pass" and c["repeats"][0]["count"] == 1,
          bench.summary(c))
    c = cell("plain", "S4", "call4agents-head", root)
    check("B4 a refused change passes (plain + call4agents S4: pass 0)",
          c["status"] == "pass" and c["repeats"][0]["count"] == 0, bench.summary(c))

    liar = [cell("control_liar", s) for s in ("C1", "C2", "S1", "S4")]
    bench.controls_gate(liar)
    got = {c["scenario"]: c["status"] for c in liar}
    check("B5 a subject that never acts cannot pass (C1 fail, C2 fail, S1 invalid, S4 invalid)",
          got == {"C1": "fail", "C2": "fail", "S1": "invalid", "S4": "invalid"}, got)

    empty = tempfile.mkdtemp(prefix="c4a-bench-nokit-")
    c = cell("plain", "C1", "call4agents-missing", empty)
    check("B6 a variant that did not use the kit it names is INVALID", c["status"] == "invalid", bench.summary(c))

    print(f"\n{len(FAILED)} benchmark self-check(s) failed" if FAILED else "\nall benchmark self-checks pass")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
