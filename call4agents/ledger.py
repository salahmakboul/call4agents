# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""The run record: one append-only SQLite table (Python standard library only).

Every module reads and writes the same entries:
  tool_intent   written BEFORE a side effect runs
  tool_done     written right after it returns (payload = the recorded result)
  approval      an HMAC over (tool, canonical arguments), signed with a local secret
  denial        a "no" for exact arguments; cancels earlier approvals for them
  approval_used one approval spent by one effect (unique: a "yes" is used once)
Entries are never updated or deleted.
"""

import dataclasses
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """CREATE TABLE IF NOT EXISTS entries (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    payload TEXT,
    t REAL NOT NULL
)"""


def _stable(o: Any) -> Any:
    """A text form that is the same in every process, for the types where we KNOW it is.
    str() is not stable in general: a set's order changes per process, an object prints its memory
    address, a socket prints its file number (big test, 2026-10-16). Anything else is refused."""
    import datetime as _dt
    import decimal
    import enum
    import pathlib
    import uuid
    if isinstance(o, (set, frozenset)):
        return sorted(canonical(x) for x in o)
    if isinstance(o, tuple):
        return list(o)
    if isinstance(o, (bytes, bytearray)):
        return {"__bytes__": bytes(o).hex()}
    if isinstance(o, enum.Enum):
        return f"{type(o).__qualname__}.{o.name}"
    if isinstance(o, (_dt.datetime, _dt.date, _dt.time)):
        return o.isoformat()
    if isinstance(o, (decimal.Decimal, uuid.UUID, pathlib.PurePath)):
        return str(o)
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    dump = getattr(o, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    raise TypeError(f"{type(o).__name__} is not a type with a stable text form, so it cannot be part of an action's "
                    "key (it could change between restarts and the action would run again). Pass plain values, "
                    "or name the action with c4a_key=... (or key=)")


MAX_DEPTH = 500


def _too_deep(value: Any) -> bool:
    """Nesting deeper than MAX_DEPTH, checked by the kit itself so every system gives the same answer:
    Python's own recursion limit differs between Windows and Linux."""
    stack = [(value, 1)]
    while stack:
        v, d = stack.pop()
        if d > MAX_DEPTH:
            return True
        if isinstance(v, dict):
            stack.extend((x, d + 1) for x in v.values())
        elif isinstance(v, (list, tuple, set, frozenset)):
            stack.extend((x, d + 1) for x in v)
    return False


def canonical(value: Any) -> str:
    """Stable text for arguments: key order and spacing never change the hash."""
    if _too_deep(value):
        raise TypeError(f"arguments are nested deeper than {MAX_DEPTH} levels; pass c4a_key=... instead")
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_stable)
    except RecursionError:
        raise TypeError("arguments are nested too deeply to make a key; pass c4a_key=... instead") from None


def effect_key(run_id: str, tool: str, args: Dict[str, Any]) -> str:
    return hashlib.sha256(f"{run_id}\x00{tool}\x00{canonical(args)}".encode()).hexdigest()


def intent_key(base: str, attempt: int = 1) -> str:
    return f"{base}#{attempt}"


SCHEMA_VERSION = 1          # bump only with a migration below; older kits refuse newer records


class LedgerError(RuntimeError):
    """The run record cannot be used safely (wrong file, newer format, network drive, bad secret)."""


class TicketRefused(Exception):
    """A decision came with a ticket this record did not issue, or one that was edited, expired or already used."""


NETWORK_FS = ("nfs", "nfs4", "cifs", "smbfs", "smb3", "9p", "fuse.sshfs", "afpfs")


def _on_network_drive(path: Path, mounts: Optional[str] = None) -> bool:
    """SQLite's write-ahead log needs shared memory on ONE machine; on a network drive locking breaks
    silently and two callers can both run an effect. We refuse those paths.
    Windows: UNC shares. Linux: the mount type in /proc/mounts (or `mounts`, for tests).
    macOS and other systems: not detected."""
    if mounts is None and sys.platform == "win32":
        return str(path.resolve()).startswith("\\\\")
    if mounts is None:
        if not sys.platform.startswith("linux"):
            return False
        try:
            with open("/proc/mounts", encoding="utf-8", errors="replace") as f:
                mounts = f.read()
        except OSError:
            return False
    p = Path(path).as_posix()
    best = ("", "")
    for m in (line.split() for line in mounts.splitlines()):
        if len(m) > 2 and (p == m[1] or p.startswith(m[1].rstrip("/") + "/")) and len(m[1]) > len(best[0]):
            best = (m[1], m[2])
    return best[1] in NETWORK_FS


class Ledger:
    _DUPLICATE: Any = sqlite3.IntegrityError     # what the store raises when a UNIQUE index refuses a row

    def __init__(self, path: str = ".c4a/ledger.sqlite", secret: Optional[bytes] = None,
                 old_secrets: Optional[List[bytes]] = None, allow_network_drive: bool = False) -> None:
        self.path = Path(path)
        if not allow_network_drive and _on_network_drive(self.path.parent):
            raise LedgerError(f"{self.path} is on a network drive: SQLite locking is not reliable there, so "
                              "once-only cannot be guaranteed. Put the run record on a local disk "
                              "(or pass allow_network_drive=True if you know the drive is safe).")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._local = threading.local()
        try:
            self._setup()
        except sqlite3.DatabaseError as exc:
            raise LedgerError(f"{self.path} is not a usable Call4Agents run record ({exc}). "
                              "Point c4a.init() at a new file or at an existing run record.") from exc
        self.secret = secret or self._load_secret()
        if not self.secret:
            raise LedgerError("the approval secret is empty; pass at least 16 random bytes (or let the kit create one)")
        if len(self.secret) < 16:
            warnings.warn("the approval secret is shorter than 16 bytes: fine for tests, too weak for production",
                          stacklevel=3)
        env_old = os.environ.get("C4A_OLD_SECRETS", "")
        self.old_secrets = list(old_secrets or []) + [x.encode() for x in env_old.split(",") if x]

    def _setup(self) -> None:
        with self._conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
            conn.execute("INSERT OR IGNORE INTO meta (k, v) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            self._tables(conn, SCHEMA)

    def _tables(self, conn: Any, schema: str) -> None:
        """Format check, table and indexes: the same for every store, so the guarantees are the same."""
        found = int(conn.execute("SELECT v FROM meta WHERE k = 'schema_version'").fetchone()[0])
        if found > SCHEMA_VERSION:
            raise LedgerError(f"{self.path} was written by a newer Call4Agents (format {found}, this kit "
                              f"reads up to {SCHEMA_VERSION}). Upgrade call4agents; never downgrade a run record.")
        conn.execute(schema)
        conn.execute("CREATE INDEX IF NOT EXISTS by_key ON entries(kind, key)")
        # Only ONE intent per (effect, attempt): two callers can never both start the same
        # effect (without it, 14 simultaneous callers sent 13 emails in our stress test).
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_intent ON entries(key) WHERE kind = 'tool_intent'")
        # Each "yes" is spent by ONE action (K17): a deliberate second identical action needs a new "yes".
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_use ON entries(key) WHERE kind = 'approval_used'")
        # Each approval ticket is decided ONCE (T5): a second "approve" or a late "deny" is refused.
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_decision ON entries(key) WHERE kind = 'ticket_decided'")

    def _conn(self) -> sqlite3.Connection:
        """One connection per thread and process, reused (opening one per call cost ~3.6 ms of the 22 ms of
        a new action). A forked child gets its own: SQLite connections must not cross a fork."""
        conn = getattr(self._local, "conn", None)
        if conn is None or self._local.pid != os.getpid():
            conn = sqlite3.connect(self.path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")      # an entry that returned is on disk before the effect runs
            conn.execute("PRAGMA journal_size_limit=1048576")   # the WAL file shrinks back to 1 MB after a checkpoint
            self._local.conn, self._local.pid = conn, os.getpid()
        return conn

    def _load_secret(self) -> bytes:
        env = os.environ.get("C4A_SECRET")
        if env:
            return env.encode()
        p = self.path.parent / "secret"
        if not p.exists():
            # Write the full secret to a private temp file, then hard-link it into place: the link either
            # creates `secret` complete or fails because another process won. A reader can never see a
            # half-written secret (checked by tests/test_c4a_production.py P3).
            tmp = p.with_name(f".secret.{os.getpid()}.{secrets.token_hex(4)}")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(secrets.token_bytes(32))
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, p)
            except FileExistsError:
                pass                                  # another process created it first: use theirs
            finally:
                os.unlink(tmp)
        data = p.read_bytes()
        if len(data) < 16:
            raise LedgerError(f"{p} is shorter than 16 bytes; delete it to create a new secret "
                              "(approvals signed with the old one then stop counting)")
        return data

    # ------------------------------------------------------------ append / read
    def append(self, run_id: str, kind: str, key: str, payload: Any = None) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute("INSERT INTO entries (run_id, kind, key, payload, t) VALUES (?, ?, ?, ?, ?)",
                               (run_id, kind, key, None if payload is None else canonical(payload), time.time()))
            return cur.lastrowid

    def claim(self, run_id: str, key: str, payload: Any = None) -> bool:
        """Atomically record an intent. True = this caller owns the effect; False = someone already does."""
        try:
            self.append(run_id, "tool_intent", key, payload)
            return True
        except self._DUPLICATE:
            return False

    def first_intent_time(self, base_key: str) -> Optional[float]:
        """When the FIRST attempt of this effect was recorded (any attempt number), or None."""
        with self._conn() as conn:
            row = conn.execute("SELECT MIN(t) FROM entries WHERE kind = 'tool_intent' AND key LIKE ?",
                               (base_key + "#%",)).fetchone()
        return row[0] if row and row[0] is not None else None

    def count_run_kind(self, run_id: str, kind: str) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM entries WHERE run_id = ? AND kind = ?", (run_id, kind)).fetchone()[0]

    def count(self, kind: str, key: str) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM entries WHERE kind = ? AND key = ?", (kind, key)).fetchone()[0]

    def latest(self, kind: str, key: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT seq, run_id, payload, t FROM entries WHERE kind = ? AND key = ? "
                               "ORDER BY seq DESC LIMIT 1", (kind, key)).fetchone()
        if row is None:
            return None
        return {"seq": row[0], "run_id": row[1], "payload": self._payload(row[0], row[2]), "t": row[3]}

    def _payload(self, seq: int, text: Optional[str]) -> Any:
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError as exc:
            raise LedgerError(f"row {seq} of {self.path} is damaged (its payload is not valid JSON: {exc}). "
                              "The record was edited by hand or corrupted; run `call4agents verify` on it.") from None

    def entries_for_key(self, key: str, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT seq, run_id, kind, key, payload, t FROM entries WHERE key = ?"
                                + (" AND kind = ?" if kind else "") + " ORDER BY seq",
                                (key, kind) if kind else (key,)).fetchall()
        return [{"seq": r[0], "run_id": r[1], "kind": r[2], "key": r[3],
                 "payload": self._payload(r[0], r[4]), "t": r[5]} for r in rows]

    def entries(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT seq, run_id, kind, key, payload, t FROM entries"
                                + (" WHERE run_id = ?" if run_id else "") + " ORDER BY seq",
                                (run_id,) if run_id else ()).fetchall()
        return [{"seq": r[0], "run_id": r[1], "kind": r[2], "key": r[3],
                 "payload": self._payload(r[0], r[4]), "t": r[5]} for r in rows]

    # ------------------------------------------------------------ approvals
    def sign(self, tool: str, args: Dict[str, Any], key: Optional[bytes] = None) -> str:
        return hmac.new(key or self.secret, f"{tool}\x00{canonical(args)}".encode(), hashlib.sha256).hexdigest()

    def record_approval(self, run_id: str, tool: str, args: Dict[str, Any]) -> str:
        mac = self.sign(tool, args)
        self.append(run_id, "approval", f"{run_id}\x00{tool}", {"mac": mac, "args": args})
        return mac

    def record_denial(self, run_id: str, tool: str, args: Dict[str, Any]) -> str:
        """A "no" for exactly these arguments: it cancels every earlier "yes" for them (K17)."""
        mac = self.sign(tool, args)
        self.append(run_id, "denial", f"{run_id}\x00{tool}", {"mac": mac, "args": args})
        return mac

    def _matching(self, run_id: str, tool: str, args: Dict[str, Any], kind: str) -> List[int]:
        wants = [self.sign(tool, args, key) for key in [self.secret] + list(getattr(self, "old_secrets", []))]
        with self._conn() as conn:
            rows = conn.execute("SELECT seq, payload FROM entries WHERE kind = ? AND key = ? ORDER BY seq",
                                (kind, f"{run_id}\x00{tool}")).fetchall()
        return [r[0] for r in rows if r[1] and any(hmac.compare_digest(json.loads(r[1]).get("mac", ""), w) for w in wants)]

    def matching_approvals(self, run_id: str, tool: str, args: Dict[str, Any]) -> List[int]:
        """seq of every "yes" recorded for exactly these arguments, spent or not."""
        return self._matching(run_id, tool, args, "approval")

    def last_denial(self, run_id: str, tool: str, args: Dict[str, Any]) -> int:
        """seq of the latest "no" for these exact arguments, 0 if none."""
        return max(self._matching(run_id, tool, args, "denial"), default=0)

    def approved(self, run_id: str, tool: str, args: Dict[str, Any]) -> bool:
        """True only if an approval exists whose MAC matches THESE exact arguments and no later "no"
        cancelled it. (Whether it is still unused is use_approval's job.)"""
        denied = self.last_denial(run_id, tool, args)
        return any(seq > denied for seq in self._matching(run_id, tool, args, "approval"))

    def use_approval(self, run_id: str, tool: str, args: Dict[str, Any], effect: str) -> bool:
        """Spend ONE unused, not-denied "yes" on this effect, atomically: a UNIQUE index allows one use
        per approval, so two callers can never spend the same "yes" (K17)."""
        denied = self.last_denial(run_id, tool, args)
        for seq in self._matching(run_id, tool, args, "approval"):
            if seq <= denied:
                continue
            try:
                self.append(run_id, "approval_used", f"{run_id}\x00{tool}\x00{seq}", {"effect": effect})
            except self._DUPLICATE:
                continue                                  # someone already spent this one
            self.append(run_id, "effect_approved", effect, {"approval": seq})
            return True
        return False

    # ------------------------------------------------------------ approval tickets (experimental)
    def _ticket_mac(self, key: bytes, nonce: str, run_id: str, tool: str, approval_mac: str, exp: float) -> str:
        msg = "\x00".join(["ticket", nonce, run_id, tool, approval_mac, repr(exp)])
        return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()

    def issue_ticket(self, run_id: str, tool: str, args: Dict[str, Any], ttl: float,
                     extra: Optional[Dict[str, Any]] = None) -> str:
        """Record a pause for a person to decide on exactly (tool, args), and return a ticket to show them.
        The ticket holds no arguments: only a random id and a MAC. What may run is read from THIS record
        when the decision comes back, never from what the client sends."""
        approval_mac = self.sign(tool, args)
        # The same call paused again while its ticket is still open (a LangGraph node re-runs from the top on
        # resume): give back the same ticket, so one person's decision answers it (T11). Open = not decided
        # yet, or approved and the action has not used the "yes". After a "no", or once the action ran, a new
        # pause asks again with a new ticket.
        rows = self.entries(run_id)
        for e in rows:
            p = e["payload"] or {}
            if (e["kind"] == "pause" and p.get("tool") == tool and p.get("mac") == approval_mac
                    and p.get("exp", 0) > time.time()
                    and {k: v for k, v in p.items() if k not in ("tool", "args", "mac", "exp")} == (extra or {})):
                old = e["key"].split("\x00", 1)[1]
                decided = self.latest("ticket_decided", old)
                yes = [r["seq"] for r in rows if r["kind"] == "approval" and (r["payload"] or {}).get("ticket") == old]
                unused = yes and not self.count("approval_used", f"{run_id}\x00{tool}\x00{yes[0]}")
                if decided is None or (decided["payload"]["decision"] == "approve" and unused):
                    return f"c4a1.{old}.{self._ticket_mac(self.secret, old, run_id, tool, approval_mac, p['exp'])}"
        nonce = secrets.token_hex(12)
        exp = round(time.time() + ttl, 3)
        self.append(run_id, "pause", f"ticket\x00{nonce}",
                    {"tool": tool, "args": args, "mac": approval_mac, "exp": exp, **(extra or {})})
        return f"c4a1.{nonce}.{self._ticket_mac(self.secret, nonce, run_id, tool, approval_mac, exp)}"

    def decide_ticket(self, ticket: str, decision: str, by: str = "") -> Dict[str, Any]:
        """Turn a person's decision into a recorded "yes" or "no" for the exact call the ticket was issued for."""
        if decision not in ("approve", "deny"):
            raise ValueError("decision must be 'approve' or 'deny'")
        parts = ticket.split(".") if isinstance(ticket, str) else []
        if len(parts) != 3 or parts[0] != "c4a1":
            raise TicketRefused("this is not a Call4Agents ticket")
        _, nonce, mac = parts
        row = self.latest("pause", f"ticket\x00{nonce}")
        if row is None:
            raise TicketRefused("this record never issued that ticket")
        p = row["payload"]
        keys = [self.secret, *getattr(self, "old_secrets", [])]
        if not any(hmac.compare_digest(mac, self._ticket_mac(k, nonce, row["run_id"], p["tool"], p["mac"], p["exp"]))
                   for k in keys):
            raise TicketRefused("the ticket or its recorded pause was changed (the signature does not match)")
        if time.time() > p["exp"]:
            raise TicketRefused("the ticket expired; pause again to ask the person again")
        try:
            self.append(row["run_id"], "ticket_decided", nonce, {"decision": decision, "by": by})
        except self._DUPLICATE:
            raise TicketRefused("this ticket was already decided") from None
        kind = "approval" if decision == "approve" else "denial"
        self.append(row["run_id"], kind, f"{row['run_id']}\x00{p['tool']}",
                    {"mac": p["mac"], "args": p["args"], "ticket": nonce, "by": by})
        extra = {k: v for k, v in p.items() if k not in ("tool", "args", "mac", "exp")}
        return {"run_id": row["run_id"], "tool": p["tool"], "args": p["args"], "decision": decision, "by": by, **extra}
