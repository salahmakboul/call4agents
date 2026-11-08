"""Google ADK: tool confirmation + a crash right after the approved tool's side effect.

Fake model (no keys, no network). One tool, pay(amount, to), with require_confirmation=True; its side effect is
one line appended to payments.txt. The app is resumable (ResumabilityConfig) and sessions live in SQLite
(SqliteSessionService), so they survive the process. Every phase is a separate OS process:

  1. start    the model asks for pay(10, "alice"); ADK pauses with an adk_request_confirmation call
  2. approve  a person says yes (function_response with confirmed=True and the invocation id, as the
              confirmation docs say for resumable apps); the tool pays, then the process dies with
              os._exit right after the payment, before ADK stores the tool's result
  3. resume   a new process resumes the documented way: runner.run_async(user_id, session_id, invocation_id)
              If ADK asks for confirmation again, the script says yes again and records that it asked.

Case "edited": between 1 and 2, the stored call is changed to pay(9999, "mallory") in the session database
(both the model's call and the confirmation's copy of it), which needs write access to the session store.
No crash in that case: it counts what the yes pays.

    python adk_confirm_crash.py            # 5 runs per case, prints JSON
"""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

APP, USER, SESSION = "payapp", "u1", "s1"


def build(work: Path):
    from google.adk.agents import LlmAgent
    from google.adk.apps import App
    from google.adk.apps._configs import ResumabilityConfig
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import Runner
    from google.adk.sessions.sqlite_session_service import SqliteSessionService
    from google.adk.tools import FunctionTool
    from google.genai import types

    class FakeLlm(BaseLlm):
        model: str = "fake-pay"

        async def generate_content_async(self, llm_request, stream=False):
            paid = any(p.function_response and p.function_response.name == "pay"
                       and "receipt" in (p.function_response.response or {})
                       for c in llm_request.contents for p in (c.parts or []))
            if paid:
                part = types.Part(text="Paid.")
            else:
                part = types.Part(function_call=types.FunctionCall(name="pay", args={"amount": 10, "to": "alice"}))
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    def pay(amount: int, to: str) -> dict:
        """Pay an amount to a person."""
        with open(work / "payments.txt", "a") as f:
            f.write(f"{amount} {to}\n")
            f.flush()
            os.fsync(f.fileno())
        if os.environ.get("KILL_AFTER_PAY") == "1":
            os._exit(17)                       # crash after the side effect, before ADK stores the result
        return {"receipt": f"paid {amount} to {to}"}

    agent = LlmAgent(name="payer", model=FakeLlm(), instruction="Pay when asked.",
                     tools=[FunctionTool(pay, require_confirmation=True)])
    app = App(name=APP, root_agent=agent, resumability_config=ResumabilityConfig(is_resumable=True))
    svc = SqliteSessionService(db_path=str(work / "sessions.db"))
    return Runner(app=app, session_service=svc), svc, types


def confirmations(events):
    out = []
    for e in events:
        for p in (e.content.parts if e.content else None) or []:
            if p.function_call and p.function_call.name == "adk_request_confirmation":
                out.append({"id": p.function_call.id, "invocation_id": e.invocation_id})
    return out


async def collect(agen):
    return [e async for e in agen]


async def phase(work: Path, name: str) -> dict:
    runner, svc, types = build(work)
    st = work / "state.json"
    if name == "start":
        await svc.create_session(app_name=APP, user_id=USER, session_id=SESSION)
        ev = await collect(runner.run_async(user_id=USER, session_id=SESSION, new_message=types.Content(
            role="user", parts=[types.Part(text="Pay alice 10.")])))
        c = confirmations(ev)
        st.write_text(json.dumps(c[-1]))
        return {"asked": len(c)}

    def yes(c):
        return types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
            id=c["id"], name="adk_request_confirmation", response={"confirmed": True}))])

    c = json.loads(st.read_text())
    if name == "approve":
        ev = await collect(runner.run_async(user_id=USER, session_id=SESSION, invocation_id=c["invocation_id"],
                                            new_message=yes(c)))
        return {"asked_again": len(confirmations(ev)), "texts": texts(ev)}
    if name == "resume":
        ev = await collect(runner.run_async(user_id=USER, session_id=SESSION, invocation_id=c["invocation_id"]))
        again = confirmations(ev)
        out = {"asked_again": len(again), "texts": texts(ev)}
        if again:                              # a person may say yes again, not knowing the first one paid
            ev2 = await collect(runner.run_async(user_id=USER, session_id=SESSION,
                                                 invocation_id=again[-1]["invocation_id"], new_message=yes(again[-1])))
            out["texts"] += texts(ev2)
        return out
    raise SystemExit(f"unknown phase {name}")


def texts(events):
    return [p.text for e in events for p in ((e.content.parts if e.content else None) or []) if p.text]


def edit_store(work: Path) -> int:
    """Change the stored pay(10, alice) to pay(9999, mallory) wherever the session database holds it."""
    con = sqlite3.connect(work / "sessions.db")
    n = 0
    for table, col in [(t, c) for t in [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                       for c in [r[1] for r in con.execute(f"PRAGMA table_info('{t}')")]]:
        rows = con.execute(f"SELECT rowid, \"{col}\" FROM \"{table}\"").fetchall()
        for rowid, val in rows:
            if isinstance(val, str) and '"alice"' in val and '"amount"' in val:
                new = val.replace('"amount": 10', '"amount": 9999').replace('"amount":10', '"amount":9999')
                new = new.replace('"alice"', '"mallory"')
                if new != val:
                    con.execute(f"UPDATE \"{table}\" SET \"{col}\" = ? WHERE rowid = ?", (new, rowid))
                    n += 1
                    print(f"edited {table}.{col}: " + ("adk_request_confirmation" if "adk_request_confirmation" in val
                          else "model function_call"), file=sys.stderr)
    con.commit()
    con.close()
    return n


def run(work, name, kill=False):
    env = dict(os.environ, KILL_AFTER_PAY="1" if kill else "0")
    p = subprocess.run([sys.executable, __file__, "--phase", str(work), name], capture_output=True, text=True,
                       env=env, timeout=120)
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        out = {"error": (p.stderr or p.stdout)[-400:]}
    out["exit"] = p.returncode
    return out


def payments(work):
    f = Path(work) / "payments.txt"
    return f.read_text().splitlines() if f.exists() else []


def one(case):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        w = Path(d)
        r = {"start": run(w, "start")}
        if case == "control: no crash":
            r["approve"] = run(w, "approve")
        elif case == "edited":
            r["rows_edited"] = edit_store(w)
            r["approve"] = run(w, "approve")
        else:
            r["approve"] = run(w, "approve", kill=True)
            r["resume"] = run(w, "resume")
        r["payments"] = payments(w)
        return r


if __name__ == "__main__":
    if sys.argv[1:2] == ["--phase"]:
        print(json.dumps(asyncio.run(phase(Path(sys.argv[2]), sys.argv[3]))))
        sys.exit(0)
    n = int(os.environ.get("N", "5"))
    import google.adk
    res = {"adk": google.adk.__version__, "python": sys.version.split()[0], "runs_per_case": n, "cases": {}}
    for case in ("control: no crash", "crash after approved payment", "edited"):
        runs = [one(case) for _ in range(n)]
        res["cases"][case] = {"payments": [r["payments"] for r in runs], "runs": runs}
    print(json.dumps(res, indent=1))
