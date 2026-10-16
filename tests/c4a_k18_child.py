"""Child process for kit check K18a: a METHOD effect with a SET argument, run in a new process each time.
Before the fix, `self` (an address) and the set's order (hash seed) changed the key in every process."""
import sys
from pathlib import Path

import call4agents as c4a


class Mailer:
    def __init__(self, outbox: Path):
        self.outbox = outbox

    @c4a.effect
    def send(self, to: set, body: str):
        with open(self.outbox, "a", encoding="utf-8") as f:
            f.write(",".join(sorted(to)) + "|" + body + "\n")
        return "sent"


if __name__ == "__main__":
    work = Path(sys.argv[1])
    c4a.init(str(work / "ledger.sqlite"), secret=b"s")
    with c4a.run("k18a"):
        Mailer(work / "outbox.txt").send({"ann", "bob", "cy", "dee", "eve"}, "hi")
