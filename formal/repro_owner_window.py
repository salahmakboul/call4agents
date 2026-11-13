# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Same-process window after Fix 17 (found by the TLA+ model, config finding_threads_window).

The owner check counts a call of THIS process as running only between _owner.started() and _owner.finished().
started() is called after check() has returned, so for a short time the intent is committed but the call is
not yet marked as running. If thread A is paused in that gap (here: a 0.5 s sleep injected right after its claim,
standing in for the scheduler), thread B of the same process sees "owner gone", asks a truthful reconcile,
gets "not done yet", gives the attempt up and runs it; then A runs it too.

Run:  python formal/repro_owner_window.py <path to the repository>
Prints the outside world's payments; 2 payments = the window is real. Offline, no LLM call.
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1]))
import call4agents as c4a  # noqa: E402


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="c4a-owner-window-"))
    lg = c4a.init(str(work / "r.sqlite"), secret=b"s" * 16)
    bank = []
    first = threading.Event()
    real_claim = lg.claim

    def claim(run_id, key, payload=None):
        owned = real_claim(run_id, key, payload)
        if owned and not first.is_set():          # thread A only: pause between the commit and started()
            first.set()
            time.sleep(0.5)
        return owned

    lg.claim = claim

    def pay(amount):
        bank.append(amount)
        return "paid"

    def check(amount):
        return "paid" if bank else None           # truthful at the moment it is asked

    pay = c4a.effect(pay, name="w.pay", reconcile=check, wait=0.05)
    out = []

    def caller(delay):
        time.sleep(delay)
        with c4a.run("run-1"):
            try:
                out.append(pay(10))
            except Exception as exc:
                out.append(type(exc).__name__)

    threads = [threading.Thread(target=caller, args=(d,)) for d in (0.0, 0.1)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    kinds = [(e["kind"], (e["payload"] or {}).get("attempt")) for e in lg.entries()]
    print("1 threads, injected pause: payments:", bank, "| callers:", out)
    print("  record:", kinds)
    return len(bank)


def main_async() -> int:
    """No change to the library at all: an async call whose event loop is blocked by another task (a
    blocking time.sleep, a common mistake) right after its check() returned, and a thread of the same
    process that calls the same action meanwhile."""
    import asyncio
    work = Path(tempfile.mkdtemp(prefix="c4a-owner-window-async-"))
    lg = c4a.init(str(work / "r.sqlite"), secret=b"s" * 16)
    bank = []

    async def apay(amount):
        bank.append(amount)
        return "paid"

    def spay(amount):
        bank.append(amount)
        return "paid"

    def check(amount):
        return "paid" if bank else None

    apay = c4a.effect(apay, name="w.pay2", reconcile=check, wait=0.05)
    spay = c4a.effect(spay, name="w.pay2", reconcile=check, wait=0.05)   # same tool name: the same action
    out = []

    def other_thread():
        time.sleep(0.2)
        with c4a.run("run-2"):
            try:
                out.append(spay(10))
            except Exception as exc:
                out.append(type(exc).__name__)

    async def blocker():
        await asyncio.sleep(0)
        time.sleep(0.8)                            # blocks the event loop

    async def both():
        with c4a.run("run-2"):
            t = threading.Thread(target=other_thread)
            t.start()
            results = await asyncio.gather(apay(10), blocker(), return_exceptions=True)
            out.append(results[0] if not isinstance(results[0], Exception) else type(results[0]).__name__)
            await asyncio.to_thread(t.join)

    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        asyncio.run(both())
    kinds = [(e["kind"], (e["payload"] or {}).get("attempt")) for e in lg.entries()]
    print("2 asyncio, blocked loop:   payments:", bank, "| callers:", out)
    print("  record:", kinds)
    return len(bank)


if __name__ == "__main__":
    paid = [main(), main_async()]
    sys.exit(0 if max(paid) <= 1 else 1)
