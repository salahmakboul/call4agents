"""2026-11-19 GUARDED rerun: the same cases as rerun.py, with call4agents (PyPI 0.7.3) on the tool. One JSON file per run.

    <venv python> rerun_guarded.py <framework> <kind> <n>      # kind: crash | control | crash_back1 (MAF only)

Scripts: scripts_guarded/ (copies of scripts/ plus the guard, see scripts_guarded/c4a_guard.py and
scripts_guarded/DIFF.txt). Output: <framework>_guarded/<kind>_NN.json. Every run is a fresh temp folder and separate
OS processes per phase. Fake / stub models only: no API keys, no network except 127.0.0.1.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent
S = HERE / "scripts_guarded"
PY = sys.executable

THREE = {"crash": "approve_then_crash", "control": "no_crash"}
FW = {
    "langgraph": dict(pkgs=["langgraph", "langgraph-checkpoint-sqlite", "langgraph-checkpoint"],
                      cmd=lambda c: [PY, str(S / "three_frameworks.py"), "one_guarded", "LangGraph", THREE[c]]),
    "pydantic_ai": dict(pkgs=["pydantic-ai", "pydantic-ai-slim"],
                        cmd=lambda c: [PY, str(S / "three_frameworks.py"), "one_guarded", "Pydantic AI", THREE[c]]),
    "openai_agents": dict(pkgs=["openai-agents", "openai"],
                          cmd=lambda c: [PY, str(S / "three_frameworks.py"), "one_guarded", "OpenAI Agents SDK", THREE[c]]),
    "google_adk": dict(pkgs=["google-adk", "google-genai"],
                       cmd=lambda c: [PY, str(S / "adk_confirm_crash.py"), "--one",
                                      {"crash": "crash after approved payment", "control": "control: no crash"}[c]]),
    "microsoft_agent_framework": dict(
        pkgs=["agent-framework", "agent-framework-core"],
        cmd=lambda c: [PY, str(S / "maf_approve_crash.py"), "--one",
                       {"crash": "crash after approved payment", "control": "control: no crash",
                        "crash_back1": "crash, resume from the checkpoint before the yes"}[c]]),
}


def count(res):
    p = res.get("payments")
    return len(p) if isinstance(p, list) else p


def main():
    fw, kind, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    spec = FW[fw]
    out = HERE / f"{fw}_guarded"
    out.mkdir(exist_ok=True)
    versions = {}
    for p in spec["pkgs"] + ["call4agents"]:
        try:
            versions[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            versions[p] = None
    env = dict(os.environ, C4A_GUARDED="1")
    env.pop("C4A_KIT_ROOT", None)
    start_i = 1 + len(list(out.glob(f"{kind}_*.json")))
    for i in range(start_i, start_i + n):
        cmd = spec["cmd"](kind)
        t0 = time.time()
        started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=str(S), env=env)
        try:
            res = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            res = {"error": "no JSON from driver", "stdout": p.stdout[-800:], "stderr": p.stderr[-1500:]}
        rec = {"framework": fw, "guarded": True, "versions": versions, "python": sys.version.split()[0],
               "date": dt.date.today().isoformat(), "started_utc": started, "seconds": round(time.time() - t0, 1),
               "kind": kind, "crash": kind in ("crash", "crash_back1"),
               "command": "C4A_GUARDED=1 " + " ".join(f'"{c}"' if " " in c else c for c in cmd), "exit": p.returncode,
               "payments": count(res) if "error" not in res or "payments" in res else None,
               "result": res}
        (out / f"{kind}_{i:02d}.json").write_text(json.dumps(rec, indent=1))
        print(f"{fw} guarded {kind} {i}: payments={rec['payments']} guard={res.get('guard_calls')} "
              f"({rec['seconds']}s)", flush=True)


if __name__ == "__main__":
    main()
