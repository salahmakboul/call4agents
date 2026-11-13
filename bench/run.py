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

    def __init__(self, subject: str, variant: str, scenario: str, base: Path, kit_path: Optional[str] = None) -> None:
        self.subject, self.variant, self.scenario = subject, variant, scenario
        self.kit = variant != "default"
        self.kit_path = kit_path              # the folder that holds the call4agents package of this variant
        self.work = Path(tempfile.mkdtemp(prefix=f"bench-{subject}-{scenario}-", dir=base))
        self.steps: List[Dict[str, Any]] = []

    def step(self, step: str, run_id: str = "run-A", arg: Optional[dict] = None, fault: str = "",
             lookup: str = "truth", delay: float = 0.0, barrier: Optional[str] = None, repeat: int = 1,
             wait: bool = True):
        # The kit of this variant comes FIRST on the path, so `import call4agents` finds that version.
        path = [self.kit_path, str(ROOT)] if self.kit_path else [str(ROOT)]
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, path + [os.environ.get("PYTHONPATH", "")])),
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
        result, error, kit = None, "", None
        for line in out.splitlines():
            if line.startswith("BENCH_RESULT "):
                result = json.loads(line[len("BENCH_RESULT "):])
            elif line.startswith("BENCH_ERROR "):
                error = line[len("BENCH_ERROR "):]
            elif line.startswith("BENCH_KIT "):
                kit = line[len("BENCH_KIT "):]
        record = {"step": step, "code": proc.returncode, "result": result, "error": error}
        if kit is not None:
            record["kit"] = kit           # which call4agents the step really imported (checked in run_cell)
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
        procs = [cell.step("resume", delay=1.0, barrier=go, wait=False) for _ in range(2)]
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


def wrong_kit(cell: Cell) -> List[str]:
    """A step that used another call4agents than its variant names (or any, in the default variant)."""
    out = []
    for s in cell.steps:
        used = s.get("kit")
        if used is None:
            continue
        if not cell.kit:
            out.append(f"the default variant imported call4agents ({used})")
        elif Path(used.split(" ", 1)[1]).resolve() != Path(cell.kit_path).resolve():
            out.append(f"step {s['step']} used call4agents {used}, not the one in {cell.kit_path}")
    return out


def run_cell(subject: str, variant: str, scenario: str, repeats: int, base: Path, keep: bool = False,
             kit_path: Optional[str] = None) -> Dict[str, Any]:
    obj = load(subject)
    kit = variant != "default"
    out: Dict[str, Any] = {"subject": subject, "variant": variant, "scenario": scenario}
    reason = obj.not_applicable(scenario, kit)
    if reason:
        return {**out, "status": "n/a", "reason": reason, "repeats": []}
    reps = []
    for _ in range(repeats):
        cell = Cell(subject, variant, scenario, base, kit_path)
        try:
            r = play(cell)
            reps.append({"count": counted(scenario, r["charges"]), "actions": len(r["charges"]),
                         "amounts": [c["amount"] for c in r["charges"]], "runs": [c["run_id"] for c in r["charges"]],
                         "processes": len({c["pid"] for c in r["charges"]}), "pass": judge(scenario, r["charges"]),
                         "invalid": r["problems"] + wrong_kit(cell), "ended": r["ended"], "steps": cell.steps})
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


def _git(*args: str) -> Optional[str]:
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else None


def kit_version(path: Path) -> str:
    """__version__ as written in that copy of call4agents/__init__.py (read, not imported)."""
    for line in (path / "call4agents" / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__ = "):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "?"


def release_kit(release: str, base: Path) -> Dict[str, Any]:
    """The released call4agents `release` in its own folder: from the git tag v<release> when the checkout has it,
    else from PyPI (pip install --no-deps --target). Only the package folder, so `bench` still comes from here."""
    import tarfile
    dest = base / f"kit-{release}"
    dest.mkdir(parents=True, exist_ok=True)
    tag = f"v{release}"
    try:
        r = subprocess.run(["git", "archive", "--format=tar", tag, "call4agents"], cwd=ROOT, capture_output=True,
                           timeout=120)
        ok = r.returncode == 0 and r.stdout
    except (OSError, subprocess.SubprocessError):
        ok = False
    if ok:
        tar = base / f"kit-{release}.tar"
        tar.write_bytes(r.stdout)
        with tarfile.open(tar) as t:
            t.extractall(dest, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(dest)
        source = f"git tag {tag} ({_git('rev-parse', '--short', tag + '^{commit}')})"
    else:
        p = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--no-deps", "--target", str(dest),
                            f"call4agents=={release}"], capture_output=True, text=True, timeout=600)
        if p.returncode != 0:
            raise SystemExit(f"could not get call4agents {release} (no tag {tag}, pip failed: {p.stderr[-300:]})")
        source = f"PyPI call4agents=={release}"
    found = kit_version(dest)
    if found != release:
        raise SystemExit(f"the copy of call4agents {release} says it is {found}")
    return {"path": str(dest), "version": found, "source": source, "label": f"call4agents {found} (release)"}


def head_kit() -> Dict[str, Any]:
    commit = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--", "call4agents"))
    v = kit_version(ROOT)
    where = f"{commit}{' + uncommitted changes' if dirty else ''}" if commit else "this folder"
    return {"path": str(ROOT), "version": v, "source": f"this checkout ({where})",
            "label": f"call4agents head ({commit or 'local'})"}


def environment(subjects: List[str], kits: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {"python": platform.python_version(), "os": platform.system(), "os_release": platform.release(),
            "machine": platform.machine(), "repo_commit": _git("rev-parse", "--short", "HEAD"),
            "kits": {k: {x: v for x, v in info.items() if x != "path"} for k, info in kits.items()},
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


def label(doc: Dict[str, Any], variant: str) -> str:
    return "default" if variant == "default" else doc["environment"]["kits"][variant]["label"]


def write_markdown(doc: Dict[str, Any], path: Path, json_name: str) -> None:
    env = doc["environment"]
    cells = {(c["subject"], c["variant"], c["scenario"]): c for c in doc["cells"]}
    subjects, scen, variants = doc["subjects"], doc["scenarios"], doc["variants"]
    kits = [v for v in variants if v != "default"]
    lines = ["# Results", "",
             f"Measured on **{env['os']} only** ({env['os']} {env['os_release']}, {env['machine']}), "
             f"Python {env['python']}, on {doc['date']}. Raw data: [results/{json_name}](results/{json_name}).", "",
             "Other systems are not measured yet. The CI workflow (.github/workflows/bench.yml) runs the same "
             "benchmark on Linux, macOS and Windows and keeps the JSON as an artifact; it does not change this file.", "",
             "Each cell is `status repeats: count`. The count is what the fake bank recorded: all actions (C1, C2, "
             "S1, S2, S3, S5, S6, S7), actions with amount 5000 (S4), actions in run B (S8). "
             f"Crash cells (S1, S3, S7) ran {doc['repeat']} times each, the others once. The pass rules are in "
             "[scenarios.md](scenarios.md).", "",
             "Framework versions: " + ", ".join(f"{k} {v}" for k, v in sorted(env["packages"].items()) if v) + ".", ""]
    for k in kits:
        info = env["kits"][k]
        lines.append(f"- **{info['label']}**: call4agents {info['version']} from {info['source']}.")
    lines += ["", "| subject | variant | " + " | ".join(scen) + " |", "|" + "---|" * (len(scen) + 2)]
    for s in subjects:
        for v in variants:
            row = [summary(cells[(s, v, x)]) if (s, v, x) in cells else "-" for x in scen]
            lines.append(f"| {load(s).title} | {label(doc, v)} | " + " | ".join(row) + " |")
    lines += ["", "Scenarios: " + "; ".join(f"**{k}** {TITLES[k]}" for k in scen) + ".", ""]

    native = [(s, x) for s in subjects for x in scen if x.startswith("S")
              and cells.get((s, "default", x), {}).get("status") == "pass"]
    lines += ["## Passed without call4agents", ""]
    if native:
        lines += ["These framework defaults pass a scenario on their own. call4agents is not needed for them:", ""]
        for s, x in native:
            lines.append(f"- **{load(s).title}**, {x} ({TITLES[x]}): {summary(cells[(s, 'default', x)])}.")
    else:
        lines.append("None.")
    lines += ["", "## Failed with call4agents", ""]
    own = [(s, k, x) for k in kits for s in subjects for x in scen
           if cells.get((s, k, x), {}).get("status") in ("fail", "invalid")]
    for s, k, x in own:
        lines.append(f"- **{load(s).title}**, {label(doc, k)}, {x} ({TITLES[x]}): {summary(cells[(s, k, x)])}.")
    if not own:
        lines.append("None in this run.")
    for old, new in zip(kits, kits[1:]):            # each call4agents version against the next one
        changed = [(s, x) for s in subjects for x in scen
                   if cells.get((s, old, x), {}).get("status") != cells.get((s, new, x), {}).get("status")]
        lines += ["", f"## Different between {label(doc, old)} and {label(doc, new)}", ""]
        for s, x in changed:
            lines.append(f"- **{load(s).title}**, {x}: {summary(cells[(s, old, x)])} -> {summary(cells[(s, new, x)])}.")
        if not changed:
            lines.append("No cell changed.")
    lines += ["", "## How each run ended", "",
              "A pass on the count does not mean the run finished. A run that stops in doubt waits for a person or "
              "for the provider to answer.", ""]
    for s in subjects:
        for v in variants:
            for x in scen:
                c = cells.get((s, v, x))
                if not c:
                    continue
                if c["status"] == "n/a":
                    lines.append(f"- {load(s).title} / {label(doc, v)} / {x}: N/A. {c['reason']}")
                    continue
                how = Counter(r["ended"] for r in c["repeats"])
                bad = [p for r in c["repeats"] for p in r["invalid"]] + c.get("invalid_because", [])
                text = ", ".join(f"{e} ({n}/{len(c['repeats'])})" for e, n in how.items())
                lines.append(f"- {load(s).title} / {label(doc, v)} / {x}: {summary(c)}; ended: {text}"
                             + (f"; INVALID: {bad[0]}" if bad else ""))
    lines += ["", "## What the subjects do", ""]
    for s in subjects:
        o = load(s)
        lines.append(f"- **{o.title}** (bench/subjects/{SUBJECTS_AND_CONTROLS[s].split(':')[0].rsplit('.', 1)[1]}.py). "
                     f"Default: {o.default_config}. With call4agents: {o.kit_config}.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def controls_gate(cells: List[Dict[str, Any]]) -> None:
    """A subject whose control fails cannot pass anything that control stands for: an approval path that never
    runs a call (C2) would 'pass' S4, S6 and S8 for free; a run that never acts (C1) would pass nothing real."""
    status = {(c["subject"], c["variant"], c["scenario"]): c["status"] for c in cells}
    for c in cells:
        need = "C2" if c["scenario"] in APPROVAL - {"C2"} else ("C1" if c["scenario"] in CRASH | {"S2"} else None)
        if need and c["status"] == "pass" and status.get((c["subject"], c["variant"], need), "pass") != "pass":
            c["status"] = "invalid"
            c["invalid_because"] = [f"control {need} did not pass for this subject and variant"]


def parse(argv: List[str]) -> Dict[str, Any]:
    opts: Dict[str, Any] = {"subjects": list(SUBJECTS), "scenarios": list(SCENARIOS), "repeat": 5, "workers": 4,
                            "out": str(ROOT / "bench" / "results"), "markdown": True, "require": False, "keep": False,
                            "release": "0.7.1,0.7.2", "head": True}
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
        elif a == "--release":
            opts["release"] = next(items)
        elif a == "--no-release":
            opts["release"] = None
        elif a == "--no-head":
            opts["head"] = False
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
    kits: Dict[str, Dict[str, Any]] = {}
    for release in (opts["release"] or "").split(","):     # oldest first, e.g. "0.7.1,0.7.2"
        if release.strip():
            kits[f"call4agents-{release.strip()}"] = release_kit(release.strip(), base)
    if opts["head"]:
        kits["call4agents-head"] = head_kit()
    variants = ["default"] + list(kits)
    for k, info in kits.items():
        print(f"variant {k}: call4agents {info['version']} from {info['source']}")
    jobs = [(s, v, x) for s in subjects for v in variants for x in opts["scenarios"]]
    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(opts["workers"]) as pool:
        futures = [pool.submit(run_cell, s, v, x, opts["repeat"] if x in CRASH else 1, base, opts["keep"],
                               kits[v]["path"] if v in kits else None) for s, v, x in jobs]
        cells = []
        for f in concurrent.futures.as_completed(futures):
            c = f.result()
            cells.append(c)
            print(f"  {c['subject']:14} {c['variant']:18} {c['scenario']:3} {summary(c)}", flush=True)
    if not opts["keep"]:
        shutil.rmtree(base, ignore_errors=True)
    order = {k: i for i, k in enumerate(jobs)}
    cells.sort(key=lambda c: order[(c["subject"], c["variant"], c["scenario"])])
    controls_gate(cells)
    date = datetime.date.today().isoformat()
    doc = {"format": "c4a-bench-1", "date": date, "seconds": round(time.time() - started, 1), "repeat": opts["repeat"],
           "subjects": subjects, "scenarios": opts["scenarios"], "variants": variants,
           "environment": environment(subjects, kits), "cells": cells}
    out = Path(opts["out"])
    out.mkdir(parents=True, exist_ok=True)
    name = f"{date}-{platform.system().lower()}.json"
    (out / name).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"\nwrote {out / name} ({doc['seconds']} s)")
    if opts["markdown"]:
        md = ROOT / "bench" / "RESULTS.md" if out.resolve() == (ROOT / "bench" / "results").resolve() else out / "RESULTS.md"
        write_markdown(doc, md, name)
        print(f"wrote {md}")
    invalid = [c for c in cells if c["status"] == "invalid"]
    for c in invalid:
        why = (c["repeats"][0]["invalid"] if c["repeats"] else []) + c.get("invalid_because", [])
        print(f"INVALID {c['subject']} {c['variant']} {c['scenario']}: {why}")
    return 1 if invalid else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

