"""Idempotency keys from agent effects to outside APIs (offline, 0 calls).

A fake payment API (stdlib http.server) removes duplicate requests that carry the same
Idempotency-Key within a lifetime, like Stripe v1. A REAL process is killed in the middle of a
payment (the API charged, our record has no result), then a new process resumes.
Run: python -m tests.test_idempotency"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILED = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


class FakePayments(BaseHTTPRequestHandler):
    charges, seen, keys = [], {}, []          # seen: key -> (time, response)
    ttl = 24 * 3600

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        key = self.headers.get("Idempotency-Key")
        if key:
            FakePayments.keys.append(key)
        hit = FakePayments.seen.get(key) if key else None
        if hit and time.time() - hit[0] <= FakePayments.ttl:
            resp = hit[1]                                   # duplicate: same answer, no new charge
        else:
            resp = {"id": f"ch_{len(FakePayments.charges) + 1}", "amount": body["amount"]}
            FakePayments.charges.append(resp)
            if key:
                FakePayments.seen[key] = (time.time(), resp)
        out = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def child(mode: str, url: str, ledger: str, amount: str, idem: str, crash: str) -> None:
    """One agent process. mode: none (no kit) | plain (kit, no key) | idem (kit + key)."""
    sys.path.insert(0, str(ROOT))
    import call4agents as c4a

    def charge(amount: int, to: str) -> dict:
        headers = {"Content-Type": "application/json"}
        if mode == "idem":
            headers.update(c4a.idempotency_headers())
        req = urllib.request.Request(url, data=json.dumps({"amount": amount, "to": to}).encode(), headers=headers)
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
        if crash == "1":
            os._exit(17)                               # the API charged; our record has no result
        return resp

    if mode != "none":
        c4a.init(ledger, secret=b"s")
        opts = {"wait": 0.2}
        if mode == "idem":
            opts["idempotent_for"] = float(idem)
        charge = c4a.effect(charge, **opts)
    try:
        with c4a.run("order-42") if mode != "none" else _null():
            r = charge(amount=int(amount), to="alice")
        print("RESULT", json.dumps(r))
    except Exception as exc:  # EffectInDoubt and friends
        print("RESULT", type(exc).__name__)


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def run_child(*args, crash="0"):
    p = subprocess.run([sys.executable, "-m", "tests.test_idempotency", "child", *args, crash], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    lines = [l for l in p.stdout.splitlines() if l.startswith("RESULT")]
    return lines[-1][7:] if lines else ("crashed" if p.returncode == 17 else p.stderr.strip()[-120:])


def scenario(mode, idem="86400", amount="10", ttl_sleep=0.0):
    FakePayments.charges.clear(); FakePayments.seen.clear(); FakePayments.keys.clear()
    ledger = str(Path(tempfile.mkdtemp(prefix="c4a-idem-")) / "l.sqlite")
    first = run_child(mode, URL, ledger, amount, idem, crash="1")
    if ttl_sleep:
        time.sleep(ttl_sleep)
    second = run_child(mode, URL, ledger, amount, idem)
    return first, second, len(FakePayments.charges), list(FakePayments.keys)


def main():
    global URL
    server = HTTPServer(("127.0.0.1", 0), FakePayments)
    URL = f"http://127.0.0.1:{server.server_address[1]}/charges"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        f, s, n, _ = scenario("none")
        check("F1 without the kit: crash during the payment, resume -> 2 charges (the problem)", n == 2, (f, s, n))
        f, s, n, _ = scenario("plain")
        check("F2 kit without a key: 1 charge, the resume is flagged in doubt (safe but needs a human)",
              n == 1 and s == "EffectInDoubt", (s, n))
        f, s, n, keys = scenario("idem")
        same_key = len(keys) == 2 and keys[0] == keys[1]
        check("F3 kit + idempotency key: 1 charge, the resume gets the SAME charge back, no human needed",
              n == 1 and same_key and s.startswith("{") and json.loads(s)["id"] == "ch_1", (s, n, keys))
        check("F3b the key is the same in both processes and fits Adyen's 64-character limit",
              same_key and len(keys[0]) <= 64, keys)
        f, s, n, _ = scenario("idem", idem="0.3", ttl_sleep=0.6)
        check("F4 key lifetime over: no blind retry (it could charge twice) -> still in doubt, 1 charge",
              n == 1 and s == "EffectInDoubt", (s, n))
        # F5 different arguments are a different action: a different key
        FakePayments.charges.clear(); FakePayments.seen.clear(); FakePayments.keys.clear()
        ledger = str(Path(tempfile.mkdtemp(prefix="c4a-idem-")) / "l.sqlite")
        run_child("idem", URL, ledger, "10", "86400")
        run_child("idem", URL, ledger, "11", "86400")
        check("F5 a different amount gets a different key (2 real charges)",
              len(FakePayments.charges) == 2 and len(set(FakePayments.keys)) == 2, FakePayments.keys)
    finally:
        server.shutdown()
    print(f"\n{len(FAILED)} idempotency check(s) failed" if FAILED else "\nall idempotency checks pass")
    return 1 if FAILED else 0


URL = ""
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        child(*sys.argv[2:8])
    else:
        sys.exit(main())
