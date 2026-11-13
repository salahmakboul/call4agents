# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Run the TLC model checker on every config of formal/Call4Agents.tla and check each result.

    python formal/run_tlc.py --tla2tools path/to/tla2tools.jar [--java path/to/java] [--only NAME ...]
    python formal/run_tlc.py --write-cfgs        # regenerate formal/cfg/*.cfg from the table below
    python formal/run_tlc.py --selftest          # check the output parser on canned TLC outputs (no Java)

Three kinds of config:
  pass     the code as it is on main: TLC must finish with no error.
  seeded   a real mistake put back on purpose (BUGS = {...}): TLC MUST report a violation of `expect`.
           A seeded bug that passes means the property cannot see that mistake: the run fails.
  finding  a real problem of the current code that the model shows: TLC must report `expect`. When the code
           is fixed, the finding config starts to pass and the run fails, so it cannot be forgotten.
Exit code 0 only if every config behaves as expected and every .cfg file matches the table.
Standard library only.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG_DIR = HERE / "cfg"
SPEC = HERE / "Call4Agents.tla"

BASE = {
    "NW": 2, "ARG3": '"b"', "MAX_CALLS": 2, "MAX_CALLS3": 1, "MAX_YES": 1, "MAX_NO": 1,
    "APPROVAL": "TRUE", "MODE": '"plain"', "RETRY_ON": "TRUE", "CRASHES": "TRUE", "EFFECT_FAILS": "TRUE",
    "OWNER": '"processes"', "START_EARLY": "FALSE", "BUGS": "{}",
}
SAFETY = ["TypeOK", "AtMostOnce", "NoUnapprovedEffect", "OneYesOneAction", "DoneIsTrue"]


def cfg(name, kind, consts=None, inv=None, props=None, spec="Spec", expect=None, note=""):
    return {"name": name, "kind": kind, "consts": dict(BASE, **(consts or {})), "inv": inv or [],
            "props": props or [], "spec": spec, "expect": expect, "note": note}


def bugs(*names):
    return "{" + ", ".join(f'"{n}"' for n in names) + "}"


CONFIGS = [
    # ---- the code as it is (main, after Fix 17): must PASS
    cfg("plain_approval", "pass", {"NW": 3}, SAFETY + ["DoneOnce"], ["AppendOnly"],
        note="approval, retry_on, errors, crashes; workers 1+2 same call, worker 3 other arguments"),
    cfg("plain_second_action", "pass", {"NW": 3, "ARG3": '"a"', "MAX_YES": 2, "MAX_NO": 0}, SAFETY + ["DoneOnce"], ["AppendOnly"],
        note="worker 3 is a second named action with the SAME arguments: it needs its own yes"),
    cfg("plain_no_approval", "pass", {"NW": 3, "APPROVAL": "FALSE", "MAX_CALLS3": 2}, SAFETY + ["DoneOnce"],
        ["AppendOnly"], note="plain claim, no approval"),
    cfg("reconcile_processes", "pass", {"MODE": '"reconcile"', "MAX_CALLS": 3}, SAFETY, ["AppendOnly"],
        note="reconcile= (truthful), each worker its own process; owner check of Fix 17"),
    cfg("reconcile_three", "pass", {"NW": 3, "MODE": '"reconcile"', "ARG3": '"a"'}, SAFETY,
        ["AppendOnly"], note="reconcile= with three workers, worker 3 a second action with the same arguments"),
    cfg("idem_processes", "pass", {"MODE": '"idem"', "MAX_CALLS": 3}, SAFETY + ["DoneOnce"], ["AppendOnly"],
        note="idempotent_for=, a provider that deduplicates the key"),
    cfg("reconcile_threads_start_early", "pass",
        {"MODE": '"reconcile"', "OWNER": '"threads"', "START_EARLY": "TRUE", "MAX_CALLS": 3}, SAFETY, ["AppendOnly"],
        note="PROPOSED change, not main: mark the call in progress before its claim; closes finding_threads_window"),
    cfg("live_calls_end", "pass", {"MODE": '"reconcile"', "APPROVAL": "FALSE"}, ["TypeOK"], ["AllCallsEnd"],
        spec="LiveSpec", note="progress: with fair workers every call ends (crashes and errors allowed)"),
    cfg("live_admitted_finishes", "pass", {"CRASHES": "FALSE", "EFFECT_FAILS": "FALSE"}, ["TypeOK"],
        ["AdmittedFinishes"], spec="LiveSpec", note="progress: no crash, a tool that returns: an admitted action gets its result"),
    # ---- findings in the current code: must FAIL with `expect`
    cfg("finding_threads_window", "finding", {"MODE": '"reconcile"', "OWNER": '"threads"', "APPROVAL": "FALSE"},
        ["AtMostOnce"], expect="AtMostOnce",
        note="threads of one process: the gap between the claim and _owner.started() lets a second thread reconcile"),
    cfg("finding_done_twice", "finding", {"MODE": '"reconcile"', "APPROVAL": "FALSE"}, ["DoneOnce"],
        expect="DoneOnce", note="two callers reconcile the same crashed attempt and both record the result"),
    # ---- seeded bugs: TLC MUST find a counterexample for `expect`
    cfg("bug_split_admission", "seeded", {"BUGS": bugs("split_admission")}, ["NoUnapprovedEffect"],
        expect="NoUnapprovedEffect"),
    cfg("bug_no_spend", "seeded", {"NW": 3, "ARG3": '"a"', "BUGS": bugs("no_spend")}, ["OneYesOneAction"],
        expect="OneYesOneAction"),
    cfg("bug_no_deny_in_doubt", "seeded", {"BUGS": bugs("no_deny_in_doubt")}, ["NoUnapprovedEffect"],
        expect="NoUnapprovedEffect"),
    cfg("bug_reconcile_lies_none", "seeded", {"MODE": '"reconcile"', "APPROVAL": "FALSE",
                                              "BUGS": bugs("reconcile_lies_none")}, ["AtMostOnce"], expect="AtMostOnce"),
    cfg("bug_reconcile_lies_found", "seeded", {"MODE": '"reconcile"', "APPROVAL": "FALSE",
                                               "BUGS": bugs("reconcile_lies_found")}, ["DoneIsTrue"], expect="DoneIsTrue"),
    cfg("bug_retry_on_lies", "seeded", {"APPROVAL": "FALSE", "BUGS": bugs("retry_on_lies")}, ["AtMostOnce"],
        expect="AtMostOnce"),
    cfg("bug_provider_forgets", "seeded", {"MODE": '"idem"', "APPROVAL": "FALSE", "BUGS": bugs("provider_forgets")},
        ["AtMostOnce"], expect="AtMostOnce"),
    cfg("bug_no_unique_intent", "seeded", {"APPROVAL": "FALSE", "BUGS": bugs("no_unique_intent")}, ["AtMostOnce"],
        expect="AtMostOnce"),
    cfg("bug_delete_on_abandon", "seeded", {"APPROVAL": "FALSE", "BUGS": bugs("delete_on_abandon")}, [],
        ["AppendOnly"], expect="AppendOnly"),
    cfg("bug_no_timeout", "seeded", {"MODE": '"reconcile"', "APPROVAL": "FALSE", "BUGS": bugs("no_timeout")},
        ["TypeOK"], ["AllCallsEnd"], spec="LiveSpec", expect="temporal"),
    cfg("bug_reconcile_live_owner", "seeded", {"MODE": '"reconcile"', "APPROVAL": "FALSE",
                                               "BUGS": bugs("reconcile_live_owner")}, ["AtMostOnce"], expect="AtMostOnce",
        note="0.7.1 behaviour, part 1: reconcile while the owner still runs the call"),
    cfg("bug_give_up_twice", "seeded", {"MODE": '"reconcile"', "APPROVAL": "FALSE", "BUGS": bugs("give_up_twice")},
        ["AtMostOnce"], expect="AtMostOnce",
        note="0.7.1 behaviour, part 2: an attempt closed twice, the next attempt by counting rows"),
    cfg("bug_v071", "seeded", {"MODE": '"reconcile"', "APPROVAL": "FALSE",
                               "BUGS": bugs("reconcile_live_owner", "give_up_twice")}, ["AtMostOnce"],
        expect="AtMostOnce", note="0.7.1 as released (both parts)"),
]


def cfg_text(c):
    lines = ["\\* generated by formal/run_tlc.py --write-cfgs; edit the table there, not this file",
             f"\\* {c['kind']}: {c['note'] or c['name']}" + (f" (expect a violation of {c['expect']})" if c["expect"] else ""),
             f"SPECIFICATION {c['spec']}", "CONSTANTS"]
    lines += [f"    {k} = {v}" for k, v in c["consts"].items()]
    if c["inv"]:
        lines.append("INVARIANT " + " ".join(c["inv"]))
    if c["props"]:
        lines.append("PROPERTY " + " ".join(c["props"]))
    return "\n".join(lines) + "\n"


def parse(out):
    """What TLC said: {'result': 'pass'|'violation'|'error', 'violated': name|None, 'distinct': int|None,
    'depth': int|None, 'trace': int|None}."""
    r = {"result": "error", "violated": None, "distinct": None, "depth": None, "trace": None}
    found = re.findall(r"([\d,]+) distinct states found", out)     # the LAST one: earlier ones are progress lines
    if found:
        r["distinct"] = int(found[-1].replace(",", ""))
    m = re.search(r"depth of the complete state graph search is (\d+)", out)
    if m:
        r["depth"] = int(m.group(1))
    states = re.findall(r"^State (\d+):", out, re.M)
    if states:
        r["trace"] = max(int(s) for s in states)
    m = re.search(r"Error: Invariant (\w+) is violated", out) or re.search(r"Error: Action property (\w+) is violated", out)
    if m:
        r["result"], r["violated"] = "violation", m.group(1)
    elif "Error: Temporal properties were violated" in out:
        r["result"], r["violated"] = "violation", "temporal"
    elif "Model checking completed. No error has been found." in out and "Error:" not in out:
        r["result"] = "pass"
    return r


def verdict(c, r):
    if c["kind"] == "pass":
        return r["result"] == "pass"
    return r["result"] == "violation" and r["violated"] == c["expect"]


def run_one(c, java, jar, workers, logs=None):
    with tempfile.TemporaryDirectory(prefix="c4a-tlc-") as tmp:
        t = Path(tmp)
        shutil.copy(SPEC, t / SPEC.name)
        (t / "MC.cfg").write_text(cfg_text(c), encoding="utf-8")
        cmd = [java, "-XX:+UseParallelGC", "-cp", str(jar), "tlc2.TLC", "-config", "MC.cfg", "-workers", str(workers),
               "-deadlock", "-metadir", str(t / "states"), SPEC.name]
        start = time.monotonic()
        p = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=1800)
        took = time.monotonic() - start
    if logs:
        Path(logs).mkdir(parents=True, exist_ok=True)
        (Path(logs) / f"{c['name']}.log").write_text(p.stdout + p.stderr, encoding="utf-8")
    r = parse(p.stdout + p.stderr)
    r["seconds"] = round(took, 1)
    r["exit"] = p.returncode
    r["tail"] = (p.stdout + p.stderr)[-3000:]
    return r


SELFTEST = [
    ("Model checking completed. No error has been found.\n53941 distinct states found, 0 states left on queue.\n"
     "The depth of the complete state graph search is 33.", "pass", None),
    ("Error: Invariant AtMostOnce is violated.\nState 1: <Initial predicate>\nState 2: <Start>\n"
     "State 14: <Effect>\n120 distinct states found", "violation", "AtMostOnce"),
    ("Error: Action property AppendOnly is violated.\nState 1:\nState 9:", "violation", "AppendOnly"),
    ("Error: Temporal properties were violated.\nState 1:\nState 3: Stuttering", "violation", "temporal"),
    ("Semantic errors:\nError: Unknown operator Foo", "error", None),
    ("Model checking completed. No error has been found.\nError: something odd", "error", None),
]


def selftest():
    bad = 0
    for text, result, violated in SELFTEST:
        r = parse(text)
        ok = r["result"] == result and r["violated"] == violated
        print(f"  {'ok  ' if ok else 'FAIL'}  parse -> {r['result']} {r['violated']}")
        bad += not ok
    progress = ("Progress(3) at 2026-11-12: 900 states generated, 706 distinct states found, 12 left on queue.\n"
                "Model checking completed. No error has been found.\n1,900 states generated, 1,706 distinct states "
                "found, 0 states left on queue.\nThe depth of the complete state graph search is 9.")
    r = parse(progress)
    ok = r["distinct"] == 1706 and r["depth"] == 9
    print(f"  {'ok  ' if ok else 'FAIL'}  the final state count is read, not a progress line ({r['distinct']})")
    bad += not ok
    c_pass, c_seed = CONFIGS[0], next(c for c in CONFIGS if c["kind"] == "seeded")
    checks = [(verdict(c_pass, {"result": "pass", "violated": None}), True, "a passing correct config is accepted"),
              (verdict(c_pass, {"result": "violation", "violated": "AtMostOnce"}), False, "a failing correct config is refused"),
              (verdict(c_seed, {"result": "pass", "violated": None}), False, "a seeded bug that passes is refused"),
              (verdict(c_seed, {"result": "violation", "violated": "Other"}), False, "the wrong invariant is refused"),
              (verdict(c_seed, {"result": "violation", "violated": c_seed["expect"]}), True, "the expected violation is accepted")]
    for got, want, what in checks:
        ok = got == want
        print(f"  {'ok  ' if ok else 'FAIL'}  {what}")
        bad += not ok
    print("selftest passed" if not bad else f"selftest: {bad} failure(s)")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tla2tools", default=os.environ.get("TLA2TOOLS", "tla2tools.jar"))
    ap.add_argument("--java", default=os.environ.get("JAVA", "java"))
    ap.add_argument("--workers", default="auto")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--json", help="write the results here")
    ap.add_argument("--logs", help="keep the full TLC output of each config in this folder")
    ap.add_argument("--write-cfgs", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.write_cfgs:
        CFG_DIR.mkdir(exist_ok=True)
        for c in CONFIGS:
            (CFG_DIR / f"{c['name']}.cfg").write_bytes(cfg_text(c).encode("utf-8"))
        print(f"wrote {len(CONFIGS)} cfg files to {CFG_DIR}")
        return 0
    bad = 0
    names = {c["name"] for c in CONFIGS}
    on_disk = {p.stem for p in CFG_DIR.glob("*.cfg")}
    for c in CONFIGS:          # the committed .cfg files must be exactly what the table says
        f = CFG_DIR / f"{c['name']}.cfg"
        if not f.exists() or f.read_text(encoding="utf-8") != cfg_text(c):
            print(f"  FAIL  {c['name']}.cfg is missing or differs from the table (run --write-cfgs)")
            bad += 1
    for extra in sorted(on_disk - names):
        print(f"  FAIL  cfg/{extra}.cfg is not in the table")
        bad += 1
    if a.only and set(a.only) - names:
        print(f"unknown config(s): {sorted(set(a.only) - names)}")
        return 2
    # TLC runs inside a temporary folder, so a relative jar path would not be found there (CI, 13 Nov: every
    # config ended in "ClassNotFoundException: tlc2.TLC").
    a.tla2tools = str(Path(a.tla2tools).resolve())
    if not Path(a.tla2tools).is_file():
        print(f"tla2tools.jar not found at {a.tla2tools}")
        return 2
    results = []
    for c in CONFIGS:
        if a.only and c["name"] not in a.only:
            continue
        r = run_one(c, a.java, a.tla2tools, a.workers, a.logs)
        ok = verdict(c, r)
        bad += not ok
        what = "no error" if r["result"] == "pass" else (f"{r['violated']} violated, trace {r['trace']} states"
                                                          if r["result"] == "violation" else "TLC ERROR")
        print(f"  {'ok  ' if ok else 'FAIL'}  {c['kind']:8} {c['name']:32} {what:42} "
              f"distinct={r['distinct']} depth={r['depth']} {r['seconds']}s", flush=True)
        if not ok:
            print(r["tail"])
        results.append({"name": c["name"], "kind": c["kind"], "expect": c["expect"], "ok": ok,
                        **{k: r[k] for k in ("result", "violated", "distinct", "depth", "trace", "seconds")}})
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\n{len(results)} config(s) run" + ("; all as expected" if not bad else f"; {bad} NOT as expected"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
