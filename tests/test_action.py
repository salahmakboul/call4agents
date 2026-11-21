"""The GitHub Action (action.yml) run step by step on this machine. Run: python -m tests.test_action

Offline, 0 LLM calls, no pip install. The composite steps of action.yml are read from the file and their bash runs
the way GitHub runs a `shell: bash` step (bash --noprofile --norc -eo pipefail), with the inputs, the step `if:`,
GITHUB_OUTPUT and GITHUB_STEP_SUMMARY. `uses:` steps (setup-python) and the pip install step are not run: the
`call4agents` command is a small shim that records its arguments and runs this checkout. The `action` job in
.github/workflows/tests.yml runs the real Action on Linux, macOS and Windows; this file lets the bug re-insertion
checks (tests/mutations) change action.yml and see the result without GitHub.

AC1 a guarded agent: the job passes, exit-code 0, the summary says nothing repeated.
AC2 an unguarded agent: the job fails, exit-code 1, an error annotation, the summary names the repeated call and the fix.
AC3 test-accounts is not "true": the job fails before crashtest runs, and no exit-code is set.
AC4 a payment provider in live mode: the job fails with exit-code 2.
AC5 inputs reach the command: --test-accounts always, --max-crashes, --watch only when given.
AC6 every input the steps use is declared, and the default install pins the version in pyproject.toml."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "action.yml"
AGENT = "call4agents/examples/crashtest_agent.py"
FAILED = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {str(detail)[:400]}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def bash():
    """Git's bash on Windows (not the WSL launcher in System32), else bash on PATH."""
    if os.name == "nt":
        for p in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
                  Path(r"C:\Program Files\Git\bin\bash.exe")):
            if p.exists():
                return str(p)
    return shutil.which("bash")


def parse(text):
    """The parts of action.yml this test needs: input defaults and the composite steps.
    A small reader for this file's layout (2-space indents, `run: |` blocks), not a general YAML parser."""
    lines = text.splitlines()
    inputs, steps, section, step, key, block = {}, [], None, None, None, None
    for line in lines:
        if block is not None:
            if line.strip() == "" or line.startswith(" " * 8):
                block.append(line[8:])
                continue
            step["run"] = "\n".join(block).rstrip() + "\n"
            block = None
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            section = line.split(":")[0]
            continue
        if section == "inputs":
            if indent == 2:
                key = line.strip().rstrip(":")
                inputs[key] = None
            elif indent == 4 and line.strip().startswith("default:"):
                v = line.split(":", 1)[1].strip()
                inputs[key] = v[1:-1] if v[:1] in "\"'" else v
        elif section == "runs" and indent >= 4:
            s = line.strip()
            if indent == 4 and s.startswith("- "):
                step = {"env": {}, "with": {}}
                steps.append(step)
                s, indent = s[2:], 6
            if indent == 6:
                k, _, v = s.partition(":")
                v = v.strip()
                if k == "run" and v == "|":
                    block = []
                elif k in ("env", "with"):
                    key = k
                else:
                    step[k] = v
            elif indent == 8 and key in ("env", "with"):
                k, _, v = s.partition(":")
                step[key][k] = v.strip()
    if block is not None:
        step["run"] = "\n".join(block).rstrip() + "\n"
    return inputs, steps


def expand(value, inputs, outputs):
    def one(m):
        expr = m.group(1).strip()
        if expr.startswith("inputs."):
            return inputs.get(expr[7:], "")
        m2 = re.fullmatch(r"steps\.([\w-]+)\.outputs\.([\w-]+)", expr)
        if m2:
            return outputs.get(m2.group(1), {}).get(m2.group(2), "")
        raise ValueError(f"expression this test cannot read: {expr}")
    return re.sub(r"\$\{\{(.*?)\}\}", one, value)


def condition(expr, inputs):
    if not expr:
        return True
    m = re.fullmatch(r"inputs\.([\w-]+)\s*(==|!=)\s*'([^']*)'", expr.strip())
    if not m:
        raise ValueError(f"if: this test cannot read: {expr}")
    same = inputs.get(m.group(1), "") == m.group(3)
    return same if m.group(2) == "==" else not same


def run_action(given, extra_env=None):
    """Run the composite steps. Returns dict(ok, outputs, summary, log, args, ran)."""
    defaults, steps = parse(ACTION.read_text(encoding="utf-8"))
    inputs = {k: ("" if v is None else v) for k, v in defaults.items()}
    inputs.update(given)
    work = Path(tempfile.mkdtemp(prefix="c4a-action-"))
    shim_dir, args_file = work / "bin", work / "args.txt"
    shim_dir.mkdir()
    shim = shim_dir / "call4agents"
    shim.write_text("#!/usr/bin/env bash\n"
                    f"printf '%s\\n' \"$@\" > '{args_file.as_posix()}'\n"
                    f"exec '{Path(sys.executable).as_posix()}' -m call4agents \"$@\"\n", encoding="utf-8")
    shim.chmod(0o755)
    env = dict(os.environ)
    for k in [k for k in env if k.startswith(("STRIPE_", "PAYPAL_", "ADYEN_", "SQUARE_"))]:
        env.pop(k)
    env.update(extra_env or {})
    env["PATH"] = str(shim_dir) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(ROOT)
    env["RUNNER_TEMP"] = str(work)
    summary = work / "summary.md"
    summary.write_text("", encoding="utf-8")
    env["GITHUB_STEP_SUMMARY"] = str(summary)
    outputs, log, ran, ok = {}, "", [], True
    try:
        for i, st in enumerate(steps):
            if "uses" in st:
                ran.append(f"uses {st['uses']}")
                continue
            if not condition(st.get("if", ""), inputs):
                continue
            run = st.get("run", "")
            if re.search(r"\bpip install\b", run):
                ran.append("pip install (not run here)")
                continue
            out_file = work / f"out{i}.txt"
            out_file.write_text("", encoding="utf-8")
            step_env = dict(env, GITHUB_OUTPUT=str(out_file))
            step_env.update({k: expand(v, inputs, outputs) for k, v in st["env"].items()})
            script = work / f"step{i}.sh"
            script.write_text(expand(run, inputs, outputs), encoding="utf-8", newline="\n")
            r = subprocess.run([bash(), "--noprofile", "--norc", "-eo", "pipefail", str(script)], cwd=ROOT,
                               env=step_env, capture_output=True, text=True, timeout=600)
            log += r.stdout + r.stderr
            ran.append(st.get("name", f"step {i}"))
            if st.get("id"):
                outputs[st["id"]] = dict(l.split("=", 1) for l in out_file.read_text(encoding="utf-8").splitlines() if "=" in l)
            if r.returncode != 0:
                ok = False
                break
        return dict(ok=ok, outputs=outputs, summary=summary.read_text(encoding="utf-8"), log=log, ran=ran,
                    args=args_file.read_text(encoding="utf-8").splitlines() if args_file.exists() else None)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def exit_code(r):
    return r["outputs"].get("crashtest", {}).get("exit-code")


def main():
    if not bash():
        print("FAIL  no bash found: the Action's steps are bash")
        return 1
    base = {"script": AGENT, "test-accounts": "true"}

    r = run_action(base, {"AGENT_GUARD": "1"})
    check("AC1 a guarded agent passes", r["ok"], r["log"][-600:])
    check("AC1 exit-code is 0", exit_code(r) == "0", r["outputs"])
    check("AC1 the summary says nothing repeated", "No action ran twice" in r["summary"], r["summary"][-400:])

    r = run_action(base, {"AGENT_GUARD": "0"})
    check("AC2 an unguarded agent fails the job", not r["ok"], r["log"][-600:])
    check("AC2 exit-code is 1", exit_code(r) == "1", r["outputs"])
    check("AC2 an error annotation is printed", "::error title=call4agents crashtest::" in r["log"], r["log"][-600:])
    check("AC2 the summary names the repeated call and the fix",
          "RAN TWICE" in r["summary"] and "refund" in r["summary"] and "@c4a.effect" in r["summary"], r["summary"][-600:])

    r = run_action(dict(base, **{"test-accounts": "no"}), {"AGENT_GUARD": "1"})
    check("AC3 without test-accounts the job fails", not r["ok"], r["log"][-400:])
    check("AC3 crashtest never ran", r["args"] is None and "crashtest" not in r["outputs"], r["ran"])
    check("AC3 the refusal says why", "test accounts" in r["log"], r["log"][-400:])

    r = run_action(base, {"AGENT_GUARD": "1", "PAYPAL_MODE": "live"})
    check("AC4 a provider in live mode fails the job", not r["ok"], r["log"][-400:])
    check("AC4 exit-code is 2", exit_code(r) == "2", r["outputs"])

    r = run_action(dict(base, **{"max-crashes": "7", "watch": "refund,notify"}), {"AGENT_GUARD": "1"})
    a = r["args"] or []
    check("AC5 --test-accounts is always passed", "--test-accounts" in a, a)
    check("AC5 --max-crashes is passed", "--max-crashes" in a and a[a.index("--max-crashes") + 1] == "7", a)
    check("AC5 --watch is passed when given", "--watch" in a and a[a.index("--watch") + 1] == "refund,notify", a)
    check("AC5 the command is crashtest on the script", a[:2] == ["crashtest", AGENT], a)
    r = run_action(base, {"AGENT_GUARD": "1"})
    check("AC5 no --watch when none is given", "--watch" not in (r["args"] or []), r["args"])

    text = ACTION.read_text(encoding="utf-8")
    declared, steps = parse(text)
    used = set(re.findall(r"inputs\.([\w-]+)", text))
    check("AC6 every input used is declared", used <= set(declared), used - set(declared))
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    check("AC6 the default install pins the version in pyproject.toml",
          declared.get("install") == f"call4agents=={version}", (declared.get("install"), version))
    if FAILED:
        print(f"{len(FAILED)} action check(s) failed")
        return 1
    print("all action checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
