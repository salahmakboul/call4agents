# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""The contract a framework implements to join the benchmark, and the fake bank that counts actions.

A subject is a small module in bench/subjects/ with one class that subclasses Subject. It drives ONE
framework, with a scripted model (no network, no keys), in two variants:

  env.kit is False  the framework's documented default configuration
  env.kit is True   the same agent, with the tool guarded by call4agents

Every method below runs in a NEW Python process (the benchmark starts one per step), so anything the
next step needs must be saved in env.work, the way the framework documents saving and resuming. The
benchmark never trusts what a subject says about actions: it counts rows in the fake bank, an SQLite
file that every process writes to.

The tool. Every subject offers the model ONE tool, pay(amount: int, to: str). Its body must be:

    result = env.pay(amount, to)      # the outside action: one row in the bank
    env.after_action()                # right after it returned, still inside the tool
    return result

In the call4agents variant, env.pay goes inside the guard and env.after_action() stays outside it. The
benchmark injects its faults in those two calls (a kill, a timeout), so a subject does not code them.

The scripted model. It asks for pay(**env.call). After a successful result it asks again until it has
had env.repeat successful results (1, or 2 in S5), then it answers in text. If the tool result is an
error, it asks for the same call once more (as a model would), and gives up after a second error. A
framework that turns tool errors into text for the model gets this behaviour; one that raises the error
to the caller gets a caller retry from the benchmark instead (see scenarios.md, S2).

Approvals. If the framework has a "yes before the tool runs" feature, use it: start_approval() stops
the run waiting for a person, decide() records the person's answer the framework's way and returns the
message a resume needs (JSON), resume_approved() continues with that message. If the framework has no
such feature, list the approval scenarios in not_applicable() with the reason: the cell is then N/A,
not a failure.
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

KILLED = 137                    # exit code of a process the benchmark killed on purpose


class Subject:
    """Subclass this. All methods get an Env and run in a fresh process."""

    name = "subject"            # short id, used in the results table
    title = "Subject"           # shown in the table
    packages: tuple = ()        # distributions whose versions go in the results, e.g. ("langgraph",)
    requires: tuple = ()        # modules that must import, else the subject is skipped, e.g. ("langgraph",)
    default_config = ""         # one line: the documented default this subject uses
    kit_config = ""             # one line: what the call4agents variant changes

    def not_applicable(self, scenario: str, kit: bool) -> Optional[str]:
        """A reason when this scenario cannot apply (for example no approval feature), else None."""
        return None

    # --- a run without approval (S1, S2, S3, S7, C1)
    def start(self, env: "Env") -> None:
        """Start run env.run_id. The scripted model asks for pay(**env.call); no approval is needed."""
        raise NotImplementedError

    def resume(self, env: "Env") -> None:
        """Continue run env.run_id from what was saved, the way the framework documents it (after a crash,
        or after the run stopped with an error)."""
        raise NotImplementedError

    # --- a run that needs a person's yes (S4, S5, S6, S8, C2)
    def start_approval(self, env: "Env") -> Dict[str, Any]:
        """Start run env.run_id with pay needing a yes. Return the arguments shown to the person, e.g.
        {"amount": 50, "to": "alice"}. The run must stop and wait (saved in env.work)."""
        raise NotImplementedError

    def decide(self, env: "Env", shown: Dict[str, Any], decision: str) -> Dict[str, Any]:
        """Record the person's decision ("yes" or "no") for the call they saw, the framework's way.
        Return what a resume needs (JSON-safe), e.g. {"resume": "yes"}. In the call4agents variant, also
        record it with call4agents (c4a.approve / c4a.deny)."""
        raise NotImplementedError

    def resume_approved(self, env: "Env", message: Dict[str, Any]) -> None:
        """Continue run env.run_id after a decision, with a message decide() returned (maybe another
        run's, in S8). If the run stops to wait for a new yes, save and return: nobody gives a second one."""
        raise NotImplementedError

    def tamper(self, env: "Env", new_args: Dict[str, Any]) -> None:
        """S4 only: change the arguments of the waiting call in the saved state, the way an edit, a bug or
        a write to the state store would. Use the framework's own state API where it has one."""
        raise NotImplementedError


class Env:
    """What the benchmark gives a subject in each process."""

    def __init__(self, work: str, run_id: str, kit: bool, call: Optional[Dict[str, Any]] = None,
                 repeat: int = 1) -> None:
        self.work = Path(work)
        self.run_id = run_id
        self.kit = kit
        self.call = dict(call or {"amount": 50, "to": "alice"})
        self.repeat = repeat
        self.fault = os.environ.get("BENCH_FAULT", "")
        self.lookup_mode = os.environ.get("BENCH_LOOKUP", "truth")
        self.delay = float(os.environ.get("BENCH_DELAY", "0") or 0)
        self._timeout_fired = False
        self._barrier()

    # --- the fake bank: an SQLite file outside every process
    def _bank(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.work / "bank.sqlite", timeout=30, isolation_level=None)
        db.execute("CREATE TABLE IF NOT EXISTS charges (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, "
                   "amount INTEGER, to_ TEXT, pid INTEGER, t REAL)")
        return db

    def pay(self, amount: int, to: str) -> str:
        """The outside action. One row in the bank per call. The benchmark's faults happen here."""
        if self.fault == "kill_before_action":
            os._exit(KILLED)
        if self.delay:
            time.sleep(self.delay)         # S3: a slow bank, so two workers overlap
        db = self._bank()
        try:
            db.execute("INSERT INTO charges (run_id, amount, to_, pid, t) VALUES (?, ?, ?, ?, ?)",
                       (self.run_id, int(amount), str(to), os.getpid(), time.time()))
        finally:
            db.close()
        if self.fault == "kill_in_action":
            os._exit(KILLED)               # the bank charged; the process died before the call returned
        if self.fault == "timeout_in_action" and not self._timeout_fired:
            self._timeout_fired = True
            raise TimeoutError("the bank did not answer in time (it did charge)")
        return f"paid {amount} to {to}"

    def after_action(self) -> None:
        """Call right after pay() returned, inside the tool, outside any guard."""
        if self.fault == "kill_after_action":
            os._exit(KILLED)               # the action happened; the framework has not saved yet

    def lookup(self, amount: int, to: str) -> str:
        """The bank's status check: "charged", "not_charged", or "unknown" (it cannot tell yet; S7)."""
        if self.lookup_mode == "unknown":
            return "unknown"
        db = self._bank()
        try:
            n = db.execute("SELECT COUNT(*) FROM charges WHERE run_id = ? AND amount = ? AND to_ = ?",
                           (self.run_id, int(amount), str(to))).fetchone()[0]
        finally:
            db.close()
        return "charged" if n else "not_charged"

    # --- helpers for the call4agents variant
    def kit_init(self) -> Any:
        import call4agents as c4a
        return c4a.init(str(self.work / "c4a" / "record.sqlite"), secret=b"bench-only-secret-0001")

    def reconcile(self):
        """For @c4a.effect(reconcile=...): asks the bank what happened to an action in doubt."""
        import call4agents as c4a

        def check(amount: int, to: str) -> Any:
            state = self.lookup(amount, to)
            if state == "unknown":
                return c4a.UNKNOWN
            return f"paid {amount} to {to}" if state == "charged" else None
        return check

    # --- S3: start together
    def _barrier(self) -> None:
        go = os.environ.get("BENCH_BARRIER")
        if not go:
            return
        Path(f"{go}.ready.{os.getpid()}").write_text("ready")
        deadline = time.monotonic() + 60
        while not Path(go).exists() and time.monotonic() < deadline:
            time.sleep(0.005)

    # --- where a subject saves its state. Two runs can share one folder (S8), so every file is per run.
    def path(self, name: str) -> Path:
        return self.work / f"{self.run_id}.{name}"

    def save(self, name: str, value: Any) -> None:
        tmp = self.path(f"{name}.tmp")
        tmp.write_text(json.dumps(value), encoding="utf-8")
        os.replace(tmp, self.path(name))

    def load(self, name: str, default: Any = None) -> Any:
        p = self.path(name)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


class kit_run:
    """`with kit_run(env):` is c4a.run(env.run_id) in the call4agents variant and does nothing in the default."""

    def __init__(self, env: Env) -> None:
        self.env, self.cm = env, None

    def __enter__(self) -> "kit_run":
        if self.env.kit:
            import call4agents as c4a
            self.cm = c4a.run(self.env.run_id)
            self.cm.__enter__()
        return self

    def __exit__(self, *exc: Any) -> bool:
        if self.cm is not None:
            return bool(self.cm.__exit__(*exc))
        return False


def tool_output_kind(text: Any) -> str:
    """How the scripted models read a tool result: "paid", "error" or "other"."""
    s = str(text)
    if s.startswith("paid "):
        return "paid"
    low = s.lower()
    if "error" in low or "timeout" in low or "doubt" in low or "timed out" in low:
        return "error"
    return "other"


def next_move(outputs: list, repeat: int) -> bool:
    """The scripted model's rule. outputs: the tool results so far, oldest first. True: ask for pay again."""
    paid = sum(tool_output_kind(o) == "paid" for o in outputs)
    errors = sum(tool_output_kind(o) == "error" for o in outputs)
    if not outputs:
        return True
    last = tool_output_kind(outputs[-1])
    if last == "error":
        return errors < 2
    if last == "paid":
        return paid < repeat
    return False


def stderr_line(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()
