# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""call4agents crashtest <script> - experimental. Kills your agent at every step, restarts it, and says what repeated.

Your script is run in a new empty folder, once cleanly, then once per crash point: it is killed (os._exit) just
before and just after each action, and just before each write it saves to SQLite or to a file in that folder. Then
it is started again the same way you would restart it. It does not need our guard: actions are found by reading the
script, and seen by a profile hook, so the code of your agent is not changed.

The script must be able to restart: a fixed run or thread id, and saved state in the current folder. On a restart
the environment has CRASHTEST_RESUME=1, so the script can also act out a changed call. It runs on your machine with
your environment: point it at test accounts, because every action really runs, many times.
"""
import concurrent.futures
import dis
import inspect
import json
import os
import re
import runpy
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from collections import Counter
from pathlib import Path

from call4agents.scan import actions_in

KILLED = 137
HIDDEN_ARGS = {"self", "cls", "ctx", "context", "config", "runtime", "run_context", "tool_context", "state"}
ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")


def _shown(frame) -> str:
    code = frame.f_code
    names = code.co_varnames[:code.co_argcount + code.co_kwonlyargcount]
    values = frame.f_locals
    return ", ".join(f"{n}={ADDRESS.sub('', repr(values.get(n)))[:80]}" for n in names if n not in HIDDEN_ARGS)


class Child:
    """Inside the process under test: counts crash points, keeps the journal, dies at the chosen point."""

    def __init__(self, journal, crash_at, watched, script):
        self.fd = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        self.crash_at, self.watched = crash_at, watched
        self.count = 0
        self.lock = threading.Lock()
        self.user_code = {}
        self.open_async = set()
        self.kit = str(Path(__file__).resolve().parent)
        self.cwd = os.path.realpath(os.getcwd())
        self.script = script

    def log(self, **entry):
        os.write(self.fd, (json.dumps(entry) + "\n").encode())

    def point(self, label):
        with self.lock:
            self.count += 1
            n = self.count
        self.log(point=n, at=label)
        if n == self.crash_at:
            os._exit(KILLED)

    def is_user(self, code) -> bool:
        known = self.user_code.get(code)
        if known is None:
            name = code.co_filename.replace("\\", "/")
            known = code.co_name in self.watched and (code.co_filename == self.script or (
                not code.co_filename.startswith(self.kit) and "/site-packages/" not in name and "/lib/python" not in name.lower()))
            self.user_code[code] = known
        return known

    def profile(self, frame, event, arg):
        if event != "call" and event != "return":
            return
        code = frame.f_code
        if not self.is_user(code):
            return
        is_async = bool(code.co_flags & (inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR))
        if event == "call":
            if is_async:
                if id(frame) in self.open_async:
                    return                            # a coroutine waking up, not a new call
                self.open_async.add(id(frame))
            shown = f"{code.co_name}({_shown(frame)})"
            self.point(f"before {shown}")
            self.log(start=code.co_name, call=shown)
        else:
            if is_async:
                if not dis.opname[code.co_code[frame.f_lasti]].startswith("RETURN"):
                    return                            # a coroutine going to sleep
                self.open_async.discard(id(frame))
            shown = f"{code.co_name}({_shown(frame)})"
            self.log(done=code.co_name, call=shown)
            self.point(f"after {shown}")

    def watch_sqlite(self):
        real = sqlite3.connect

        def connect(*args, **kwargs):
            conn = real(*args, **kwargs)

            def trace(statement):
                word = statement.lstrip()[:8].upper()
                if word.startswith("COMMIT") or (not conn.in_transaction and word.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))):
                    self.point("before a saved write")
            if str(args[0] if args else kwargs.get("database")) != ":memory:":
                conn.set_trace_callback(trace)
            return conn
        sqlite3.connect = sqlite3.dbapi2.connect = connect

    def audit(self, event, args):
        if event == "open" and len(args) == 3 and isinstance(args[0], (str, os.PathLike)):
            flags = args[2] if isinstance(args[2], int) else 0
            if flags & (os.O_WRONLY | os.O_RDWR) and str(args[0]) and os.path.realpath(args[0]).startswith(self.cwd) \
                    and not str(args[0]).endswith((".sqlite", ".sqlite-wal", ".sqlite-shm", ".db", ".journal")):
                self.point("before a saved write")


def child(journal, crash_at, watched, script, args):
    sys.argv = [script, *args]
    sys.path.insert(0, str(Path(script).resolve().parent))
    c = Child(journal, int(crash_at), set(filter(None, watched.split(","))), script)
    c.watch_sqlite()
    sys.addaudithook(c.audit)
    threading.setprofile(c.profile)
    sys.setprofile(c.profile)
    try:
        runpy.run_path(script, run_name="__main__")
    finally:
        sys.setprofile(None)
    sys.stdout.flush()


def start(script, work, journal, crash_at, watched, args, resume, timeout):
    command = [sys.executable, "-m", "call4agents.crashtest", "--child", str(journal), str(crash_at), ",".join(sorted(watched)),
               str(Path(script).resolve()), *args]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "CRASHTEST_RESUME": "1" if resume else "",
           "PYTHONPATH": os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[1]), os.environ.get("PYTHONPATH")]))}
    return subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=timeout)


def read_journal(path) -> list:
    p = Path(path)
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []


def calls(entries) -> Counter:
    return Counter(e["call"] for e in entries if "start" in e)


def one_crash(script, point, watched, args, timeout, baseline) -> dict:
    work = tempfile.mkdtemp(prefix="c4a-crashtest-")
    try:
        first, second = Path(work) / ".first", Path(work) / ".second"
        killed = start(script, work, first, point, watched, args, False, timeout)
        resumed = start(script, work, second, 0, watched, args, True, timeout)
        a, b = read_journal(first), read_journal(second)
        at = next((e["at"] for e in a if e.get("point") == point), "?")
        ran = calls(a) + calls(b)
        done = Counter(e["call"] for e in a + b if "done" in e)
        in_doubt = "EffectInDoubt" in resumed.stderr
        return {"point": point, "at": at, "reached": killed.returncode == KILLED,
                "twice": sorted(c for c in ran if c in baseline and ran[c] > baseline[c]),
                "unapproved": sorted(c for c in ran if c not in baseline),
                "lost": [] if in_doubt else sorted(c for c in baseline if not done[c]),
                "stopped": resumed.returncode != 0, "in_doubt": in_doubt,
                "error": resumed.stderr.strip().splitlines()[-1][:160] if resumed.returncode else ""}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def choose_points(total, limit) -> list:
    if total <= limit:
        return list(range(1, total + 1))
    return sorted({1 + round(i * (total - 1) / (limit - 1)) for i in range(limit)})


def fix_hint(source: str) -> str:
    if re.search(r"^\s*(from|import)\s+langgraph", source, re.M):
        return "from call4agents.adapters import langgraph as c4a_lg\n" "@c4a_lg.effect                  # put it on the action"
    if re.search(r"^\s*(from|import)\s+pydantic_ai", source, re.M):
        return "from call4agents.adapters import pydantic_ai as c4a_pai\n" "@c4a_pai.effect                 # under @agent.tool, on the action"
    if re.search(r"^\s*from\s+agents\b", source, re.M):
        return "from call4agents.adapters import openai_agents as c4a_oa\n" "@c4a_oa.effect                  # under @function_tool, inside c4a.run(your_run_id)"
    return "import call4agents as c4a\n" "@c4a.effect                     # put it on the action, inside c4a.run(your_run_id)"


def crashtest(script, watch=(), args=(), max_crashes=200, timeout=120, workers=None) -> dict:
    source = Path(script).read_text(encoding="utf-8", errors="replace")
    watched = actions_in(source) | set(watch)
    work = tempfile.mkdtemp(prefix="c4a-crashtest-")
    try:
        journal = Path(work) / ".clean"
        clean = start(script, work, journal, 0, watched, args, False, timeout)
        if clean.returncode != 0:
            return {"error": "the script failed on a clean run", "stderr": clean.stderr[-1500:]}
        entries = read_journal(journal)
        again = Path(work) / ".again"
        start(script, work, again, 0, watched, args, True, timeout)
        repeated_clean = sorted(calls(read_journal(again)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    baseline = calls(entries)
    total = sum(1 for e in entries if "point" in e)
    if not baseline:
        return {"error": "no action was seen. Name one: --watch your_function", "watched": sorted(watched)}
    points = choose_points(total, max_crashes)
    with concurrent.futures.ThreadPoolExecutor(workers or min(4, os.cpu_count() or 1)) as pool:
        results = list(pool.map(lambda p: one_crash(script, p, watched, args, timeout, baseline), points))
    return {"script": str(script), "actions": dict(baseline), "crash_points": total, "tested": len(points),
            "reached": sum(r["reached"] for r in results), "repeats_on_clean_restart": repeated_clean,
            "results": results, "fix": fix_hint(source)}


def problems(report) -> dict:
    out = {"twice": {}, "unapproved": {}, "lost": {}}
    for r in report["results"]:
        for kind in out:
            for call in r[kind]:
                out[kind].setdefault(call, []).append(r["at"])
    return out


def demo() -> int:
    """The same small agent loop, without the guard and then with it. Returns 0 when both come out as expected."""
    example = str(Path(__file__).resolve().parent / "examples" / "crashtest_agent.py")
    print("A small agent refunds a customer. Without the guard:\n")
    os.environ["AGENT_GUARD"] = "0"
    without = main([example])
    print("\nThe same agent with the one line added:\n")
    os.environ["AGENT_GUARD"] = "1"
    guarded = main([example])
    os.environ.pop("AGENT_GUARD")
    return 0 if (without, guarded) == (1, 0) else 1


def main(argv) -> int:
    as_json = "--json" in argv
    rest, watch, script_args, max_crashes = [], [], [], 200
    if "--" in argv:
        script_args = argv[argv.index("--") + 1:]
        argv = argv[:argv.index("--")]
    items = iter(a for a in argv if a != "--json")
    for a in items:
        if a == "--watch":
            watch += next(items, "").split(",")
        elif a == "--max-crashes":
            max_crashes = int(next(items, "200"))
        else:
            rest.append(a)
    if rest == ["--demo"]:
        return demo()
    if len(rest) != 1 or not Path(rest[0]).is_file():
        print("usage: call4agents crashtest --demo | <agent script> [--watch name,name] [--max-crashes N] [--json] [-- script args]")
        return 2
    report = crashtest(rest[0], watch, script_args, max_crashes)
    if as_json:
        print(json.dumps(report, indent=1))
    if "error" in report:
        if not as_json:
            print(f"crashtest stopped: {report['error']}\n{report.get('stderr', '')}")
        return 2
    found = problems(report)
    bad = bool(found["twice"] or found["unapproved"])
    if as_json:
        return 1 if bad else 0
    print(f"call4agents crashtest (experimental): {Path(rest[0]).name}")
    print(f"Clean run: {sum(report['actions'].values())} action(s), {report['crash_points']} crash points. "
          f"Killed and restarted {report['reached']} time(s).")
    for call, where in found["twice"].items():
        print(f"\n  RAN TWICE       {call}\n    in {len(where)} of {report['tested']} crashes, first when killed {where[0]}")
    for call, where in found["unapproved"].items():
        print(f"\n  NOT APPROVED    {call}\n    the clean run never made this call. It ran in {len(where)} of {report['tested']} "
              f"crashes, first when killed {where[0]}")
    for call, where in found["lost"].items():
        print(f"\n  NEVER FINISHED  {call}\n    in {len(where)} of {report['tested']} crashes, first when killed {where[0]}")
    doubt = sum(r["in_doubt"] for r in report["results"])
    other = [r for r in report["results"] if r["stopped"] and not r["in_doubt"]]
    if doubt:
        print(f"\n  {doubt} restart(s) stopped as in doubt instead of repeating: a person or the provider settles those.")
    if other:
        print(f"\n  {len(other)} restart(s) ended with an error, first: {other[0]['error']}")
    if report["repeats_on_clean_restart"] and bad:
        print("\n  Note: starting it again after a clean finish also repeats its actions. If this script is meant to start "
              "a new job each time, give it a fixed run id and saved state, or these results say little.")
    if bad:
        print(f"\nThe one-line fix:\n\n{report['fix']}\n\nThen run this test again.")
        return 1
    print("\nNo action ran twice and no unapproved call ran, at the points tested.")
    print("This covers this script, this path and these crash points. It is not a proof for other paths.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6:])
    else:
        sys.exit(main(sys.argv[1:]))
