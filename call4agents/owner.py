# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Who is running an attempt, and can it still be running? (Fix 17)

An intent without a result means "crashed" OR "still running". Asking reconcile= while the owner is still
running gets a truthful "not done yet", and running the action again then pays twice. So before the kit asks
reconcile, it checks the owner the intent names:

- our pid (this process, or an earlier one with the same pid, e.g. a restarted container): running only while
  the call is in progress HERE, since only the owner of an attempt can run it;
- another process on this host: running while that pid is alive, until `lease` seconds after the intent
  (a reused pid counts as alive: the safe side);
- another host: running until `lease` seconds after the intent (how long one attempt can run at most), since
  we cannot see that machine;
- a record written before the owner was recorded: no check, as before.

Standard library only. Never sends a signal: on Windows os.kill(pid, 0) would END the process."""
import os
import socket
import sys
import threading
import time
import uuid
from typing import Any, Dict, Optional

TOKEN = uuid.uuid4().hex          # one per process: tells this process from a later one with the same pid
_ACTIVE: Dict[str, int] = {}      # intent keys whose function is running in this process -> callers
_LOCK = threading.Lock()


def me() -> Dict[str, Any]:
    return {"host": socket.gethostname(), "pid": os.getpid(), "token": TOKEN}


def started(intent: str) -> None:
    with _LOCK:
        _ACTIVE[intent] = _ACTIVE.get(intent, 0) + 1


def finished(intent: str) -> None:
    with _LOCK:
        n = _ACTIVE.get(intent, 0) - 1
        if n > 0:
            _ACTIVE[intent] = n
        else:
            _ACTIVE.pop(intent, None)


def pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5                 # access denied: it exists
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True                                     # cannot tell: safe side
            return code.value == 259                            # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)                                         # POSIX: signal 0 only checks
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def may_still_run(intent_key: str, intent: Optional[Dict[str, Any]], lease: float) -> Optional[str]:
    """None when the owner of this attempt is provably not running it any more; else why it may be."""
    if intent is None:
        return None
    owner = (intent.get("payload") or {}).get("owner")
    age = time.time() - float(intent.get("t") or 0)
    mine = me()
    if not isinstance(owner, dict):
        return None                                             # written before 0.8: as before, no owner check
    if owner.get("host") != mine["host"]:
        return None if age >= lease else f"it may still be running on {owner.get('host')} (lease {lease:g} s)"
    if owner.get("pid") == mine["pid"]:
        # Our pid: either this process, or an earlier one that had the same pid (a restarted container). Only the
        # owner of an attempt can be running it, so "in progress here" decides both cases; the token is kept in the
        # record for people reading it, not for this check (a token test could never change the answer).
        with _LOCK:
            running = intent_key in _ACTIVE
        return "it is still running in this process" if running else None
    if pid_alive(owner.get("pid")):
        return None if age >= lease else f"it may still be running in process {owner.get('pid')} (lease {lease:g} s)"
    return None
