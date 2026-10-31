# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""call4agents audit - experimental. A signed export of a run record: who approved what, when, for which exact
arguments, and what ran.

    call4agents audit <record> <out.json> [--run <id>]     needs the record's secret (C4A_SECRET or its `secret` file)
    call4agents audit-verify <out.json> [--secret-file f]  exit 0 only if nothing was changed

Each entry is chained to the one before it with a hash, and the end of the chain is signed with a key made from the
record's secret. Changing, removing, adding or reordering anything makes verification fail.

What this does not prove (see LIMITS): the signature is symmetric, so anyone who holds the secret can sign a false
export. It shows the file has not changed since a holder of the secret signed it. It does not show that the record
was honest before the export. Action results are left out; arguments are included, so treat the file as sensitive.
"""
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from call4agents.ledger import LedgerError, canonical
from call4agents.ops import actions, open_record

FORMAT = "c4a-audit-1"
LEFT_OUT = ("mac", "result")
LIMITS = ("This shows the file has not changed since a holder of the secret signed it. Anyone with the secret can sign "
          "a false export, and it does not show the record was honest before the export.")


def find_secret(record: Optional[str] = None, secret_file: Optional[str] = None) -> bytes:
    """The secret, or an error. A missing secret must never create a new one: a new key would sign anything."""
    if secret_file:
        return Path(secret_file).read_bytes().strip()
    env = os.environ.get("C4A_SECRET")
    if env:
        return env.encode()
    p = Path(record).parent / "secret" if record else None
    if p is not None and p.is_file():
        return p.read_bytes().strip()
    raise LedgerError("no secret found: set C4A_SECRET or pass --secret-file")


def _key(secret: bytes) -> bytes:
    return hmac.new(secret, FORMAT.encode(), hashlib.sha256).digest()      # never the same key that signs an approval


def _link(previous: str, entry: Dict[str, Any]) -> str:
    return hashlib.sha256(f"{previous}\x00{canonical(entry)}".encode()).hexdigest()


def _signature(secret: bytes, doc: Dict[str, Any]) -> str:
    signed = {k: doc[k] for k in ("format", "exported_at", "run", "count", "head", "actions", "decisions")}
    return hmac.new(_key(secret), canonical(signed).encode(), hashlib.sha256).hexdigest()


def _chain(entries: List[Dict[str, Any]]) -> str:
    head = FORMAT
    for e in entries:
        head = _link(head, {k: v for k, v in e.items() if k != "hash"})
        if e.get("hash") != head:
            raise LedgerError(f"entry {e.get('seq')} does not match the chain")
    return head


def _clean(entry: Dict[str, Any]) -> Dict[str, Any]:
    payload = entry["payload"]
    if isinstance(payload, dict):
        payload = {k: v for k, v in payload.items() if k not in LEFT_OUT}
    return {"seq": entry["seq"], "run_id": entry["run_id"], "kind": entry["kind"], "key": entry["key"],
            "t": entry["t"], "payload": payload}


def build(record: str, secret: bytes, run: Optional[str] = None) -> Dict[str, Any]:
    lg = open_record(record)
    all_entries = lg.entries()
    entries = [_clean(e) for e in all_entries if run is None or e["run_id"] == run]
    if run is not None and not entries:
        raise LedgerError(f"no entries for run {run!r}")
    by_seq = {e["seq"]: e for e in entries}
    decisions = [{"decision": "yes" if e["kind"] == "approval" else "no", "run_id": e["run_id"], "at": e["t"], "seq": e["seq"],
                  "args": (e["payload"] or {}).get("args"), "by": (e["payload"] or {}).get("by")}
                 for e in entries if e["kind"] in ("approval", "denial")]
    used = {e["key"]: (e["payload"] or {}).get("approval") for e in entries if e["kind"] == "effect_approved"}
    summary = []
    for a in actions(lg).values():
        if run is None or a["run_id"] == run:
            yes = by_seq.get(used.get(a["key"]))
            summary.append({"run_id": a["run_id"], "tool": a["tool"], "args": a["args"], "state": a["state"], "started": a["first_t"],
                            "approved_at": yes["t"] if yes else None, "approved_by": (yes["payload"] or {}).get("by") if yes else None})
    head = FORMAT
    for e in entries:
        head = _link(head, e)
        e["hash"] = head
    doc = {"format": FORMAT, "exported_at": time.time(), "run": run, "count": len(entries), "head": head, "actions": summary,
           "decisions": decisions, "entries": entries}
    doc["signature"] = _signature(secret, doc)
    return doc


def verify(doc: Dict[str, Any], secret: bytes) -> None:
    """Raises LedgerError unless the export is exactly what was signed."""
    if doc.get("format") != FORMAT:
        raise LedgerError("not a Call4Agents audit export")
    entries = doc.get("entries")
    if not isinstance(entries, list) or doc.get("count") != len(entries):
        raise LedgerError("the number of entries does not match")
    if _chain(entries) != doc.get("head"):
        raise LedgerError("the end of the chain does not match")
    if not hmac.compare_digest(str(doc.get("signature")), _signature(secret, doc)):
        raise LedgerError("the signature does not match: the file was changed, or this is not its secret")


def main(argv: List[str]) -> int:
    command, rest = argv[0], argv[1:]
    options = {}
    for flag in ("--run", "--secret-file"):
        if flag in rest:
            i = rest.index(flag)
            options[flag] = rest[i + 1] if i + 1 < len(rest) else ""
            del rest[i:i + 2]
    try:
        if command == "audit":
            if len(rest) != 2:
                print("usage: call4agents audit <record> <out.json> [--run <id>] [--secret-file f]")
                return 2
            secret = find_secret(rest[0], options.get("--secret-file"))
            doc = build(rest[0], secret, options.get("--run"))
            Path(rest[1]).write_text(json.dumps(doc, indent=1), encoding="utf-8")
            print(f"audit export (experimental): {doc['count']} entries, {len(doc['actions'])} actions, "
                  f"{len(doc['decisions'])} decisions -> {rest[1]}")
            print(LIMITS)
            return 0
        if len(rest) != 1:
            print("usage: call4agents audit-verify <out.json> [--secret-file f]")
            return 2
        doc = json.loads(Path(rest[0]).read_text(encoding="utf-8"))
        verify(doc, find_secret(rest[0], options.get("--secret-file")))
        print(f"OK: {doc['count']} entries, nothing changed since it was signed.")
        print(LIMITS)
        return 0
    except (LedgerError, OSError, ValueError, AttributeError, TypeError) as e:
        print(f"FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
