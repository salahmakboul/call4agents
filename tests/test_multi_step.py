"""The multi-step recipe in docs/MULTI_STEP.md, run for real (child processes, hard kills). Run: python -m tests.test_multi_step

MS1 control: without the kit, a crash after a step's effect makes that step happen twice on the restart.
MS2 with the kit and no reconcile=, no step happens twice at any crash point; the plan stops in doubt instead.
MS3 with reconcile= on each step, every crash point ends with exactly one reserve, one charge and one email.
MS4 a loop that carries on past an uncertain step lets later steps run; `call4agents story` names that case.
MS5 the same loop in an ordered run (`c4a.run(..., ordered=True)`) runs no later step: 0 charges, 0 emails.
The numbers in docs/MULTI_STEP.md are the ones these checks assert."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from call4agents.story import story

FAILED = []
ROOT = Path(__file__).resolve().parents[1]
CRASHES = [f"{s}:{w}" for s in ("reserve", "charge", "email") for w in ("before", "after")]


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {str(detail)[:500]}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def go(folder: Path, mode: str, crash: str = ""):
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    args = [sys.executable, "-m", "call4agents.examples.multi_step", str(folder), mode] + ([crash] if crash else [])
    return subprocess.run(args, capture_output=True, text=True, env=env, cwd=str(ROOT))


def cell(mode: str, crash: str):
    folder = Path(tempfile.mkdtemp())
    first = go(folder, mode, crash)
    second = go(folder, mode)
    shop = (folder / "shop.txt").read_text().splitlines() if (folder / "shop.txt").exists() else []
    counts = {s: sum(1 for l in shop if l.startswith(s + " ")) for s in ("reserve", "charge", "email")}
    return folder, first, second, counts


def main_test():
    plain = {c: cell("plain", c)[3] for c in CRASHES}
    twice = [c for c, n in plain.items() if max(n.values()) > 1]
    check("MS1 control: without the kit, 5 of 6 crash points repeat a step (the 6th crashes before anything ran)",
          sorted(twice) == sorted(c for c in CRASHES if c != "reserve:before"), plain)

    guard = {}
    for c in CRASHES:
        _, first, second, counts = cell("guard", c)
        guard[c] = (first.returncode, second.returncode, counts)
    check("MS2 with the kit, no step happens twice at any crash point",
          all(max(n.values()) <= 1 for _, _, n in guard.values()), guard)
    order = ("reserve", "charge", "email")
    later_ran = {c: n for c, (_, _, n) in guard.items()
                 if any(n[s] for s in order[order.index(c.split(":")[0]) + 1:])}
    check("MS2 ... and the resume stops in doubt every time (exit 3), nothing after the crashed step runs",
          all(r2 == 3 and r1 == 9 for r1, r2, _ in guard.values()) and not later_ran, (guard, later_ran))

    rec = {}
    for c in [""] + CRASHES:
        _, first, second, counts = cell("guard_reconcile", c)
        rec[c or "no crash"] = (second.returncode, counts)
    check("MS3 with reconcile=, every crash point and the no-crash control end with one of each step",
          all(r == 0 and n == {"reserve": 1, "charge": 1, "email": 1} for r, n in rec.values()), rec)

    folder, first, second, counts = cell("guard_carry_on", "reserve:after")
    check("MS4 carrying on past an uncertain reserve still runs charge and email",
          counts == {"reserve": 1, "charge": 1, "email": 1} and "uncertain" in second.stdout, (counts, second.stdout))
    page = story(str(folder / "record.sqlite"))
    check("MS4 story names the half-done change",
          "reserve(" in page and "later actions still started" in page
          and page.count("It may or may not have happened: it started") == 1 and page.count("Done: it ran") == 2, page)

    folder, first, second, counts = cell("guard_ordered", "reserve:after")
    check("MS5 the carry-on loop in an ordered run: no later step runs (charge 0, email 0), each is refused",
          counts == {"reserve": 1, "charge": 0, "email": 0} and second.stdout.count("uncertain") == 3,
          (counts, second.stdout, second.stderr[-300:]))
    page = story(str(folder / "record.sqlite"))
    check("MS5 story shows one uncertain action and no later one started",
          page.count("It may or may not have happened: it started") == 1 and "later actions still started" not in page
          and "Done: it ran" not in page, page)
    later = []
    for c in ("charge:before", "email:after"):
        folder, first, second, counts = cell("guard_ordered", c)
        later.append((c, counts))
    check("MS5 crashes at later steps: no step twice and nothing after the crashed step",
          later == [("charge:before", {"reserve": 1, "charge": 0, "email": 0}),
                    ("email:after", {"reserve": 1, "charge": 1, "email": 1})], later)

    if FAILED:
        print(f"{len(FAILED)} multi-step check(s) failed")
        return 1
    print("all multi-step checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main_test())
