# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Record / replay of model calls and tool effects, and a diff between two runs (stdlib only).

    @c4a.recorded                       # wrap your model call (any function: OpenAI, LiteLLM, local ...)
    def ask_model(prompt): ...

Record:  c4a.init("run.sqlite")                       - every call is stored, in order, per run
Replay:  c4a.init("new.sqlite", replay_from="run.sqlite") - recorded results come back, offline and free;
         effects return their recorded result and never run; a call whose arguments differ from the
         recording raises ReplayMismatch (your code or prompt changed).
Diff:    python -m call4agents diff old.sqlite new.sqlite [run_id]
         shows the first place where two runs differ (e.g. the same task with another model).

Results keep their type in replay (call4agents.codec); unsupported types fail at record time.
"""

import functools
import inspect
import json
import sqlite3
from typing import Any, Callable, Dict, List, Optional

from call4agents import codec as _codec
from call4agents.ledger import canonical


class ReplayMismatch(Exception):
    pass


class ReplayMissing(Exception):
    pass


class _Source:
    """A recorded run record, read-only."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.cursor: Dict[tuple, int] = {}

    def calls(self, run_id: str, name: str) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT payload FROM entries WHERE run_id = ? AND kind = 'llm_call' ORDER BY seq",
                                (run_id,)).fetchall()
        finally:
            conn.close()
        return [p for p in (json.loads(r[0]) for r in rows) if p["name"] == name]

    def next_call(self, run_id: str, name: str, args: Dict[str, Any], decode: Callable = None) -> Any:
        calls = self.calls(run_id, name)
        i = self.cursor.get((run_id, name), 0)
        if i >= len(calls):
            raise ReplayMissing(f"no recorded call #{i + 1} of {name} in run {run_id}")
        self.cursor[(run_id, name)] = i + 1
        if canonical(calls[i]["args"]) != canonical(args):
            raise ReplayMismatch(f"{name} call #{i + 1} in run {run_id}: recorded {canonical(calls[i]['args'])}, "
                                 f"now {canonical(args)}")
        return (decode or _codec.decode)(calls[i]["result"])

    def done(self, key: str) -> Optional[Dict[str, Any]]:
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT payload FROM entries WHERE kind = 'tool_done' AND key = ? ORDER BY seq DESC "
                               "LIMIT 1", (key,)).fetchone()
        finally:
            conn.close()
        return None if row is None else json.loads(row[0])


SOURCE: Optional[_Source] = None


def set_source(path: Optional[str]) -> None:
    global SOURCE
    SOURCE = _Source(path) if path else None


def recorded(fn: Optional[Callable] = None, *, name: Optional[str] = None,
             encode: Optional[Callable] = None, decode: Optional[Callable] = None):
    """Record a model call's arguments and result; in replay mode return the recording instead.
    Results keep their type (call4agents.codec); a type it cannot keep raises NotRecordable at
    record time unless you pass encode= (value -> JSON) and decode= (JSON -> value)."""
    enc = encode or _codec.encode
    dec = decode or _codec.decode
    def wrap(f: Callable) -> Callable:
        label = name or f.__name__

        def args_of(a: tuple, k: dict) -> Dict[str, Any]:
            bound = inspect.signature(f).bind(*a, **k)
            bound.apply_defaults()
            return dict(bound.arguments)

        def store(call_args: Dict[str, Any], result: Any) -> None:
            from call4agents import current_run, current_ledger
            lg, run_id = current_ledger(), current_run()
            n = lg.count_run_kind(run_id, "llm_call")
            lg.append(run_id, "llm_call", f"{run_id}#{n + 1}", {"name": label, "args": call_args, "result": enc(result)})

        if inspect.iscoroutinefunction(f):
            @functools.wraps(f)
            async def aw(*a: Any, **k: Any) -> Any:
                from call4agents import current_run
                call_args = args_of(a, k)
                result = SOURCE.next_call(current_run(), label, call_args, dec) if SOURCE else await f(*a, **k)
                store(call_args, result)
                return result
            return aw

        @functools.wraps(f)
        def w(*a: Any, **k: Any) -> Any:
            from call4agents import current_run
            call_args = args_of(a, k)
            result = SOURCE.next_call(current_run(), label, call_args, dec) if SOURCE else f(*a, **k)
            store(call_args, result)
            return result
        return w

    return wrap(fn) if fn is not None else wrap


def trace(path: str, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """The comparable steps of a run: model calls and finished effects, in order."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT run_id, kind, payload FROM entries WHERE kind IN ('llm_call', 'tool_intent', "
                            "'tool_done') " + ("AND run_id = ? " if run_id else "") + "ORDER BY seq",
                            (run_id,) if run_id else ()).fetchall()
    finally:
        conn.close()
    steps = []
    for rid, kind, payload in rows:
        p = json.loads(payload) if payload else {}
        if kind == "llm_call":
            steps.append({"run": rid, "step": "model", "name": p["name"], "args": p["args"], "result": p["result"]})
        elif kind == "tool_intent":
            steps.append({"run": rid, "step": "tool", "name": p.get("tool"), "args": p.get("args")})
    return steps


def diff(path_a: str, path_b: str, run_id: Optional[str] = None) -> Dict[str, Any]:
    a, b = trace(path_a, run_id), trace(path_b, run_id)
    for i, (x, y) in enumerate(zip(a, b)):
        for field in ("step", "name", "args", "result"):
            if x.get(field) != y.get(field):
                return {"same": False, "first_difference": i + 1, "field": field, "a": x, "b": y,
                        "steps_a": len(a), "steps_b": len(b)}
    if len(a) != len(b):
        return {"same": False, "first_difference": min(len(a), len(b)) + 1, "field": "length",
                "steps_a": len(a), "steps_b": len(b)}
    return {"same": True, "steps": len(a)}
