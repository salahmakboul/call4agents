# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""call4agents story - experimental. What happened in a run, in plain words, for a person who did not write the code.

    call4agents story <record> [--run <id>]

Reads the run record and prints, for each run, every action in time order: what it was, who approved it, what
the record shows (done, may or may not have happened, refused, waiting for a person) and what to do next.
Read-only: it changes nothing, needs no secret, and sends nothing.

What it cannot show: steps your code never started are not in the record, so they are not listed. An action
that may or may not have happened is never described as done. Arguments are printed, so treat the output as
sensitive, like the record itself.
"""
import json
import sys
import time
from typing import Any, Dict, List, Optional

from call4agents.ledger import LedgerError
from call4agents.ops import actions, open_record

NOT_SHOWN = ("The record lists only actions that were started. Steps your code never reached are not in it, "
             "so they are not listed here.")
SENSITIVE = "This page includes the arguments of each action. Treat it as sensitive, like the record itself."


def _when(t: Optional[float]) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(t)) if t else "an unknown time"


def _call(tool: Any, args: Any) -> str:
    short = str(tool or "?").rsplit(".", 1)[-1]
    text = json.dumps(args, default=str, ensure_ascii=False, sort_keys=True) if args is not None else ""
    if len(text) > 160:
        text = text[:157] + "..."
    return f"{short}({text})"


def _tool_of(key: str) -> str:
    return key.split("\x00", 1)[1] if "\x00" in key else key


def _action_lines(a: Dict[str, Any], yes: Optional[Dict[str, Any]], record: str) -> List[str]:
    what = _call(a["tool"], a["args"])
    lines = [f"- {_when(a['first_t'])}: {what} was started."]
    if yes is not None:
        by = (yes["payload"] or {}).get("by")
        lines.append(f"  A person approved this exact call{' (' + by + ')' if by else ''} at {_when(yes['t'])}.")
    state = a["state"]
    if a["resolved"] is not None:
        lines.append(f"  A person marked it {'done' if a['done'] else 'not done'} by hand. Their note: "
                     f"\"{a['resolved'] or 'no note'}\".")
    elif state.startswith("done"):
        lines.append("  Done: it ran and its result is saved. A retry returns this result and does not run it again.")
    elif state.startswith("not done"):
        lines.append("  Not done: it stopped with an error that shows nothing happened. The next try may run it again.")
    else:
        lines.append("  It may or may not have happened: it started, and the record has no result (for example the "
                     "program stopped or the call failed). Nothing will run it again on its own.")
        lines.append(f"  What to do: check in the outside system whether it happened, then record what you found:")
        lines.append(f"    call4agents resolve {record} {a['key'][:12]} done \"what you checked\"")
        lines.append(f"    call4agents resolve {record} {a['key'][:12]} not-done \"what you checked\"")
    if a["errors"]:
        lines.append(f"  Last error: {str(a['errors'][-1])[:200]}")
    if a["late"]:
        lines.append("  Warning: a result arrived after this action was given up, so it may have happened twice. "
                     "Check the outside system for a duplicate.")
    return lines


def story(record: str, run: Optional[str] = None) -> str:
    lg = open_record(record)
    entries = lg.entries()
    if run is not None:
        entries = [e for e in entries if e["run_id"] == run]
        if not entries:
            raise LedgerError(f"no entries for run {run!r}")
    acts = actions(lg)
    by_seq = {e["seq"]: e for e in entries}
    used = {e["key"]: (e["payload"] or {}).get("approval") for e in entries if e["kind"] == "effect_approved"}
    decided = {e["key"] for e in entries if e["kind"] == "ticket_decided"}

    runs: Dict[str, List[tuple]] = {}
    seen = set()
    for e in sorted(entries, key=lambda x: x["seq"]):
        kind, key, p = e["kind"], e["key"], e["payload"] or {}
        timeline = runs.setdefault(e["run_id"], [])
        if kind == "tool_intent":
            base = key.rsplit("#", 1)[0]
            if base in acts and base not in seen:
                seen.add(base)
                timeline.append(("action", acts[base]))
        elif kind == "rule_refused":
            timeline.append(("text", [f"- {_when(e['t'])}: a rule refused {_call(p.get('tool'), p.get('args'))}"
                                      f" ({p.get('rule')}: {p.get('why')}). Nothing ran."]))
        elif kind == "denial":
            by = p.get("by")
            timeline.append(("text", [f"- {_when(e['t'])}: a person said no to {_call(_tool_of(key), p.get('args'))}"
                                      f"{' (' + by + ')' if by else ''}. It will not run."]))
        elif kind == "pause" and key.startswith("ticket\x00") and key.split("\x00", 1)[1] not in decided:
            exp = p.get("exp")
            when = "has expired" if exp and time.time() > exp else f"is open until {_when(exp)}"
            timeline.append(("text", [f"- {_when(e['t'])}: waiting for a person to approve or refuse "
                                      f"{_call(p.get('tool'), p.get('args'))}. The request {when}."]))

    out = ["# What happened", ""]
    if not runs:
        out += ["The record has no runs yet.", ""]
    for run_id, timeline in runs.items():
        mine = [x for k, x in timeline if k == "action"]
        done = sum(1 for a in mine if a["state"].startswith("done"))
        doubt = [a for a in mine if "IN DOUBT" in a["state"] or a["late"]]
        notdone = sum(1 for a in mine if a["state"].startswith("not done"))
        out.append(f"## Run {run_id}")
        out.append("")
        parts = [f"{len(mine)} action{'s' if len(mine) != 1 else ''} started", f"{done} done"]
        if doubt:
            parts.append(f"{len(doubt)} may or may not have happened")
        if notdone:
            parts.append(f"{notdone} not done")
        out.append("In short: " + ", ".join(parts) + ".")
        if doubt:
            out.append("A person must check the outside system before this run can be called finished.")
            first = min(doubt, key=lambda a: a["first_t"])
            later = [a for a in mine if a["first_t"] > first["first_t"] and a is not first]
            if later:
                names = ", ".join(_call(a["tool"], a["args"]) for a in later)
                out.append(f"Note: after {_call(first['tool'], first['args'])} was left uncertain, later actions "
                           f"still started: {names}. Check that this was safe.")
        out.append("")
        for k, x in timeline:
            if k == "action":
                out += _action_lines(x, by_seq.get(used.get(x["key"])), record)
            else:
                out += x
        out.append("")
    out += [NOT_SHOWN, SENSITIVE]
    return "\n".join(out)


def main(argv: List[str]) -> int:
    args = list(argv)
    run = None
    if "--run" in args:
        i = args.index("--run")
        if i + 1 >= len(args):
            print("usage: call4agents story <record> [--run <id>]")
            return 2
        run = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        print("usage: call4agents story <record> [--run <id>]")
        return 2
    try:
        print(story(args[0], run))
    except LedgerError as e:
        print(f"story: {e}")
        return 1
    return 0
