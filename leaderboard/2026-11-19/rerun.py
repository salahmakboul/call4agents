"""2026-11-19 rerun of the finding, newest versions (the public copy: the frameworks on the leaderboard page). One JSON file per run.

    <venv python> rerun.py <framework> <kind> <n>      # kind: crash | control | edited | <extra case name>

Every run is a fresh temp folder and separate OS processes per phase (see the scripts in scripts/).
Fake / stub models only: no API keys, no network except 127.0.0.1.
"""
import datetime as dt
import json
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent
S = HERE / "scripts"
PY = sys.executable

THREE = {"crash": "approve_then_crash", "control": "no_crash", "edited": "edited_after_yes"}
FW = {
    "langgraph": dict(pkgs=["langgraph", "langgraph-checkpoint-sqlite", "langgraph-checkpoint"],
                      cmd=lambda c: [PY, str(S / "three_frameworks.py"), "one", "LangGraph", THREE[c]]),
    "pydantic_ai": dict(pkgs=["pydantic-ai", "pydantic-ai-slim"],
                        cmd=lambda c: [PY, str(S / "three_frameworks.py"), "one", "Pydantic AI", THREE[c]]),
    "openai_agents": dict(pkgs=["openai-agents", "openai"],
                          cmd=lambda c: [PY, str(S / "three_frameworks.py"), "one", "OpenAI Agents SDK", THREE[c]]),
    "google_adk": dict(pkgs=["google-adk", "google-genai"],
                       cmd=lambda c: [PY, str(S / "adk_confirm_crash.py"), "--one",
                                      {"crash": "crash after approved payment", "control": "control: no crash",
                                       "edited": "edited"}[c]]),
    "microsoft_agent_framework": dict(
        pkgs=["agent-framework", "agent-framework-core"],
        cmd=lambda c: [PY, str(S / "maf_approve_crash.py"), "--one",
                       {"crash": "crash after approved payment", "control": "control: no crash",
                        "edited": "crash, then edited after the yes",
                        "edited_before_yes": "edited before the yes",
                        "crash_back1": "crash, resume from the checkpoint before the yes"}[c]]),
}


def count(fw, res):
    p = res.get("payments")
    return len(p) if isinstance(p, list) else p


def main():
    fw, kind, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    spec = FW[fw]
    out = HERE / fw
    out.mkdir(exist_ok=True)
    versions = {}
    for p in spec["pkgs"]:
        try:
            versions[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            versions[p] = None
    start_i = 1 + len(list(out.glob(f"{kind}_*.json")))
    for i in range(start_i, start_i + n):
        cmd = spec["cmd"](kind)
        t0 = time.time()
        started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=str(S))
        try:
            res = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            res = {"error": "no JSON from driver", "stdout": p.stdout[-800:], "stderr": p.stderr[-1500:]}
        rec = {"framework": fw, "versions": versions, "python": sys.version.split()[0], "date": dt.date.today().isoformat(),
               "started_utc": started, "seconds": round(time.time() - t0, 1),
               "kind": kind, "crash": kind in ("crash", "crash_back1") or (fw == "microsoft_agent_framework" and kind == "edited"),
               "command": " ".join(f'"{c}"' if " " in c else c for c in cmd), "exit": p.returncode,
               "payments": count(fw, res) if "error" not in res or "payments" in res else None,
               "result": res}
        (out / f"{kind}_{i:02d}.json").write_text(json.dumps(rec, indent=1))
        print(f"{fw} {kind} {i}: payments={rec['payments']} ({rec['seconds']}s)", flush=True)


if __name__ == "__main__":
    main()
