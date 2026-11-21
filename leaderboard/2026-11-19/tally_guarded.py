"""Count payments in the GUARDED runs from the saved run files only. python tally_guarded.py > tally_guarded.txt

Payments come from each run's fake bank file (payments.txt lines, saved in the run JSON by the driver).
How the guard ended comes from guard_log.jsonl (saved in the run JSON as guard_calls):
  in doubt         the resumed call got EffectInDoubt, the inner pay did not run
  recorded result  the guard returned its recorded result, the inner pay did not run
  ran              the inner pay ran in that call (the bank file got a line; controls)
A crash run's first, paying call never logs (the process dies inside it); the bank file shows it.
"""
import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def n_payments(d):
    r = d["result"]
    p = r.get("paid", r.get("payments"))
    if isinstance(p, list):
        return len(p), tuple(str(x if isinstance(x, str) else f"{x['amount']} {x['to']}") for x in p)
    return p, ()


def ended(calls):
    if not calls:
        return "tool not called again"
    s = set(calls)
    if s == {"ran"}:
        return "ran"
    if s == {"EffectInDoubt"}:
        return "in doubt"
    if s == {"recorded_result"}:
        return "recorded result"
    return "+".join(calls)


all_ok = True
for fw_dir in sorted(p for p in HERE.iterdir() if p.is_dir() and p.name.endswith("_guarded")):
    files = sorted(fw_dir.glob("*_[0-9][0-9].json"))
    if not files:
        continue
    by = collections.defaultdict(list)
    for f in files:
        d = json.loads(f.read_text())
        r = d["result"]
        pays, what = n_payments(d)
        c4a = (r.get("call4agents") or {})
        by[d["kind"]].append(dict(pays=pays, what=what, ended=ended(r.get("guard_calls")), versions=d["versions"],
                                  c4a_ver=c4a.get("version"), c4a_from_venv="site-packages" in (c4a.get("file") or ""),
                                  exit=d["exit"], file=f.name, error=r.get("error")))
    first = by[next(iter(by))][0]["versions"]
    print(f"== {fw_dir.name}  versions={first}")
    for kind, rows in by.items():
        c = collections.Counter(r["pays"] for r in rows)
        e = collections.Counter(r["ended"] for r in rows)
        whats = collections.Counter(r["what"] for r in rows)
        vers = collections.Counter((r["c4a_ver"], r["c4a_from_venv"]) for r in rows)
        bad = [r["file"] for r in rows if r["exit"] != 0 or r["error"]]
        not_one = [r["file"] for r in rows if r["pays"] != 1]
        all_ok &= not not_one and not bad
        print(f"  {kind:12s} n={len(rows):2d} payments_per_run={dict(sorted(c.items(), key=str))} guard_ended={dict(e)}"
              f" paid={dict(whats)} call4agents(version, from venv)={dict(vers)}"
              + (f" NOT_1_PAYMENT={not_one}" if not_one else "") + (f" driver_problem={bad}" if bad else ""))
print(f"\nevery guarded run (crash and control) made exactly 1 payment: {all_ok}")
