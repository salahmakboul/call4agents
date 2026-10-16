# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Operations on a run record, for the person on call (no SQL needed).

    call4agents status  <record>                       counts, runs, actions in doubt
    call4agents doubt   <record> [--json] [--fail-if-any]  actions that may or may not have happened
                                                       (--fail-if-any: exit 3 if there are any, for alerts)
    call4agents resolve <record> <key> done|not-done [note]
    call4agents archive <record> <archive> <days>      move finished runs older than N days
    call4agents verify  <record>                       integrity, format version, warnings

Every change is a NEW entry ("resolved by operator", with your note); nothing is edited in place,
except `archive`, which moves whole finished runs to another file in one transaction.
"""

import json
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional

from call4agents import codec as _codec
from call4agents.ledger import SCHEMA, Ledger, LedgerError


_NO_SIGNING = b"ops-commands-never-sign-approvals"     # ops never sign: no secret file is created


def open_record(path: str) -> Ledger:
    """An EXISTING run record. A typo must not create a new empty record that then looks healthy."""
    from pathlib import Path
    if not Path(path).is_file():
        raise LedgerError(f"{path} does not exist; give the path of an existing run record")
    return Ledger(path, secret=_NO_SIGNING)


def _attempt(key: str) -> int:
    return int(key.rsplit("#", 1)[1]) if "#" in key else 1


def actions(lg: Ledger) -> Dict[str, Dict[str, Any]]:
    """One row per action (base key): tool, args, run, attempts, state."""
    out: Dict[str, Dict[str, Any]] = {}
    for e in lg.entries():
        kind, key = e["kind"], e["key"]
        if kind == "tool_intent":
            base = key.rsplit("#", 1)[0]
            a = out.setdefault(base, {"key": base, "run_id": e["run_id"], "tool": (e["payload"] or {}).get("tool"),
                                      "args": (e["payload"] or {}).get("args"), "first_t": e["t"], "attempts": [],
                                      "abandoned": [], "done": False, "errors": [], "late": False, "resolved": None})
            a["attempts"].append(_attempt(key))
            a["last_t"] = e["t"]
        elif key in out:
            a = out[key]
            if kind == "tool_done":
                a["done"] = True
                if (e["payload"] or {}).get("resolved_by"):
                    a["resolved"] = e["payload"].get("note", "")
            elif kind == "tool_abandoned":
                a["abandoned"].append((e["payload"] or {}).get("attempt"))
                if (e["payload"] or {}).get("resolved_by"):
                    a["resolved"] = e["payload"].get("note", "")
            elif kind == "tool_error":
                a["errors"].append((e["payload"] or {}).get("error"))
            elif kind == "late_result":
                a["late"] = True
    for a in out.values():
        latest = max(a["attempts"])
        a["state"] = ("done" if a["done"] else
                      "not done (may run again)" if latest in a["abandoned"] else "IN DOUBT")
        if a["late"]:
            a["state"] += " + LATE RESULT (may have happened twice)"
    return out


def in_doubt(lg: Ledger) -> List[Dict[str, Any]]:
    return [a for a in actions(lg).values() if "IN DOUBT" in a["state"] or a["late"]]


def find_key(lg: Ledger, prefix: str) -> str:
    hits = [k for k in actions(lg) if k.startswith(prefix)]
    if len(hits) != 1:
        raise LedgerError(f"{len(hits)} actions start with {prefix!r}; give more characters of the key")
    return hits[0]


def resolve(lg: Ledger, prefix: str, outcome: str, note: str = "", result: Any = None) -> Dict[str, Any]:
    """done: it happened (later calls get `result`, default None). not-done: it did not; the next call runs it."""
    key = find_key(lg, prefix)
    a = actions(lg)[key]
    if a["done"]:
        raise LedgerError(f"{key[:12]} is already done; nothing to resolve")
    stamp = {"resolved_by": "operator", "note": note, "at": time.time()}
    if outcome == "done":
        lg.append(a["run_id"], "tool_done", key, {"result": _codec.encode(result), **stamp})
    elif outcome == "not-done":
        lg.append(a["run_id"], "tool_abandoned", key, {"attempt": max(a["attempts"]), **stamp})
    else:
        raise LedgerError("outcome must be 'done' or 'not-done'")
    return actions(lg)[key]


def verify(path: str) -> Dict[str, Any]:
    lg = open_record(path)
    with lg._conn() as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        version = conn.execute("SELECT v FROM meta WHERE k = 'schema_version'").fetchone()[0]
        rows = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    acts = actions(lg)
    return {"integrity": integrity, "format": int(version), "rows": rows, "actions": len(acts),
            "in_doubt": sum("IN DOUBT" in a["state"] for a in acts.values()),
            "late_results": sum(a["late"] for a in acts.values())}


def archive(path: str, dest: str, older_than_days: float) -> Dict[str, int]:
    """Move whole runs to `dest` when every action in them is finished (none in doubt) and the run's
    newest entry is older than N days. Runs with anything in doubt always stay."""
    lg = open_record(path)
    Ledger(dest, secret=_NO_SIGNING)                       # creates the archive with the same format
    cutoff = time.time() - older_than_days * 86400
    acts = actions(lg)
    open_runs = {a["run_id"] for a in acts.values() if not a["done"] and "not done" not in a["state"]}
    open_runs |= {a["run_id"] for a in acts.values() if a["late"]}
    with lg._conn() as conn:
        newest = dict(conn.execute("SELECT run_id, MAX(t) FROM entries GROUP BY run_id").fetchall())
    runs = [r for r, t in newest.items() if t < cutoff and r not in open_runs]
    moved = 0
    if runs:
        conn = lg._conn()
        conn.execute("ATTACH DATABASE ? AS arch", (str(dest),))
        try:
            with conn:
                marks = ",".join("?" * len(runs))
                moved = conn.execute(f"INSERT INTO arch.entries (run_id, kind, key, payload, t) SELECT run_id, kind, "
                                     f"key, payload, t FROM entries WHERE run_id IN ({marks}) ORDER BY seq", runs).rowcount
                conn.execute(f"DELETE FROM entries WHERE run_id IN ({marks})", runs)
        finally:
            conn.execute("DETACH DATABASE arch")
    return {"runs_moved": len(runs), "rows_moved": moved, "runs_kept_open": len(open_runs)}


def _print_actions(rows: List[Dict[str, Any]]) -> None:
    for a in rows:
        age = time.time() - a["first_t"]
        print(f"  {a['key'][:12]}  {a['state']:<26} run={a['run_id']}  {a['tool']}{json.dumps(a['args'], default=str)}"
              f"  age={age / 3600:.1f}h  attempts={len(a['attempts'])}"
              + (f"  last error: {a['errors'][-1]}" if a["errors"] else ""))


def main(argv: List[str]) -> int:
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "status":
            v = verify(rest[0])
            print(json.dumps(v, indent=1))
            return 0 if v["integrity"] == "ok" else 1
        if cmd == "verify":
            v = verify(rest[0])
            print(json.dumps(v, indent=1))
            return 0 if v["integrity"] == "ok" and not v["late_results"] else 1
        if cmd == "doubt":
            rows = in_doubt(open_record(rest[0]))
            if "--json" in rest:
                print(json.dumps(rows, default=str, indent=1))
            else:
                print(f"{len(rows)} action(s) need a decision" + (":" if rows else ""))
                _print_actions(rows)
                if rows:
                    print("Resolve one with: call4agents resolve <record> <key> done|not-done \"what you checked\"")
            return 3 if rows and "--fail-if-any" in rest else 0      # for cron / alerting
        if cmd == "resolve":
            a = resolve(open_record(rest[0]), rest[1], rest[2], " ".join(rest[3:]))
            _print_actions([a])
            return 0
        if cmd == "archive":
            print(json.dumps(archive(rest[0], rest[1], float(rest[2]))))
            return 0
    except (IndexError, ValueError):
        print(__doc__)
        return 2
    except LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(__doc__)
    return 2
