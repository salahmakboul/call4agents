"""The pytest plugin (experimental): the c4a_crashtest fixture on the bundled demo agent. Offline, no real effects.

Run: python -m tests.test_pytest_plugin [--require-pytest]
Without pytest installed the checks are skipped, unless --require-pytest is given (CI gives it).
"""
import os
import subprocess
import sys
import tempfile
from importlib.metadata import entry_points
from pathlib import Path

import call4agents

FAILED = []
AGENT = Path(call4agents.__file__).resolve().parent / "examples" / "crashtest_agent.py"
ENTRY_POINT = any(ep.name == "call4agents" and ep.value == "call4agents.adapters.pytest_plugin"
                  for ep in entry_points(group="pytest11"))


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def run_pytest(body, guard):
    with tempfile.TemporaryDirectory(prefix="c4a-pytest-") as d:
        (Path(d) / "test_agent.py").write_text(body.replace("AGENT", repr(str(AGENT))), encoding="utf-8")
        # the kit under test, also when it is not installed (run from a checkout)
        kit_root = str(Path(call4agents.__file__).resolve().parent.parent)
        env = dict(os.environ, AGENT_GUARD=guard,
                   PYTHONPATH=os.pathsep.join([kit_root] + [p for p in [os.environ.get("PYTHONPATH")] if p]))
        # Installed, pytest loads the plugin through its entry point (that is what users get); loading it
        # again with -p would register it twice. From a checkout, load it by name.
        load = [] if ENTRY_POINT else ["-p", "call4agents.adapters.pytest_plugin"]
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *load, "--rootdir", d, d],
                           cwd=d, env=env, capture_output=True, text=True, timeout=600)
        return r.returncode, r.stdout + r.stderr


def main():
    try:
        import pytest  # noqa: F401
    except ImportError:
        if "--require-pytest" in sys.argv:
            print("  FAIL  pytest is not installed")
            return 1
        print("  skip  PT1-PT3 (pytest not installed)")
        return 0

    print(f"  info  plugin loaded through {'the installed pytest11 entry point' if ENTRY_POINT else '-p (not installed)'}")
    code, out = run_pytest("def test_it(c4a_crashtest):\n    c4a_crashtest(AGENT)\n", "0")
    check("PT1 without test_accounts=True the fixture refuses and runs nothing",
          code == 1 and "pass test_accounts=True" in out and "crash(es) tested" not in out, out[-800:])

    code, out = run_pytest("def test_it(c4a_crashtest):\n    c4a_crashtest(AGENT, test_accounts=True)\n", "0")
    check("PT2 the unguarded agent fails the test and names the action that ran twice",
          code == 1 and "RAN TWICE" in out and "refund(" in out and "c4a.effect" in out, out[-800:])

    code, out = run_pytest("def test_it(c4a_crashtest):\n    r = c4a_crashtest(AGENT, test_accounts=True)\n"
                           "    assert r['tested'] > 0 and r['actions']\n", "1")
    check("PT3 the guarded agent passes, after real crashes", code == 0 and "1 passed" in out, out[-800:])

    print(f"\n{len(FAILED)} pytest plugin check(s) failed" if FAILED else "\nall pytest plugin checks pass")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
