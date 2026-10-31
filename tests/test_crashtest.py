"""call4agents crashtest and scan. Run: python -m tests.test_crashtest [--require-frameworks]

Offline, 0 LLM calls. Every scripted agent really is killed and restarted.
CT1 an action with no guard: the repeat is found, exit 1, and every crash point was really reached.
CT2 the same action with the guard: nothing repeats, exit 0.
CT3 an async action is counted once per call, and its repeat is found.
CT4 a call the clean run never made is reported as not approved.
CT5 the old approval bypass (a crash after a yes is spent, then other arguments under the same key) is refused.
CT6 a failing script and a script with no action stop with a clear message, exit 2. --watch adds an action.
CT7 only with --require-frameworks: each real framework, without and with the guard.
SC1-SC6 scan: finds real actions, leaves reads, guarded tools and non-tools alone, and says what it cannot see."""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

from call4agents import scan as sc
from call4agents.crashtest import crashtest, main, problems

FAILED = []
HERE = Path(__file__).resolve().parent

UNGUARDED = """
import json, os
def pay(amount, to):
    with open("bank.txt", "a") as f:
        f.write(f"{amount} {to}")
done = json.load(open("state.json")) if os.path.exists("state.json") else []
for step in ("plan", "pay", "reply"):
    if step in done:
        continue
    if step == "pay":
        pay(10, "alice")
    done.append(step)
    with open("state.json", "w") as f:
        json.dump(done, f)
"""
GUARDED = """
import json, os
import call4agents as c4a
c4a.init("rec.sqlite", secret=b"s" * 16)
@c4a.effect
def pay(amount, to):
    with open("bank.txt", "a") as f:
        f.write(f"{amount} {to}")
done = json.load(open("state.json")) if os.path.exists("state.json") else []
with c4a.run("job-1"):
    for step in ("plan", "pay", "reply"):
        if step in done:
            continue
        if step == "pay":
            pay(10, "alice")
        done.append(step)
        with open("state.json", "w") as f:
            json.dump(done, f)
"""
ASYNC = """
import asyncio, os
async def send_email(to):
    await asyncio.sleep(0.01)
    with open("out.txt", "a") as f:
        f.write(to)
    await asyncio.sleep(0.01)
async def go():
    if not os.path.exists("saved"):
        await send_email("bob")
        with open("saved", "w") as f:
            f.write("1")
asyncio.run(go())
"""
CHANGED = """
import os
def pay(amount):
    with open("bank.txt", "a") as f:
        f.write(str(amount))
if not os.path.exists("saved"):
    pay(20 if os.environ.get("CRASHTEST_RESUME") else 10)
with open("saved", "w") as f:
    f.write("1")
"""
BYPASS = """
import os
import call4agents as c4a
c4a.init("rec.sqlite", secret=b"s" * 16)
@c4a.effect(approval=True, name="m.pay")
def pay(amount):
    with open("bank.txt", "a") as f:
        f.write(str(amount))
with c4a.run("job-1"):
    resume = os.environ.get("CRASHTEST_RESUME")
    if not resume:
        c4a.approve(pay, amount=10)
    try:
        pay(20 if resume else 10, c4a_key="order-1")
    except (c4a.ApprovalRequired, c4a.KeyConflict, c4a.EffectInDoubt) as e:
        print(type(e).__name__)
"""


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {str(detail)[:300]}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def script(folder, name, text):
    path = Path(folder) / name
    path.write_text(text)
    return str(path)


def crash_checks(folder):
    bad = script(folder, "agent_bad.py", UNGUARDED)
    r = crashtest(bad)
    found = problems(r)
    call = "pay(amount=10, to='alice')"
    check("CT1 the repeat of an unguarded action is found", call in found["twice"], found)
    check("CT1 the loop around the action is not reported as an action", list(r["actions"]) == [call], r["actions"])
    check("CT1 every crash point was really reached", r["tested"] == r["crash_points"] == r["reached"] >= 4, r)
    check("CT1 killed before and after the action and before a saved write",
          {f"before {call}", f"after {call}", "before a saved write"} <= {x["at"] for x in r["results"]},
          [x["at"] for x in r["results"]])
    check("CT1 a kill just before the action is not a repeat",
          not next(x for x in r["results"] if x["at"] == f"before {call}")["twice"], r["results"])
    check("CT1 a kill just after the action is a repeat",
          next(x for x in r["results"] if x["at"] == f"after {call}")["twice"] == [call], r["results"])
    check("CT1 the command exits 1 and names the fix", main([bad]) == 1 and "c4a.effect" in r["fix"])

    good = script(folder, "agent_good.py", GUARDED)
    r = crashtest(good)
    found = problems(r)
    check("CT2 with the guard nothing repeats", not found["twice"] and not found["unapproved"], found)
    check("CT2 the guard's own record writes are crash points too", r["crash_points"] > 6, r["crash_points"])
    check("CT2 a crash inside stops as in doubt, not as a lost action",
          any(x["in_doubt"] for x in r["results"]) and not found["lost"], found)
    check("CT2 the command exits 0", main([good]) == 0)

    r = crashtest(script(folder, "agent_async.py", ASYNC))
    check("CT3 an async action is one call, not one per await", sum(r["actions"].values()) == 1, r["actions"])
    check("CT3 its repeat is found", "send_email(to='bob')" in problems(r)["twice"], problems(r))

    r = crashtest(script(folder, "agent_changed.py", CHANGED))
    check("CT4 a call the clean run never made is reported", "pay(amount=20)" in problems(r)["unapproved"], problems(r))
    r = crashtest(script(folder, "agent_bypass.py", BYPASS))
    check("CT5 the old approval bypass: pay(20) never runs at any crash point",
          "pay(amount=20)" not in problems(r)["unapproved"] and r["reached"] == r["tested"], problems(r))

    broken = script(folder, "agent_broken.py", "def pay(x):\n    pass\npay(1)\nraise ValueError('nope')\n")
    check("CT6 a failing script: exit 2, not a clean result", main([broken]) == 2)
    quiet = script(folder, "agent_quiet.py", "def add(a, b):\n    return a + b\nadd(1, 2)\n")
    check("CT6 no action seen: exit 2", "error" in crashtest(quiet) and main([quiet]) == 2)
    check("CT6 --watch adds an action", crashtest(quiet, watch=["add"]).get("actions") == {"add(a=1, b=2)": 1})
    check("CT6 --demo finds the repeat without the guard and none with it", main(["--demo"]) == 0)


def framework_checks():
    for label, module, file in (("LangGraph", "langgraph.checkpoint.sqlite", "lg_agent.py"),
                                ("Pydantic AI", "pydantic_ai", "pai_agent.py"),
                                ("OpenAI Agents SDK", "agents.models.interface", "oa_agent.py")):
        if importlib.util.find_spec(module.split(".")[0]) is None or importlib.util.find_spec(module) is None:
            check(f"CT7 {label} is installed", False)
            continue
        path = str(HERE / "crashtest_agents" / file)
        os.environ["AGENT_GUARD"] = "0"
        without = problems(crashtest(path))
        os.environ["AGENT_GUARD"] = "1"
        guarded = crashtest(path)
        os.environ.pop("AGENT_GUARD")
        check(f"CT7 {label}: the repeat is found without the guard", "send_email(to='bob')" in without["twice"], without)
        check(f"CT7 {label}: nothing repeats with the guard", "error" not in guarded and not problems(guarded)["twice"], guarded)


def scan_checks():
    def scanned(body):
        return sc.scan_source("import requests, os\n" + body, "x.py")

    hit = scanned("@function_tool\ndef pay_invoice(id):\n    return requests.post('https://x', json={'id': id})\n")
    check("SC1 a tool that POSTs, with no guard, is listed", [f["tool"] for f in hit] == ["pay_invoice"]
          and hit[0]["confidence"] == "code" and hit[0]["missing"] == ["approval", "once-only guard"], hit)
    check("SC1 a tools=[...] list counts as a tool",
          sc.scan_source("import os\ndef wipe(p):\n    os.remove(p)\nagent = Agent(tools=[wipe])\n", "x.py")[0]["tool"] == "wipe")
    check("SC1 a call through a helper in the same file is seen",
          scanned("def _do(u):\n    requests.delete(u)\n@function_tool\ndef clean(u):\n    _do(u)\n")[0]["does"]
          == ["sends an HTTP DELETE (in _do)"])
    check("SC1 a tool written as a class, through self.client, is seen", sc.scan_source(
        "class Refund(BaseTool):\n    def _run(self, id):\n        self.client.post('u')\n", "x.py")[0]["tool"] == "Refund")
    kit = "class Mail(Toolkit):\n    def __init__(self):\n        super().__init__(tools=[self.send])\n        self.log()\n"
    kit += "    def log(self):\n        requests.post('u')\n    def send(self, to):\n        requests.post('u')\n"
    check("SC1 a method a toolkit hands over is a tool, and one it only calls is not",
          [f["tool"] for f in sc.scan_source(kit, "x.py")] == ["send"], sc.scan_source(kit, "x.py"))
    check("SC1 a SQL write is seen",
          scanned("@function_tool\ndef note(db, t):\n    db.execute('INSERT INTO n VALUES (1)')\n")[0]["does"] == ["writes to a database"])
    check("SC2 a guarded tool with approval is left alone",
          not scanned("@function_tool\n" "@c4a.effect(approval=True)\ndef pay(a):\n    requests.post('https://x')\n"))
    only_once = scanned("@function_tool\n" "@c4a.effect\ndef pay(a):\n    requests.post('https://x')\n")
    check("SC2 once-only alone still asks for approval", only_once[0]["missing"] == ["approval"], only_once)
    keyed = scanned("@function_tool\ndef pay(a):\n    requests.post('https://x', headers={'Idempotency-Key': a})\n")
    check("SC2 an idempotency key counts as once-only", keyed[0]["missing"] == ["approval"], keyed)
    check("SC3 the loop around an action is not an action",
          sc.actions_in("def pay(a):\n    pass\ndef send_all():\n    pay(1)\n") == {"pay"})
    check("SC3 a message to another agent is left alone",
          not scanned("@function_tool\ndef ask_agent(q):\n    return a2a.send_message(q)\n"))
    check("SC3 a search that POSTs its query is left alone",
          not scanned("@function_tool\ndef search_web(q):\n    return requests.post('u', json=q)\n"))
    check("SC3 a read is left alone", not scanned("@function_tool\ndef get_weather(c):\n    return requests.get('https://x')\n"))
    check("SC3 a read named like an action is left alone", not scanned("@function_tool\ndef list_payment(c):\n    return 1\n"))
    check("SC3 a function not given to a model is left alone", not scanned("def pay(a):\n    requests.post('https://x')\n"))
    named = scanned("@function_tool\ndef send_invoice(a):\n    return backend(a)\n")
    check("SC4 a name alone is marked as weak", named[0]["confidence"] == "name only", named)
    elsewhere = sc.scan_source("import requests\n@tool\ndef pay(a):\n    requests.post('u')\ng = b.compile(interrupt_before=['t'])\n", "x.py")
    check("SC5 approval set up elsewhere in the file is unknown, not missing", elsewhere[0]["missing"][0].startswith("unknown"), elsewhere)
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "bad.py").write_text("@tool\ndef pay(a):\n    requests.post('u')\n")
        (Path(d) / "broken.py").write_text("def (\n")
        (Path(d) / "tests").mkdir()
        (Path(d) / "tests" / "t.py").write_text("@tool\ndef pay(a):\n    requests.post('u')\n")
        r = sc.scan(d)
        check("SC6 a folder: bad syntax is skipped, tests are left out", len(r["findings"]) == 1 and r["files"] == 2, r)
        check("SC6 exits 1 on a finding and prints its limits", sc.main([d]) == 1 and "cannot see" in sc.LIMITS)


def main_test():
    scan_checks()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        crash_checks(folder)
    if "--require-frameworks" in sys.argv:
        framework_checks()
    if FAILED:
        print(f"{len(FAILED)} crashtest check(s) failed")
        return 1
    print("all crashtest and scan checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main_test())
