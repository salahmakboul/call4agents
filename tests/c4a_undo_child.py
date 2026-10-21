"""Child process for undo check U6: do three charges, then undo them with a crash half-way."""
import os
import sys
from pathlib import Path

import call4agents as c4a

work = Path(sys.argv[1])
mode = sys.argv[2]
c4a.init(str(work / "ledger.sqlite"), secret=b"u" * 16)
undone = {"n": 0}


def refund(n):
    with open(work / "refunds.txt", "a") as f:
        f.write(f"{n}\n")
    undone["n"] += 1
    if mode == "undo-crash" and undone["n"] == 2:
        os._exit(17)                     # dies right after the 2nd refund, before it is recorded


@c4a.effect(name="u6.charge", undo=refund)
def charge(n):
    return f"ch_{n}"


with c4a.run("u6"):
    if mode == "do":
        for n in (1, 2, 3):
            charge(n)
    else:
        try:
            c4a.undo()
        except c4a.UndoFailed:
            pass
