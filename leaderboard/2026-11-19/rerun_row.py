"""Rerun one row of the crash test leaderboard with one command, into a new folder, and count the payments.

    pip install langgraph langgraph-checkpoint-sqlite        # the framework of the row (see README.txt)
    python rerun_row.py langgraph                            # 30 crash runs + 30 controls, then the count
    python rerun_row.py langgraph --n 5 --guarded            # also the same runs with call4agents on the tool

Rows: langgraph, pydantic_ai, openai_agents, google_adk, microsoft_agent_framework.
--guarded needs `pip install call4agents` in the same Python.

Each run is a fresh temp folder and a separate process per phase. Fake models only: no API key, no network, no real
money (a "payment" is a line in a local file). The saved 19 Nov files next to this script are not touched: the
drivers and scripts are copied into my-rerun-<row>-<time>/ and write their run files there."""
import argparse
import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROWS = ["langgraph", "pydantic_ai", "openai_agents", "google_adk", "microsoft_agent_framework"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("row", choices=ROWS)
    ap.add_argument("--n", type=int, default=30, help="runs per case (default 30)")
    ap.add_argument("--guarded", action="store_true", help="also run the cases with call4agents on the tool")
    a = ap.parse_args()
    out = HERE / f"my-rerun-{a.row}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    out.mkdir()
    for f in ("rerun.py", "rerun_guarded.py", "tally.py", "tally_guarded.py"):
        shutil.copy2(HERE / f, out / f)
    for d in ("scripts", "scripts_guarded"):
        shutil.copytree(HERE / d, out / d, ignore=shutil.ignore_patterns("__pycache__", "DIFF.txt"))
    steps = [["rerun.py", a.row, "crash", str(a.n)], ["rerun.py", a.row, "control", str(a.n)]]
    if a.guarded:
        steps += [["rerun_guarded.py", a.row, "crash", str(a.n)], ["rerun_guarded.py", a.row, "control", str(a.n)]]
    for s in steps:
        print(f"== python {' '.join(s)}", flush=True)
        if subprocess.run([sys.executable, str(out / s[0]), *s[1:]], cwd=out).returncode:
            print(f"stopped: {s[0]} failed")
            return 2
    for t in ["tally"] + (["tally_guarded"] if a.guarded else []):
        r = subprocess.run([sys.executable, str(out / f"{t}.py")], cwd=out, capture_output=True, text=True)
        (out / f"{t}.txt").write_text(r.stdout, encoding="utf-8")
        print(r.stdout + r.stderr)
    print(f"run files and counts: {out}")
    print("To correct a row, open an issue or pull request with this folder (see README.txt).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
