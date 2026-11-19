# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Is a framework subject wired the way bench/subject.py says? Run: python -m bench.subjectcheck <subject> ...

bench/selfcheck.py proves the harness can see a failure; it runs no framework. This checks the wiring of real
subjects, with checks that do not depend on how well the framework does (so a framework that gets better or
worse does not break them). Each prints ok or FAIL; the exit code is 1 if any fails. A named subject whose
framework is not installed is a FAIL, not a skip (this is used by the mutation batch).

  W1 the tool calls the bank: default C1 -> pass with 1 action
  W2 the call4agents variant guards the tool: C1 with this checkout's kit -> pass with 1 action, and the kit's
     record holds an entry for bench.pay in that run
  W3 the kill after the action happens inside the tool: default S1 is not INVALID (env.after_action() is called)
  W4 tamper() changes the call the run will use: default S4 is not INVALID and did not pay the old amount 50
     (a framework that refuses the change pays nothing; one that runs it pays 5000)
"""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, List

FAILED: List[str] = []


def check(name: str, ok: bool, detail: Any = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def guarded_entries(base: Path, subject: str) -> int:
    n = 0
    for rec in base.glob(f"bench-{subject}-C1-*/c4a/record.sqlite"):
        db = sqlite3.connect(rec)
        try:
            n += db.execute("SELECT COUNT(*) FROM entries WHERE run_id = 'run-A' AND (key LIKE '%bench.pay%' "
                            "OR payload LIKE '%bench.pay%')").fetchone()[0]
        finally:
            db.close()
    return n


def main(subjects: List[str]) -> int:
    from bench import run as bench
    if not subjects:
        print("usage: python -m bench.subjectcheck <subject> [<subject> ...]")
        return 2
    root = str(bench.ROOT)
    for s in subjects:
        print(f"{s}:")
        if s not in bench.SUBJECTS or not bench.installed(s):
            check(f"{s} is a registered subject with its framework installed", False, "not registered or not installed")
            continue
        base = Path(tempfile.mkdtemp(prefix="c4a-bench-wire-"))
        try:
            c = bench.run_cell(s, "default", "C1", 1, base)
            check("W1 the tool calls the bank (default C1: pass with 1 action)",
                  c["status"] == "pass" and c["repeats"][0]["actions"] == 1, bench.summary(c))

            c = bench.run_cell(s, "call4agents-head", "C1", 1, base, keep=True, kit_path=root)
            found = guarded_entries(base, s)
            check("W2 the kit variant guards the tool (C1 pass with 1, the record has bench.pay)",
                  c["status"] == "pass" and c["repeats"][0]["actions"] == 1 and found > 0,
                  f"{bench.summary(c)}, {found} record entries for bench.pay")

            c = bench.run_cell(s, "default", "S1", 1, base)
            check("W3 the kill after the action happens inside the tool (default S1 not INVALID)",
                  c["status"] != "invalid", c["repeats"][0]["invalid"] if c["repeats"] else c)

            c = bench.run_cell(s, "default", "S4", 1, base)
            amounts = c["repeats"][0]["amounts"] if c["repeats"] else None
            check("W4 tamper changes the call the run uses (default S4 not INVALID, the old 50 not paid)",
                  c["status"] != "invalid" and amounts is not None and 50 not in amounts,
                  f"{bench.summary(c)}, amounts {amounts}, {c['repeats'][0]['invalid'] if c['repeats'] else ''}")
        finally:
            shutil.rmtree(base, ignore_errors=True)
    print(f"\n{len(FAILED)} subject check(s) failed" if FAILED else "\nall subject checks pass")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
