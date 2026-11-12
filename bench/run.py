# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Run every subject x scenario, count the actions in the fake bank, write the results.

    python -m bench.run                         # all installed subjects, crash cells 5 times each
    python -m bench.run --subjects plain,langgraph --scenarios S1,S4 --repeat 3
    python -m bench.run --require-frameworks    # a subject that is not installed is an error, not a skip

Writes bench/results/<date>-<os>.json and bench/RESULTS.md (use --out DIR to write elsewhere, --no-markdown
to skip the table). Offline: scripted models, no keys, no network. See bench/scenarios.md for the rules.
"""
import concurrent.futures
import datetime
import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bench.subject import KILLED  # noqa: E402
from bench.subjects import SUBJECTS, SUBJECTS_AND_CONTROLS  # noqa: E402

CALL = {"amount": 50, "to": "alice"}
CHANGED = {"amount": 5000, "to": "alice"}
SCENARIOS = ["C1", "C2", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]
CRASH = {"S1", "S3", "S7"}                      # repeated N times: real kills and a real race
APPROVAL = {"C2", "S4", "S5", "S6", "S8"}
TITLES = {
    "C1": "control: a run with no fault makes one action",
    "C2": "control: an approved call runs once",
    "S1": "crash after the action, before the save; resume",
    "S2": "timeout after the action; retry",
    "S3": "two workers resume the same run at once",
    "S4": "yes for 50, the call changes to 5000",
    "S5": "one yes, the agent calls the tool twice",
    "S6": "no after yes, before it runs",
    "S7": "crash inside the action, the bank cannot tell yet",
    "S8": "the yes of run A delivered to run B",
}
STEP_TIMEOUT = 180


class Cell:
    """One scenario of one subject in one variant, in its own folder."""

    def __init__(self, subject: str, kit: bool, scenario: str, base: Path) -> None:
        self.subject, self.kit, self.scenario = subject, kit, scenario
        self.work = Path(tempfile.mkdtemp(prefix=f"bench-{subject}-{scenario}-", dir=base))
        self.steps: List[Dict[str, Any]] = []

    def step(self, step: str, run_id: str = "run-A", arg: Optional[dict] = None, fault: str = "",
             lookup: str = "truth", delay: float = 0.0, barrier: Optional[str] = None, repeat: int = 1,
             wait: bool = True):
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")])),
               "PYTHONDONTWRITEBYTECODE": "1", "BENCH_FAULT": fault, "BENCH_LOOKUP": lookup,
               "BENCH_DELAY": str(delay), "BENCH_BARRIER": barrier or ""}
        cmd = [sys.executable, "-m", "bench.child", self.subject, "1" if self.kit else "0", step, str(self.work),
               run_id, str(repeat), json.dumps(arg or {})]
        proc = subprocess.Popen(cmd, cwd=self.work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if not wait:
            return proc
        return self.finish(step, proc)

    def finish(self, step: str, proc: subprocess.Popen) -> Dict[str, Any]:
        try:
            out, err = proc.communicate(timeout=STEP_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            out += "\nBENCH_ERROR StepTimeout: the step ran longer than the benchmark allows"
        result, error = None, ""
        for line in out.splitlines():
            if line.startswith("BENCH_RESULT "):
                result = json.loads(line[len("BENCH_RESULT "):])
            elif line.startswith("BENCH_ERROR "):
                error = line[len("BENCH_ERROR "):]
        record = {"step": step, "code": proc.returncode, "result": result, "error": error}
        if proc.returncode not in (0, KILLED) and not error:
            record["error"] = "exited " + str(proc.returncode) + ": " + (err.strip().splitlines() or [""])[-1][:200]
        self.steps.append(record)
        return record

    def charges(self) -> List[Dict[str, Any]]:
        """What the bank saw: one row per action, read from the file every process wrote to."""
        p = self.work / "bank.sqlite"
        if not p.exists():
            return []
        import sqlite3
        db = sqlite3.connect(p)
        try:
            rows = db.execute("SELECT run_id, amount, to_, pid FROM charges ORDER BY seq").fetchall()
        finally:
            db.close()
        return [{"run_id": r[0], "amount": r[1], "to": r[2], "pid": r[3]} for r in rows]


def ended(step: Dict[str, Any]) -> str:
    """How the last step of a run ended, in a few words."""
    if step["code"] == KILLED:
        return "killed"
    if step["code"] == 0:
        r = step["result"]
        if isinstance(r, dict) and r.get("waiting") is not None:
            return "waiting for a new yes"
        return "finished"
    kind = step["error"].split(":", 1)[0]
    return f"stopped: {kind}"


def same_call(shown: Any, call: Dict[str, Any]) -> bool:
    try:
        return {k: (int(v) if k == "amount" else str(v)) for k, v in dict(shown).items()} == call
    except (TypeError, ValueError):
        return False


def play(cell: Cell) -> Dict[str, Any]:
    """Run one repeat of a scenario. Returns the bank's count and whether the setup reached its fault point
    (a repeat whose setup did not happen as designed is INVALID, never a pass)."""
    s, problems = cell.scenario, []

    def expect(ok: bool, what: str) -> None:
        if not ok:
            problems.append(what)

    if s == "C1":
        last = cell.step("start")
        expect(last["code"] == 0, "the run did not finish")
    elif s == "S1":
        first = cell.step("start", fault="kill_after_action")
        expect(first["code"] == KILLED and len(cell.charges()) == 1, "the kill after the action was not reached")
        last = cell.step("resume")
    elif s == "S2":
        first = cell.step("start", fault="timeout_in_action")
        expect(len(cell.charges()) >= 1, "the timeout after the action was not reached")
        last = first
        if first["code"] != 0:                   # the error reached the caller: the caller retries once
            last = cell.step("resume")
    elif s == "S3":
        first = cell.step("start", fault="kill_before_action")
        expect(first["code"] == KILLED and len(cell.charges()) == 0, "the kill before the action was not reached")
        go = str(cell.work / "go")
        procs = [cell.step("resume", delay=0.5, barrier=go, wait=False) for _ in range(2)]
        deadline = time.monotonic() + 120
        while len(list(cell.work.glob("go.ready.*"))) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        expect(len(list(cell.work.glob("go.ready.*"))) == 2, "the two workers did not both start")
        Path(go).write_text("go")
        steps = [cell.finish("resume", p) for p in procs]
        last = steps[-1]
    elif s == "S7":
        first = cell.step("start", fault="kill_in_action")
        expect(first["code"] == KILLED and len(cell.charges()) == 1, "the kill inside the action was not reached")
        last = cell.step("resume", lookup="unknown")
    elif s in APPROVAL:
        repeat = 2 if s == "S5" else 1
        first = cell.step("start_approval", repeat=repeat)
        shown = first["result"]
        expect(first["code"] == 0 and same_call(shown, CALL) and not cell.charges(),
               f"the run did not stop to wait for a yes on {CALL} (got {shown!r}, {first['error']!r})")
        if s == "S8":
            other = cell.step("start_approval", run_id="run-B")
            expect(other["code"] == 0 and same_call(other["result"], CALL), "run B did not stop to wait for a yes")
        if problems:
            return {"charges": cell.charges(), "problems": problems, "ended": ended(first)}
        yes = cell.step("decide", arg={"shown": shown, "decision": "yes"}, repeat=repeat)
        expect(yes["code"] == 0, f"recording the yes failed: {yes['error']}")
        message = (yes["result"] or {})
        if s == "S4":
            t = cell.step("tamper", arg={"new_args": CHANGED})
            expect(t["code"] == 0, f"changing the saved call failed: {t['error']}")
        if s == "S6":
            no = cell.step("decide", arg={"shown": shown, "decision": "no"})
            expect(no["code"] == 0, f"recording the no failed: {no['error']}")
        target = "run-B" if s == "S8" else "run-A"
        last = cell.step("resume_approved", run_id=target, arg={"message": message}, repeat=repeat)
    else:
        raise ValueError(f"unknown scenario {s}")
    return {"charges": cell.charges(), "problems": problems, "ended": ended(last)}


def judge(scenario: str, charges: List[Dict[str, Any]]) -> bool:
    """The pass rules (bench/scenarios.md). Only the bank's rows count."""
    n = len(charges)
    if scenario in ("C1", "C2", "S1", "S2", "S3", "S5", "S7"):
        return n == 1
    if scenario == "S4":
        return not any(c["amount"] == CHANGED["amount"] for c in charges)
    if scenario == "S6":
        return n == 0
    if scenario == "S8":
        return not any(c["run_id"] == "run-B" for c in charges)
    raise ValueError(scenario)


def counted(scenario: str, charges: List[Dict[str, Any]]) -> int:
    """The number shown in the table: run B's actions in S8, actions with 5000 in S4, else all actions."""
    if scenario == "S4":
        return sum(c["amount"] == CHANGED["amount"] for c in charges)
    if scenario == "S8":
        return sum(c["run_id"] == "run-B" for c in charges)
    return len(charges)


def run_cell(subject: str, kit: bool, scenario: str, repeats: int, base: Path, keep: bool = False) -> Dict[str, Any]:
    obj = load(subject)
    out: Dict[str, Any] = {"subject": subject, "variant": "call4agents" if kit else "default", "scenario": scenario}
    reason = obj.not_applicable(scenario, kit)
    if reason:
        return {**out, "status": "n/a", "reason": reason, "repeats": []}
    reps = []
    for _ in range(repeats):
        cell = Cell(subject, kit, scenario, base)
        try:
            r = play(cell)
            reps.append({"count": counted(scenario, r["charges"]), "actions": len(r["charges"]),
                         "amounts": [c["amount"] for c in r["charges"]], "runs": [c["run_id"] for c in r["charges"]],
                         "processes": len({c["pid"] for c in r["charges"]}), "pass": judge(scenario, r["charges"]),
                         "invalid": r["problems"], "ended": r["ended"], "steps": cell.steps})
        finally:
            if not keep:
                shutil.rmtree(cell.work, ignore_errors=True)
    if any(r["invalid"] for r in reps):
        status = "invalid"
    else:
        status = "pass" if all(r["pass"] for r in reps) else "fail"
    return {**out, "status": status, "repeats": reps}


_LOADED: Dict[str, Any] = {}


def load(subject: str) -> Any:
    if subject not in _LOADED:
        module, _, cls = SUBJECTS_AND_CONTROLS[subject].partition(":")
        _LOADED[subject] = getattr(importlib.import_module(module), cls)()
    return _LOADED[subject]


def installed(subject: str) -> bool:
    obj = load(subject)
    for mod in obj.requires:
        try:
            if importlib.util.find_spec(mod) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


def version(dist: str) -> Optional[str]:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def environment(subjects: List[str]) -> Dict[str, Any]:
    import call4agents
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                timeout=20).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {"python": platform.python_version(), "os": platform.system(), "os_release": platform.release(),
            "machine": platform.machine(), "call4agents": call4agents.__version__,
            "call4agents_from": str(Path(call4agents.__file__).resolve().parent), "repo_commit": commit,
            "packages": {d: version(d) for s in subjects for d in load(s).packages}}


def summary(cell: Dict[str, Any]) -> str:
    """'pass 5/5: 1' or 'FAIL 5/5: 2' or 'FAIL 3/5: 2, 2/5: 1'. The number is the scenario's count."""
    if cell["status"] == "n/a":
        return "N/A"
    reps = cell["repeats"]
    counts = Counter(r["count"] for r in reps)
    parts = ", ".join(f"{k}/{len(reps)}: {v}" for v, k in sorted(counts.items(), key=lambda kv: -kv[1]))
    word = {"pass": "pass", "fail": "FAIL", "invalid": "INVALID"}[cell["status"]]
    return f"{word} {parts}"


def write_markdown(doc: Dict[str, Any], path: Path, json_name: str) -> None:
    env = doc["environment"]
    cells = {(c["subject"], c["variant"], c["scenario"]): c for c in doc["cells"]}
    subjects = doc["subjects"]
    scen = doc["scenarios"]
    lines = ["# Results", "",
             f"Measured on **{env['os']} only** ({env['os']} {env['os_release']}, {env['machine']}), "
             f"Python {env['python']}, call4agents {env['call4agents']}, on {doc['date']}. "
             f"Raw data: [results/{json_name}](results/{json_name}).", "",
             "Other systems are not measured yet. The CI workflow (.github/workflows/bench.yml) runs the same "
             "benchmark on Linux, macOS and Windows and keeps the JSON as an artifact; it does not change this file.", "",
             "Each cell is `status repeats: count`. The count is what the fake bank recorded: actions in total "
             "(S1, S2, S3, S5, S7, C1, C2), actions with amount 5000 (S4), actions in run B (S8), actions (S6). "
             f"Crash cells (S1, S3, S7) ran {doc['repeat']} times each, the others once. The pass rules are in "
             "[scenarios.md](scenarios.md).", "",
             "Versions: " + ", ".join(f"{k} {v}" for k, v in sorted(env["packages"].items()) if v) + ".", ""]
    head = "| subject | variant | " + " | ".join(scen) + " |"
    lines += [head, "|" + "---|" * (len(scen) + 2)]
    for s in subjects:
        title = load(s).title
        for variant in ("default", "call4agents"):
            row = [summary(cells[(s, variant, x)]) if (s, variant, x) in cells else "-" for x in scen]
            lines.append(f"| {title} | {variant} | " + " | ".join(row) + " |")
    lines += ["", "Scenarios: " + "; ".join(f"**{k}** {TITLES[k]}" for k in scen) + ".", ""]

    native = [(s, x) for s in subjects for x in scen if x.startswith("S")
              and cells.get((s, "default", x), {}).get("status") == "pass"]
    lines += ["## Passed without call4agents", ""]
    if native:
        lines += ["These framework defaults already pass a scenario on their own. call4agents is not needed for them:", ""]
        for s, x in native:
            lines.append(f"- **{load(s).title}**, {x} ({TITLES[x]}): {summary(cells[(s, 'default', x)])}.")
    else:
        lines.append("None.")
    own = [(s, x) for s in subjects for x in scen
           if cells.get((s, "call4agents", x), {}).get("status") in ("fail", "invalid")]
    lines += ["", "## Failed with call4agents", ""]
    if own:
        for s, x in own:
            lines.append(f"- **{load(s).title}**, {x}: {summary(cells[(s, 'call4agents', x)])}.")
    else:
        lines.append("None in this run.")
    lines += ["", "## How each run ended", "",
              "A pass on the count does not mean the run finished. A run that stops in doubt waits for a person or "
              "for the provider to answer.", ""]
    for s in subjects:
        for variant in ("default", "call4agents"):
            for x in scen:
                c = cells.get((s, variant, x))
                if not c:
                    continue
                if c["status"] == "n/a":
                    lines.append(f"- {load(s).title} / {variant} / {x}: N/A. {c['reason']}")
                    continue
                how = Counter(r["ended"] for r in c["repeats"])
                bad = [p for r in c["repeats"] for p in r["invalid"]]
                text = ", ".join(f"{v} ({k}/{len(c['repeats'])})" for v, k in how.items())
                lines.append(f"- {load(s).title} / {variant} / {x}: {summary(c)}; ended: {text}"
                             + (f"; INVALID: {bad[0]}" if bad else ""))
    lines += ["", "## What the subjects do", ""]
    for s in subjects:
        o = load(s)
        lines.append(f"- **{o.title}** (bench/subjects/{SUBJECTS[s].split(':')[0].rsplit('.', 1)[1]}.py). "
                     f"Default: {o.default_config}. With call4agents: {o.kit_config}.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse(argv: List[str]) -> Dict[str, Any]:
    opts: Dict[str, Any] = {"subjects": list(SUBJECTS), "scenarios": list(SCENARIOS), "repeat": 5, "workers": 4,
                            "out": str(ROOT / "bench" / "results"), "markdown": True, "require": False, "keep": False}
    items = iter(argv)
    for a in items:
        if a == "--subjects":
            opts["subjects"] = next(items).split(",")
        elif a == "--scenarios":
            opts["scenarios"] = next(items).split(",")
        elif a == "--repeat":
            opts["repeat"] = int(next(items))
        elif a == "--workers":
            opts["workers"] = int(next(items))
        elif a == "--out":
            opts["out"] = next(items)
        elif a == "--no-markdown":
            opts["markdown"] = False
        elif a == "--require-frameworks":
            opts["require"] = True
        elif a == "--keep":
            opts["keep"] = True
        else:
            raise SystemExit(f"unknown option {a!r}; see the docstring of bench/run.py")
    return opts


def main(argv: List[str]) -> int:
    opts = parse(argv)
    subjects = []
    for s in opts["subjects"]:
        if s not in SUBJECTS_AND_CONTROLS:
            raise SystemExit(f"unknown subject {s!r}")
        if installed(s):
            subjects.append(s)
        elif opts["require"]:
            print(f"ERROR: {s} is not installed (--require-frameworks)")
            return 2
        else:
            print(f"skip  {s}: not installed")
    base = Path(tempfile.mkdtemp(prefix="c4a-bench-"))
    jobs = [(s, kit, x) for s in subjects for kit in (False, True) for x in opts["scenarios"]]
    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(opts["workers"]) as pool:
        futures = {pool.submit(run_cell, s, kit, x, opts["repeat"] if x in CRASH else 1, base, opts["keep"]): (s, kit, x)
                   for s, kit, x in jobs}
        cells = []
        for f in concurrent.futures.as_completed(futures):
            c = f.result()
            cells.append(c)
            print(f"  {c['subject']:14} {c['variant']:12} {c['scenario']:3} {summary(c)}", flush=True)
    if not opts["keep"]:
        shutil.rmtree(base, ignore_errors=True)
    order = {k: i for i, k in enumerate(jobs)}
    cells.sort(key=lambda c: order[(c["subject"], c["variant"] == "call4agents", c["scenario"])])
    date = datetime.date.today().isoformat()
    doc = {"format": "c4a-bench-1", "date": date, "seconds": round(time.time() - started, 1), "repeat": opts["repeat"],
           "subjects": subjects, "scenarios": opts["scenarios"], "environment": environment(subjects), "cells": cells}
    out = Path(opts["out"])
    out.mkdir(parents=True, exist_ok=True)
    name = f"{date}-{platform.system().lower()}.json"
    (out / name).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"\nwrote {out / name} ({doc['seconds']} s)")
    if opts["markdown"]:
        md = ROOT / "bench" / "RESULTS.md" if Path(opts["out"]).resolve() == (ROOT / "bench" / "results").resolve() \
            else out / "RESULTS.md"
        write_markdown(doc, md, name)
        print(f"wrote {md}")
    invalid = [c for c in cells if c["status"] == "invalid"]
    for c in invalid:
        print(f"INVALID {c['subject']} {c['variant']} {c['scenario']}: {c['repeats'][0]['invalid'] if c['repeats'] else ''}")
    return 1 if invalid else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
