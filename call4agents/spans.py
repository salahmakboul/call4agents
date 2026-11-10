# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""call4agents spans - experimental. A run record as OpenTelemetry traces, for the tracing tool you already use.

    call4agents spans <record> <out.json> [--run <id>] [--with-args] [--service <name>]

Writes one OTLP/JSON trace document (one line). Each action is one span named "execute_tool <tool>" (kind INTERNAL),
with the GenAI attributes gen_ai.operation.name = "execute_tool" and gen_ai.tool.name, as in the OpenTelemetry GenAI
semantic conventions (status: Development, so names may still change). Each run is one trace. What the record knows
and the conventions do not name goes under c4a.*: the run, the state (done, not done, IN DOUBT), the attempts, who
approved it, and which rules decided. A call refused by a rule is a span too, with error.type "c4a.rule_refused".

An action in doubt has error.type "c4a.in_doubt" and status ERROR: tracing tools then show it as a failure, which is
what it needs (a person must look). An action that finished is left with status UNSET, as the conventions ask.
Arguments are left out unless you pass --with-args (gen_ai.tool.call.arguments is opt-in: it may hold personal data);
so are error texts and a refusal's reason, which can quote them: without the flag a status says only the error's
class or the rule's name.
Results are never written. Ids are made from the run id and action key, so the same record gives the same ids.

This file is not signed: for evidence use `call4agents audit`. We have not loaded it into a real collector or
tracing backend yet.
"""
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from call4agents.ledger import LedgerError
from call4agents.ops import actions, open_record

SCOPE = "call4agents"
INTERNAL, UNSET, ERROR = 1, 0, 2          # OTLP SpanKind and StatusCode numbers


def _hex(label: str, text: str, size: int) -> str:
    return hashlib.sha256(f"{label}\x00{text}".encode()).hexdigest()[:size]


def _value(v: Any) -> Dict[str, Any]:
    """An OTLP AnyValue."""
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_value(x) for x in v]}}
    if isinstance(v, dict):
        return {"kvlistValue": {"values": [{"key": str(k), "value": _value(x)} for k, x in v.items()]}}
    return {"stringValue": "" if v is None else str(v)}


def _attrs(pairs: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"key": k, "value": _value(v)} for k, v in pairs.items() if v is not None]


def _nano(t: float) -> str:
    return str(int(t * 1_000_000_000))


def _error_name(text: Optional[str]) -> str:
    """'Busy("not sent")' -> 'Busy': a low-cardinality class name, as error.type asks."""
    return (text or "error").split("(", 1)[0].strip() or "error"


def build(record: str, run: Optional[str] = None, with_args: bool = False, service: str = "call4agents") -> Dict[str, Any]:
    lg = open_record(record)
    entries = [e for e in lg.entries() if run is None or e["run_id"] == run]
    if run is not None and not entries:
        raise LedgerError(f"no entries for run {run!r}")
    last_t: Dict[str, float] = {}
    first: Dict[str, Dict[str, Any]] = {}
    approved, by_seq, gave_up = {}, {e["seq"]: e for e in entries}, {}
    for e in entries:
        base = e["key"].rsplit("#", 1)[0] if e["kind"] == "tool_intent" else e["key"]
        last_t[base] = e["t"]
        if e["kind"] == "tool_intent":
            first.setdefault(base, e["payload"] or {})
        elif e["kind"] == "tool_abandoned" and (e["payload"] or {}).get("error"):
            gave_up[e["key"]] = e["payload"]["error"]       # an error in retry_on: proved not done
        elif e["kind"] == "effect_approved":
            approved[e["key"]] = by_seq.get((e["payload"] or {}).get("approval"))
    spans = []
    for a in actions(lg).values():
        if run is not None and a["run_id"] != run:
            continue
        yes = approved.get(a["key"])
        rules = first.get(a["key"], {}).get("rules") or {}
        pairs: Dict[str, Any] = {
            "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": a["tool"], "c4a.run_id": a["run_id"],
            "c4a.state": a["state"], "c4a.attempts": len(a["attempts"]), "c4a.errors": len(a["errors"]) or None,
            "c4a.approved": True if yes else None, "c4a.approved_by": (yes["payload"] or {}).get("by") if yes else None,
            "c4a.rules.decision": rules.get("decision"), "c4a.rules.digest": rules.get("digest") or None,
            "c4a.key": a["key"]}
        status: Dict[str, Any] = {"code": UNSET}
        if "IN DOUBT" in a["state"] or a["late"]:
            pairs["error.type"] = "c4a.in_doubt"
            status = {"code": ERROR, "message": a["state"]}
        elif not a["done"] and (a["errors"] or a["key"] in gave_up):
            last = a["errors"][-1] if a["errors"] else gave_up[a["key"]]
            pairs["error.type"] = _error_name(last)
            status = {"code": ERROR, "message": str(last)[:200] if with_args else _error_name(last)}
        if with_args:
            pairs["gen_ai.tool.call.arguments"] = a["args"]
        spans.append({"traceId": _hex("c4a-trace", a["run_id"], 32), "spanId": _hex("c4a-span", a["key"], 16),
                      "name": f"execute_tool {a['tool']}", "kind": INTERNAL, "startTimeUnixNano": _nano(a["first_t"]),
                      "endTimeUnixNano": _nano(last_t.get(a["key"], a["first_t"])), "attributes": _attrs(pairs),
                      "status": status})
    for e in entries:
        if e["kind"] != "rule_refused":
            continue
        p = e["payload"] or {}
        pairs = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": p.get("tool"), "c4a.run_id": e["run_id"],
                 "c4a.state": "refused by a rule", "error.type": "c4a.rule_refused", "c4a.rule": p.get("rule"),
                 "c4a.rules.digest": p.get("digest") or None, "c4a.key": e["key"]}
        if with_args:
            pairs["gen_ai.tool.call.arguments"] = p.get("args")
        spans.append({"traceId": _hex("c4a-trace", e["run_id"], 32), "spanId": _hex("c4a-refusal", str(e["seq"]), 16),
                      "name": f"execute_tool {p.get('tool')}", "kind": INTERNAL, "startTimeUnixNano": _nano(e["t"]),
                      "endTimeUnixNano": _nano(e["t"]), "attributes": _attrs(pairs),
                      "status": {"code": ERROR, "message": str(p.get("why"))[:200] if with_args
                                 else f"refused by the rule {p.get('rule')}"}})
    from call4agents import __version__
    return {"resourceSpans": [{"resource": {"attributes": _attrs({"service.name": service})},
                               "scopeSpans": [{"scope": {"name": SCOPE, "version": __version__}, "spans": spans}]}]}


def main(argv: List[str]) -> int:
    rest, options = list(argv), {}
    for flag in ("--run", "--service"):
        if flag in rest:
            i = rest.index(flag)
            options[flag] = rest[i + 1] if i + 1 < len(rest) else ""
            del rest[i:i + 2]
    with_args = "--with-args" in rest
    rest = [r for r in rest if r != "--with-args"]
    if len(rest) != 2:
        print("usage: call4agents spans <record> <out.json> [--run <id>] [--with-args] [--service <name>]")
        return 2
    try:
        doc = build(rest[0], options.get("--run"), with_args, options.get("--service") or "call4agents")
    except (LedgerError, OSError) as e:
        print(f"FAILED: {e}")
        return 1
    spans = doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
    Path(rest[1]).write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
    doubt = sum(1 for s in spans if any(a["key"] == "error.type" and a["value"]["stringValue"] == "c4a.in_doubt"
                                        for a in s["attributes"]))
    print(f"spans (experimental): {len(spans)} spans, {doubt} in doubt -> {rest[1]}"
          + ("" if with_args else " (arguments left out; --with-args adds them)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
