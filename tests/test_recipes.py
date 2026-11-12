"""The four recipes in docs/RECIPES.md, run for real (child processes, hard kills). Run: python -m tests.test_recipes

Each recipe's numbers are the ones the documentation shows. A control first proves that the fake providers can
count a duplicate: the same steps without the kit charge, book and refund twice."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from call4agents.examples import recipes

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


CONTROL = r"""
import os, sys
from pathlib import Path
from call4agents.examples import recipes
work = Path(sys.argv[1])
bank = recipes.Store(work, "bank", {"charges": [], "by_key": {}})
data = bank.load()
# without the kit, each try makes up a new request id, so the bank cannot see the repeat
data["charges"].append({"id": "ch_%d" % (len(data["charges"]) + 1)})
bank.save(data)
recipes.crash_here()
"""


def control():
    work = Path(tempfile.mkdtemp(prefix="c4a-recipe-control-"))
    script = work / "nokit.py"
    script.write_text(CONTROL, encoding="utf-8")
    env = recipes.child_env(False)      # the script is in a temp folder: it must find the package under test
    codes = []
    for crash in ("1", "0"):
        env[recipes.CRASH] = crash
        codes.append(subprocess.run([sys.executable, str(script), str(work)], env=env).returncode)
    charges = json.loads((work / "bank.json").read_text(encoding="utf-8"))["charges"]
    check("control: without the kit, a kill and a retry charge twice", codes == [17, 0] and len(charges) == 2,
          f"exit codes {codes}, charges {len(charges)}")


def story(name):
    """The recipe's numbers, or {} after a named FAIL when a step did not behave (e.g. the kill never came)."""
    try:
        return recipes.run_recipe(name)
    except RuntimeError as exc:
        check(f"{name}: every step behaves as written", False, str(exc)[:200])
        return {}


def main():
    print("recipes (real kills, fake providers in files)")
    control()
    got = story("payments")
    check("payments: 1 charge after a kill and a resume", got.get("charges") == 1, got)
    check("payments: the resume gets the first charge back", got.get("resumed_charge") == "ch_1", got)
    check("payments: the yes for 42 does not pay 4200", got.get("changed_amount") == "refused", got)

    got = story("bookings")
    check("bookings: 1 booking", got.get("bookings") == 1, got)
    check("bookings: a lagging search books nothing (UNKNOWN)", got.get("first_resume") == "in doubt", got)
    check("bookings: the next resume records the booking", got.get("second_resume") == "bk_1", got)

    got = story("emails")
    check("emails: 2 emails for 2 messages", got.get("emails") == 2, got)
    check("emails: refused -> sent -> in doubt -> the recorded answer",
          got.get("steps") == ["refused", "sent", "in doubt", "sent"], got)

    got = story("refunds")
    check("refunds: 2 refunds, 380 in total", (got.get("refunds"), got.get("total")) == (2, 380), got)
    check("refunds: limit, yes and rule each decide once",
          got.get("steps") == ["re_1", "needs a yes", "re_2", "over the daily limit", "refused by rule max.amount"], got)

    print(f"\n{'ALL RECIPE CHECKS PASSED' if not FAILED else 'FAILED: ' + ', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
