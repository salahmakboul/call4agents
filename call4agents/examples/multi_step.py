# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""A change of several steps (reserve, then charge, then email) that crashes in the middle. See docs/MULTI_STEP.md.

    python -m call4agents.examples.multi_step <folder> plain|guard|guard_reconcile|guard_carry_on [crash]

crash is "<step>:before" or "<step>:after" (the process is killed before, or right after, the step's effect) and
is left out on the resume. The fake shop writes one line per effect to <folder>/shop.txt, so repeats can be
counted. The run record is <folder>/record.sqlite. Nothing is sent anywhere.

Modes:
  plain           no kit: a restart runs the whole plan again (the baseline)
  guard           each step is a @c4a.effect; a step that crashed mid-way stops the plan as in doubt
  guard_reconcile the same, and each step can ask the shop whether it already happened
  guard_carry_on  like guard, but the loop catches the in-doubt error and goes on, as an agent loop that hands
                  the error to the model may do; later steps then run while an earlier one is uncertain
"""
import os
import sys
from pathlib import Path

import call4agents as c4a

STEPS = ("reserve", "charge", "email")


def main(argv) -> int:
    folder, mode = Path(argv[0]), argv[1]
    crash = argv[2] if len(argv) > 2 else ""
    shop = folder / "shop.txt"

    def happened(step: str, order: str) -> bool:
        return shop.exists() and f"{step} {order}\n" in shop.read_text()

    def make(step: str):
        def body(order: str) -> str:
            if crash == f"{step}:before":
                os._exit(9)
            with open(shop, "a") as f:
                f.write(f"{step} {order}\n")
            if crash == f"{step}:after":
                os._exit(9)                       # the effect happened; its result is never saved
            return f"{step} ok"
        body.__name__ = step
        if mode == "plain":
            return body
        reconcile = None
        if mode == "guard_reconcile":
            # Ask the outside system. Return the result if it happened, None ONLY if it surely did not.
            def reconcile(order, _step=step):
                return f"{_step} ok" if happened(_step, order) else None
        return c4a.effect(body, name=f"shop.{step}", reconcile=reconcile)

    steps = [make(s) for s in STEPS]
    if mode == "plain":
        print("RESULT", [f(order="A-1") for f in steps])
        return 0
    c4a.init(str(folder / "record.sqlite"), secret=b"multi-step-example-secret")
    out = []
    try:
        with c4a.run("order-A-1"):
            for f in steps:
                try:
                    out.append(f(order="A-1"))
                except c4a.EffectInDoubt:
                    if mode != "guard_carry_on":
                        raise
                    out.append("uncertain")
    except c4a.EffectInDoubt as e:
        print("STOPPED in doubt:", str(e)[:120])
        return 3
    print("RESULT", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
