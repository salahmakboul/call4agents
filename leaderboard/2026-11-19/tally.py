"""Count payments per framework and case from the saved run files only. python tally.py > tally.txt"""
import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
for fw_dir in sorted(p for p in HERE.iterdir() if p.is_dir() and not p.name.endswith("_guarded") and list(p.glob("*_[0-9][0-9].json"))):
    by = collections.defaultdict(list)
    for f in sorted(fw_dir.glob("*_[0-9][0-9].json")):
        d = json.loads(f.read_text())
        r = d["result"]
        extra = ""
        if fw_dir.name == "langgraph" and "asked_again" in r:
            extra = "asked" if r["asked_again"] else ""
        if fw_dir.name == "google_adk" and "resume" in r:
            extra = "asked" if r["resume"].get("asked_again") else ""
        if fw_dir.name == "microsoft_agent_framework" and "resume" in r:
            extra = "asked" if r["resume"].get("asked") else ""
        if fw_dir.name in ("langgraph", "pydantic_ai", "openai_agents"):
            what = [f"{p['amount']} {p['to']}" for p in r.get("paid", [])]
            raised = [v for k, v in r.items() if k.startswith("raised_in_")]
        else:
            what, raised = r.get("payments", []), []
        by[d["kind"]].append((d["payments"], extra, tuple(what), tuple(raised), d["versions"], d["exit"]))
    print(f"== {fw_dir.name}  versions={by[next(iter(by))][0][4]}")
    for kind, rows in by.items():
        c = collections.Counter(r[0] for r in rows)
        asked = sum(1 for r in rows if r[1] == "asked")
        whats = collections.Counter(r[2] for r in rows)
        raised = collections.Counter(r[3] for r in rows if r[3])
        bad = sum(1 for r in rows if r[5] != 0)
        print(f"  {kind:18s} n={len(rows):2d} payments_per_run={dict(sorted(c.items()))} asked_again={asked}"
              f" paid={dict(whats)}" + (f" raised={dict(raised)}" if raised else "") + (f" driver_exit!=0: {bad}" if bad else ""))
