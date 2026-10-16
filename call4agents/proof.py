# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""c4a prove (module call4agents.proof) - show that a guard (a test, an assertion, a check) actually catches its bug.

For each mutation: rewrite ONE module in memory in a fresh process (your files are never
changed), run your check, and expect it to FAIL. Every batch also runs a no-op control that
must PASS - if the control fails, the whole batch is void.

    python -m call4agents prove prove.json
prove.json:
    {"check": "tests.test_tools:test_send",            # module:function (raises on failure), or a module
     "mutations": [{"module": "mytools", "old": "if done:", "new": "if False:"}]}
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

PRELUDE = r'''
import importlib, linecache, runpy, sys
sys.path.insert(0, {cwd!r})
MOD, OLD, NEW, CHECK = {mod!r}, {old!r}, {new!r}, {check!r}
m = importlib.import_module(MOD)
src = open(m.__file__, encoding="utf-8").read()
if OLD not in src:
    print("C4A_RESULT=TARGET_NOT_FOUND"); raise SystemExit(0)
src = src.replace(OLD, NEW)
linecache.cache[m.__file__] = (len(src), None, src.splitlines(True), m.__file__)
exec(compile(src, m.__file__, "exec"), m.__dict__)
try:
    if ":" in CHECK:
        mod_name, fn = CHECK.split(":")
        result = getattr(importlib.import_module(mod_name), fn)()
        ok = result is not False
    else:
        try:
            runpy.run_module(CHECK, run_name="__main__")
            ok = True
        except SystemExit as e:
            ok = e.code in (0, None)
except BaseException:
    ok = False
print("C4A_RESULT=" + ("PASSES" if ok else "FAILS"))
'''


def run_one(module: str, old: str, new: str, check: str, cwd: str = ".") -> str:
    code = PRELUDE.format(cwd=str(Path(cwd).resolve()), mod=module, old=old, new=new, check=check)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=cwd).stdout
    marks = [l for l in out.splitlines() if l.startswith("C4A_RESULT=")]
    return marks[0].split("=", 1)[1] if marks else "NO_RESULT"


def prove(check: str, mutations: List[Dict[str, str]], cwd: str = ".") -> Dict[str, Any]:
    first = mutations[0]["module"] if mutations else ""
    control = run_one(first, "", "", check, cwd) if first else "NO_MUTATIONS"
    rows = []
    for m in mutations:
        r = run_one(m["module"], m["old"], m["new"], check, cwd)
        rows.append({**m, "result": {"FAILS": "caught", "PASSES": "MISSED"}.get(r, r)})
    valid = control == "PASSES"
    return {"control": control, "valid": valid, "mutations": rows,
            "proven": valid and bool(rows) and all(r["result"] == "caught" for r in rows)}


def main(spec_path: str) -> int:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    r = prove(spec["check"], spec["mutations"], spec.get("cwd", "."))
    print(f"control (no change): {r['control']}" + ("" if r["valid"] else "  -> batch VOID: the check fails unmutated"))
    for m in r["mutations"]:
        print(f"  {m['result']:16} {m['module']}: {m['old'][:40]!r} -> {m['new'][:40]!r}")
    print("PROVEN: every mutation is caught" if r["proven"] else "NOT PROVEN")
    return 0 if r["proven"] else 1
