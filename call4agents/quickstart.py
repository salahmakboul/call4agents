# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""python -m call4agents quickstart - see it work in under a minute, offline, no keys.

1. The plain-Python demo (always): crash after an email, resume; a changed payment after a "yes".
2. For every framework that is installed (LangGraph, Pydantic AI, OpenAI Agents SDK): the same
   crash-and-resume inside the real framework with a fake model - without the kit, then with it.
"""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from call4agents.examples.crash_resume import FRAMEWORKS, MODULES

LABELS = {"langgraph": "LangGraph", "pydantic_ai": "Pydantic AI", "openai_agents": "OpenAI Agents SDK"}


_KIT_DIR = None


def kit_dir() -> Path:
    """A clean folder holding ONLY the call4agents package. Running from a repository checkout put the
    repo root on sys.path, and the repo's own `agents/` package hid the OpenAI Agents SDK (also `agents`)."""
    global _KIT_DIR
    if _KIT_DIR is None:
        _KIT_DIR = Path(tempfile.mkdtemp(prefix="c4a-kit-"))
        atexit.register(shutil.rmtree, _KIT_DIR, True)
        shutil.copytree(Path(__file__).resolve().parent, _KIT_DIR / "call4agents",
                        ignore=shutil.ignore_patterns("__pycache__"))
    return _KIT_DIR


def _clean_env() -> dict:
    return {**os.environ, "PYTHONPATH": str(kit_dir())}


def installed(name: str) -> bool:
    probe = "; ".join([
        "import importlib.util as u, sys",
        f"m = {MODULES[name]!r}",
        "ok = (lambda: (u.find_spec(m.split('.')[0]) is not None) and (u.find_spec(m) is not None))",
        "r = False",
        "exec('try:' + chr(10) + ' r = ok()' + chr(10) + 'except ModuleNotFoundError:' + chr(10) + ' r = False')",
        "sys.exit(0 if r else 1)",
    ])
    return subprocess.run([sys.executable, "-c", probe], env=_clean_env(), cwd=kit_dir(),
                          capture_output=True).returncode == 0


def emails(framework: str, use_kit: bool) -> int:
    work = Path(tempfile.mkdtemp(prefix=f"c4a-{framework}-"))
    env = _clean_env()
    base = [sys.executable, "-m", "call4agents.examples.crash_resume", framework, str(work), "1" if use_kit else "0"]
    subprocess.run(base + ["crash"], env={**env, "C4A_EXAMPLE_CRASH": "1"}, cwd=kit_dir(), capture_output=True,
                   timeout=120)
    resumed = subprocess.run(base + ["resume"], env=env, cwd=kit_dir(), capture_output=True, text=True, timeout=120)
    if resumed.returncode:
        raise RuntimeError(resumed.stderr.strip().splitlines()[-1] if resumed.stderr.strip() else "resume failed")
    out = work / "outbox.txt"
    n = len(out.read_text().splitlines()) if out.exists() else 0
    shutil.rmtree(work, ignore_errors=True)             # leave no temp folders behind
    return n


def main() -> int:
    started = time.time()
    from call4agents.__main__ import demo, main as _unused  # noqa: F401
    print("call4agents quickstart (offline, fake models, no keys)\n")
    r = demo()
    print("Plain Python - crash right after an email is sent, then resume:")
    for label in list(r)[:3]:
        extra = " (refused to resend: in doubt)" if r[label]["in_doubt"] else ""
        print(f"  {label:45} emails sent: {r[label]['emails']}{extra}")
    a = r["approval"]
    print(f'  "yes" to pay(10, alice), call changed to pay(9999, mallory): {a["changed_payment"].split()[0]}; '
          f'approved call: {a["approved_payment"].split()[0]}; payments: {a["payments"]}')
    found = [f for f in FRAMEWORKS if installed(f)]
    print("\nFrameworks found:", ", ".join(LABELS[f] for f in found) or "none (pip install langgraph langgraph-checkpoint-sqlite / pydantic-ai / openai-agents)")
    from call4agents.__main__ import approval_ok
    ok = all(r[k]["emails"] == v for k, v in zip(list(r)[:3], (2, 1, 1))) and approval_ok(a)
    for f in found:
        try:
            without, with_kit = emails(f, False), emails(f, True)
            print(f"  {LABELS[f]:18} crash after the email, resume:  without call4agents {without} emails,  with call4agents {with_kit}")
            ok = ok and with_kit == 1
        except Exception as exc:  # report, never hide
            print(f"  {LABELS[f]:18} could not run: {str(exc)[:120]}")
            ok = False
    print(f"\n{'OK' if ok else 'SOMETHING DID NOT MATCH'} in {time.time() - started:.1f} s.  Next: python -m call4agents selftest")
    return 0 if ok else 1
