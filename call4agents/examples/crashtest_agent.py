# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""A small agent loop for `call4agents crashtest --demo`. It saves its progress after each step, as a framework does.

The refund goes to a local file, so nothing real is paid. AGENT_GUARD=1 adds the one-line fix."""
import json
import os

import call4agents as c4a

c4a.init("record.sqlite", secret=b"crashtest-demo-0")


def refund(order: str, amount: int) -> str:
    with open("bank.txt", "a") as f:
        f.write(f"refund {amount} for {order}\n")
    return "refunded"


if os.environ.get("AGENT_GUARD") == "1":
    refund = c4a.effect(refund)

done = json.load(open("progress.json")) if os.path.exists("progress.json") else []
with c4a.run("ticket-481"):
    for step in ("read the ticket", "refund", "reply to the customer"):
        if step not in done:
            if step == "refund":
                refund("A-1042", 49)
            done.append(step)
            with open("progress.json", "w") as f:
                json.dump(done, f)
