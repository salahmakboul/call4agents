# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""One run record shared by several machines, in PostgreSQL (pip install "call4agents[postgres]").

Same table, same UNIQUE indexes and same format check as the SQLite record (Ledger._tables), so
"one intent per effect" and "one use per yes" hold across machines. Not in the core: the core uses only
the Python standard library (K7); psycopg is imported only when you open a postgresql:// record.
"""
import os
import threading
import time
import warnings
from typing import Any, List, Optional

from call4agents.ledger import SCHEMA_VERSION, Ledger, LedgerError, canonical
PG_SCHEMA = """CREATE TABLE IF NOT EXISTS entries (
    seq BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    payload TEXT,
    t DOUBLE PRECISION NOT NULL
)"""


def _redact(dsn: str) -> str:
    """The connection string without user or password, for error messages."""
    from urllib.parse import urlsplit
    u = urlsplit(dsn)
    return f"{u.scheme}://{u.hostname or ''}{':' + str(u.port) if u.port else ''}{u.path}"


def _pg_in(v: Any) -> Any:
    """PostgreSQL text cannot hold NUL, which approval keys use as a separator. A reversible escape:
    \\x01 -> \\x01\\x01 and \\x00 -> \\x01\\x02 (no two different strings map to the same text)."""
    return v.replace("\x01", "\x01\x01").replace("\x00", "\x01\x02") if isinstance(v, str) else v


def _pg_out(v: Any) -> Any:
    if not isinstance(v, str) or "\x01" not in v:
        return v
    out, i = [], 0
    while i < len(v):
        if v[i] == "\x01":
            out.append("\x01" if v[i + 1] == "\x01" else "\x00")
            i += 2
        else:
            out.append(v[i])
            i += 1
    return "".join(out)


class _PgRows:
    def __init__(self, cur: Any) -> None:
        self.cur = cur

    def fetchone(self) -> Any:
        row = self.cur.fetchone()
        return None if row is None else tuple(_pg_out(v) for v in row)

    def fetchall(self) -> List[Any]:
        return [tuple(_pg_out(v) for v in row) for row in self.cur.fetchall()]


class _PgConn:
    """Gives a psycopg connection the small part of the sqlite3 API the Ledger uses: `with conn:` is one
    transaction (commit, or rollback on error), `?` placeholders, and text with NUL in it."""

    def __init__(self, raw: Any) -> None:
        self.raw = raw

    def __enter__(self) -> "_PgConn":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is None:
            self.raw.commit()
        else:
            self.raw.rollback()
        return False

    def execute(self, sql: str, params: Any = ()) -> Any:
        return _PgRows(self.raw.execute(sql.replace("?", "%s"), tuple(_pg_in(v) for v in params)))


class PostgresLedger(Ledger):
    """The same run record in PostgreSQL, so several machines (workers, replicas) share it.
    The same UNIQUE indexes make "one intent per effect" and "one use per yes" hold across machines.
    Needs `pip install "call4agents[postgres]"` (psycopg 3). Every machine must use the SAME secret
    (secret= or C4A_SECRET): approvals signed on one machine are checked on another, so there is no
    local secret file here."""

    def __init__(self, dsn: str, secret: Optional[bytes] = None, old_secrets: Optional[List[bytes]] = None) -> None:
        try:
            import psycopg
        except ImportError:
            raise LedgerError('a PostgreSQL run record needs psycopg 3: pip install "call4agents[postgres]"') from None
        self._psycopg = psycopg
        self._DUPLICATE = psycopg.errors.UniqueViolation
        self.dsn, self.path = dsn, _redact(dsn)
        self._lock, self._local = threading.Lock(), threading.local()
        secret = secret or os.environ.get("C4A_SECRET", "").encode()
        if not secret:
            raise LedgerError("a shared run record needs the same secret on every machine: pass secret= or set "
                              "C4A_SECRET (a local secret file would differ per machine)")
        if len(secret) < 16:
            warnings.warn("the approval secret is shorter than 16 bytes: fine for tests, too weak for production",
                          stacklevel=3)
        self.secret = secret
        try:
            self._setup()
        except psycopg.Error as exc:
            raise LedgerError(f"{self.path} is not a usable Call4Agents run record ({exc})") from exc
        env_old = os.environ.get("C4A_OLD_SECRETS", "")
        self.old_secrets = list(old_secrets or []) + [x.encode() for x in env_old.split(",") if x]

    def _setup(self) -> None:
        with self._conn() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(4243)")   # machines starting together create the tables once
            conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
            conn.execute("INSERT INTO meta (k, v) VALUES ('schema_version', ?) ON CONFLICT (k) DO NOTHING",
                         (str(SCHEMA_VERSION),))
            self._tables(conn, PG_SCHEMA)

    def _conn(self) -> _PgConn:      # type: ignore[override]
        conn = getattr(self._local, "conn", None)
        if conn is None or self._local.pid != os.getpid() or conn.raw.closed:
            conn = _PgConn(self._psycopg.connect(self.dsn))
            self._local.conn, self._local.pid = conn, os.getpid()
        return conn

    def append(self, run_id: str, kind: str, key: str, payload: Any = None) -> int:
        with self._lock, self._conn() as conn:
            row = conn.execute("INSERT INTO entries (run_id, kind, key, payload, t) VALUES (?, ?, ?, ?, ?) "
                               "RETURNING seq",
                               (run_id, kind, key, None if payload is None else canonical(payload), time.time()))
            return row.fetchone()[0]
