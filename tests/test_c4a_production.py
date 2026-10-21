"""Production checks for the kit (offline, 0 LLM calls). Run: python -m tests.test_c4a_production

P1-P13: record format and versions, bad files, secret creation races and rotation, network drives,
unsafe decorations, late results, the on-call commands (doubt / resolve / archive / verify),
unrecordable results, many threads, and a forked child (Linux/macOS only)."""

import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import warnings
from pathlib import Path

import call4agents as c4a
from call4agents import ops
from call4agents.ledger import Ledger, LedgerError, _on_network_drive, effect_key, intent_key

ROOT = Path(__file__).resolve().parents[1]
FAILED = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def tmp(name="l.sqlite"):
    return str(Path(tempfile.mkdtemp(prefix="c4a-prod-")) / name)


def raises(exc, fn):
    """The message if fn raised `exc`; None if it did not raise; "WRONG <type>: ..." for another error
    (so a check FAILS with a reason instead of crashing the suite)."""
    try:
        fn()
        return None
    except exc as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"WRONG {type(e).__name__}: {e}"


def main():
    warnings.simplefilter("ignore")
    # P1 a record written by a NEWER kit is refused with a clear message (never misread)
    p = tmp()
    Ledger(p, secret=b"x" * 16)
    con = sqlite3.connect(p)
    con.execute("UPDATE meta SET v = '99' WHERE k = 'schema_version'")
    con.commit()
    con.close()
    msg = raises(LedgerError, lambda: Ledger(p, secret=b"x" * 16))
    check("P1 a record from a newer format is refused, saying to upgrade", msg is not None and "newer" in msg, msg)

    # P2 a file that is not a run record gives a clear error, not a crash deep inside
    p = tmp()
    Path(p).write_bytes(b"this is not sqlite" * 100)
    msg = raises(LedgerError, lambda: Ledger(p, secret=b"x" * 16))
    check("P2 a non-record file gives a clear LedgerError", msg is not None and "not a usable" in msg
          and not msg.startswith("WRONG"), msg)

    # P3 12 processes start at the same moment on a fresh folder (3 rounds): they all use ONE complete secret
    code = ("import sys, hashlib; sys.path.insert(0, sys.argv[2]); from call4agents.ledger import Ledger; "
            "s = Ledger(sys.argv[1]).secret; print(len(s), hashlib.sha256(s).hexdigest())")
    rounds3 = []
    for _ in range(3):
        d = Path(tempfile.mkdtemp(prefix="c4a-secret-"))
        procs = [subprocess.Popen([sys.executable, "-c", code, str(d / "l.sqlite"), str(ROOT)], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True,
                                  env={k: v for k, v in os.environ.items() if k not in ("C4A_SECRET", "C4A_OLD_SECRETS")})
                 for _ in range(12)]
        outs = [pr.communicate(timeout=120) for pr in procs]
        rounds3.append(({o.strip() for o, _ in outs}, [e.strip().splitlines()[-1] for _, e in outs if e.strip()][:1],
                        sorted(f.name for f in d.iterdir() if f.name.startswith(".secret."))))
    check("P3 12 processes creating the secret at once (x3) all get the same 32-byte secret; no temp files left",
          all(len(digs) == 1 and next(iter(digs)).startswith("32 ") and not errs and not left for digs, errs, left in rounds3),
          rounds3)

    # P4 secret rotation: a yes signed with the OLD secret counts only if the old secret is listed
    p = tmp()
    old, new = b"old-secret-123456", b"new-secret-654321"
    c4a.init(p, secret=old)

    @c4a.effect(approval=True, name="prod.pay")
    def pay(amount, to):
        paid.append((amount, to))
        return "paid"
    paid = []
    with c4a.run("rot"):
        c4a.approve(pay, amount=5, to="a")
    c4a.init(p, secret=new)
    with c4a.run("rot"):
        refused = raises(c4a.ApprovalRequired, lambda: pay(amount=5, to="a")) is not None
    c4a.init(p, secret=new, old_secrets=[old])
    with c4a.run("rot"):
        third = raises(c4a.ApprovalRequired, lambda: pay(amount=5, to="a"))
    check("P4 after a secret change, old approvals count only when the old secret is listed",
          refused and third is None and paid == [(5, "a")], (refused, third, paid))

    # P5 network drives are detected (SQLite locking is not reliable there)
    sample = ("/dev/sda1 / ext4 rw 0 0" + chr(10) + "server:/exports /mnt/nfs nfs4 rw 0 0" + chr(10)
              + "//nas/share /mnt/smb cifs rw 0 0" + chr(10) + "tmpfs /tmp tmpfs rw 0 0")
    by_mounts = (_on_network_drive(Path("/mnt/nfs/app/c4a"), sample), _on_network_drive(Path("/mnt/smb/x"), sample),
                 _on_network_drive(Path("/home/app/c4a"), sample), _on_network_drive(Path("/tmp/c4a"), sample))
    local = _on_network_drive(Path(tempfile.gettempdir()))
    unc = _on_network_drive(Path("//fileserver/share/c4a")) if sys.platform == "win32" else True
    check("P5 network drives are detected (Windows share; NFS/SMB mounts by type), local disks are not",
          by_mounts == (True, True, False, False) and not local and unc, (by_mounts, local, unc))

    # P6 unsafe decorations fail at once, with a reason
    def gen(x):
        yield x
    g = raises(TypeError, lambda: c4a.effect(gen))
    lam = raises(TypeError, lambda: c4a.effect(lambda x: x))
    check("P6 a generator and an unnamed lambda are refused when decorated", g is not None and lam is not None
          and "generator" in g and "name=" in lam, (g, lam))

    # P7 a result that arrives AFTER its attempt was given up is recorded and reported, never hidden
    p = tmp()
    lg = c4a.init(p, secret=b"y" * 16)
    gate = threading.Event()

    @c4a.effect(name="prod.slow")
    def slow(to):
        gate.wait(10)
        return "sent"
    caught = []

    def run_slow():
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with c4a.run("late"):
                slow("bob")
            caught.extend(str(x.message) for x in w)
    th = threading.Thread(target=run_slow)
    th.start()
    k = effect_key("late", "prod.slow", {"to": "bob"})
    for _ in range(200):
        if lg.count("tool_intent", intent_key(k, 1)):
            break
        threading.Event().wait(0.01)
    ops.resolve(lg, k[:12], "not-done", "operator thought it failed")
    gate.set()
    th.join()
    states = {a["key"]: a["state"] for a in ops.actions(lg).values()}
    check("P7 a result after 'not done' is flagged LATE (may have happened twice), with a warning",
          "LATE" in states.get(k, "") and any("AFTER" in m for m in caught), (states.get(k), caught))

    # P8 on-call: doubt lists an in-doubt action; resolve not-done lets it run; resolve done returns the note's result
    p = tmp()
    lg = c4a.init(p, secret=b"z" * 16)
    sent = []

    @c4a.effect(name="prod.mail", wait=0.05)
    def mail(to):
        sent.append(to)
        return "sent"
    for who in ("ann", "bob"):
        lg.claim("night", intent_key(effect_key("night", "prod.mail", {"to": who}), 1), {"tool": "prod.mail", "args": {"to": who}})
    listed = {a["args"]["to"] for a in ops.in_doubt(lg)}
    alert_before = subprocess.run([sys.executable, "-m", "call4agents", "doubt", p, "--fail-if-any"], cwd=ROOT,
                                  capture_output=True, text=True).returncode
    ops.resolve(lg, effect_key("night", "prod.mail", {"to": "ann"})[:12], "not-done", "provider says not sent")
    ops.resolve(lg, effect_key("night", "prod.mail", {"to": "bob"})[:12], "done", "provider log shows it")
    with c4a.run("night"):
        for who in ("ann", "bob"):
            raises(c4a.EffectInDoubt, lambda: mail(who))
    alert_after = subprocess.run([sys.executable, "-m", "call4agents", "doubt", p, "--fail-if-any"], cwd=ROOT,
                                 capture_output=True, text=True).returncode
    check("P8 doubt lists both (alert exit 3); 'not-done' runs ann once; 'done' never resends bob; then empty (exit 0)",
          listed == {"ann", "bob"} and sent == ["ann"] and ops.in_doubt(lg) == [] and (alert_before, alert_after) == (3, 0),
          (listed, sent, ops.in_doubt(lg), alert_before, alert_after))

    # P9 the on-call commands never create a record by mistake
    missing = tmp("typo.sqlite")
    rc = subprocess.run([sys.executable, "-m", "call4agents", "verify", missing], cwd=ROOT, capture_output=True, text=True)
    check("P9 'verify' on a missing file fails clearly and creates nothing",
          rc.returncode == 1 and "does not exist" in rc.stderr and not Path(missing).exists(), (rc.returncode, rc.stderr[-120:]))

    # P10 archive moves finished old runs, keeps runs with anything in doubt, loses no row
    p, dest = tmp(), tmp("archive.sqlite")
    lg = c4a.init(p, secret=b"w" * 16)

    @c4a.effect(name="prod.note")
    def note(i):
        return i
    for r in ("old-1", "old-2"):
        with c4a.run(r):
            note(1)
            note(2)
    lg.claim("old-doubt", intent_key(effect_key("old-doubt", "prod.note", {"i": 9}), 1), {"tool": "prod.note", "args": {"i": 9}})
    before = len(lg.entries())
    con = sqlite3.connect(p)
    con.execute("UPDATE entries SET t = t - 40 * 86400")           # make everything 40 days old
    con.commit()
    con.close()
    res = ops.archive(p, dest, 30)
    after_src, after_dst = len(Ledger(p, secret=b"w" * 16).entries()), len(Ledger(dest, secret=b"w" * 16).entries())
    runs_left = {e["run_id"] for e in Ledger(p, secret=b"w" * 16).entries()}
    check("P10 archive moves 2 finished runs, keeps the in-doubt run, and no row is lost",
          res["runs_moved"] == 2 and runs_left == {"old-doubt"} and after_src + after_dst == before
          and ops.verify(p)["integrity"] == "ok", (res, runs_left, before, after_src, after_dst))

    # P11 a result that cannot be recorded: in doubt at once, with the reason
    lg = c4a.init(tmp(), secret=b"v" * 16)

    class Opaque:
        pass

    @c4a.effect(name="prod.opaque", wait=0.05)
    def make(i):
        return Opaque()
    with c4a.run("nr"):
        first = raises(TypeError, lambda: make(1))
        second = raises(c4a.EffectInDoubt, lambda: make(1))
    check("P11 an unrecordable result fails loudly; the retry is in doubt naming it (no silent resend)",
          first is not None and second is not None and "could not be recorded" in second, (first, second))

    # P12 8 threads x 150 different actions on one record: every action recorded exactly once
    lg = c4a.init(tmp(), secret=b"u" * 16)
    done = []

    @c4a.effect(name="prod.work")
    def work(t, i):
        done.append((t, i))
        return i

    errors12 = []

    def worker(t):
        try:
            with c4a.run("threads"):
                for i in range(150):
                    work(t, i)
                    work(t, i)                               # the repeat must not run again
        except Exception as e:  # noqa: BLE001
            errors12.append(repr(e)[:80])
    ths = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    check("P12 8 threads x 150 actions (each called twice): 1200 runs, 1200 results, integrity ok",
          not errors12 and len(done) == 1200 and len(set(done)) == 1200 and lg.count_run_kind("threads", "tool_done") == 1200
          and ops.verify(lg.path.__str__())["integrity"] == "ok", (len(done), errors12[:2]))

    # P14 arguments without a stable text form are refused (a socket prints its file number: the key would move)
    import socket
    lg = c4a.init(tmp(), secret=b"s" * 16)
    echo = c4a.effect(lambda **kw: "ok", name="prod.echo")
    with c4a.run("args"):
        sock = socket.socket()
        try:
            m_sock = raises(TypeError, lambda: echo(s=sock))
        finally:
            sock.close()
        deep = []
        for _ in range(600):                          # above the kit's 500-level limit, same on every system
            deep = [deep]
        m_deep = raises(TypeError, lambda: echo(d=deep))
        fine = []
        for _ in range(100):
            fine = [fine]
        m_fine = raises(TypeError, lambda: echo(d=fine))
        import datetime as _dt, decimal as _dec, enum as _enum, uuid as _uuid
        Color = _enum.Enum("Color", "RED BLUE")
        ok_types = raises(Exception, lambda: echo(t=(1, 2), b=b"x", e=Color.RED, d=_dt.date(2026, 1, 1),
                                                  m=_dec.Decimal("1.5"), u=_uuid.UUID(int=1), p=Path("a/b")))
    check("P14 a socket and a 600-deep argument are refused clearly; 100-deep and tuple/bytes/enum/date/Decimal/UUID/Path work",
          m_sock is not None and "stable" in m_sock and m_deep is not None and "nested" in m_deep and ok_types is None
          and m_fine is None, (m_sock, m_deep, ok_types, m_fine))

    # P15 a damaged row names the record and the row, instead of a bare JSON error
    p15 = tmp()
    lg = c4a.init(p15, secret=b"s" * 16)
    k15 = effect_key("dmg", "prod.dmg", {"x": 1})
    con = sqlite3.connect(p15)
    con.execute("INSERT INTO entries (run_id, kind, key, payload, t) VALUES ('dmg', 'tool_done', ?, '{oops', 0)", (k15,))
    con.commit()
    con.close()
    dmg = c4a.effect(lambda x: "ok", name="prod.dmg")
    with c4a.run("dmg"):
        m15 = raises(LedgerError, lambda: dmg(x=1))
    check("P15 a damaged row gives a LedgerError naming the row and the record",
          m15 is not None and "damaged" in m15 and "row" in m15 and not m15.startswith("WRONG"), m15)

    # P17 the kit says "Python 3.10 or newer" but only 3.12 is installed here: scan the source for syntax and
    # library names that do not exist on 3.10, after proving the scan finds a planted example
    def py310_problems(text, where):
        import io
        import re as _re
        import tokenize
        out, stack = [], []
        if hasattr(tokenize, "FSTRING_START"):              # 3.12+: f-strings are tokenized; same-quote nesting is 3.12-only
            for t in tokenize.generate_tokens(io.StringIO(text).readline):
                if t.type == tokenize.FSTRING_START:
                    stack.append(t.string.lstrip("rRfFbBuU")[0])
                elif t.type == tokenize.FSTRING_END:
                    stack.pop()
                elif stack and t.type == tokenize.STRING and t.string.lstrip("rRbBuU")[0] == stack[-1]:
                    out.append(f"{where}:{t.start[0]} f-string reuses its own quote (3.12+)")
        for name in ("tomllib", "ExceptionGroup", "except*", "TaskGroup", "datetime.UTC", "itertools.batched",
                     "typing.Self", "from typing import Self", "@override"):
            for m in _re.finditer(_re.escape(name), text):
                out.append(f"{where}:{text[:m.start()].count(chr(10)) + 1} uses {name} (3.11+)")
        return out
    planted = py310_problems('d = {}' + chr(10) + 'x = f"{d["k"]}"' + chr(10) + 'import tomllib' + chr(10), "planted")
    found17 = [x for f in sorted((ROOT / "call4agents").rglob("*.py")) for x in py310_problems(f.read_text(encoding="utf-8"), f.name)]
    can_see = len(planted) == 2 or (not hasattr(__import__("tokenize"), "FSTRING_START") and len(planted) == 1)
    check("P17 no 3.11+/3.12+ syntax or library names in the kit (the scan finds both planted examples)",
          can_see and not found17, (planted, found17))

    # P18 `report` and `doctor` only print: run them with every network connection made impossible
    no_net = ("import socket, runpy, sys" + chr(10)
              + "def refuse(*a, **k): raise RuntimeError('NETWORK USED')" + chr(10)
              + "socket.socket.connect = refuse; socket.create_connection = refuse; socket.getaddrinfo = refuse" + chr(10)
              + "sys.argv = ['call4agents', sys.argv[1]]" + chr(10)
              + "runpy.run_module('call4agents', run_name='__main__')")
    runs18 = [subprocess.run([sys.executable, "-c", no_net, cmd], cwd=ROOT, capture_output=True, text=True, timeout=120)
              for cmd in ("report", "doctor")]
    link = [l for l in runs18[0].stdout.splitlines() if l.startswith("https://")]
    from urllib.parse import parse_qs, urlparse
    q18 = parse_qs(urlparse(link[0]).query) if link else {}
    check("P18 report and doctor work with the network blocked (they send nothing); the link pre-fills the issue form",
          all(r.returncode == 0 and "NETWORK USED" not in r.stderr for r in runs18)
          and q18.get("template") == ["feedback.yml"] and "python" in (q18.get("doctor") or [""])[0],
          ([r.returncode for r in runs18], runs18[0].stderr[-200:], list(q18)))

    # P13 a forked child does not reuse the parent's database connection (Linux/macOS)
    if not hasattr(os, "fork"):
        print("  skip  P13 no fork() on this platform")
    else:
        lg = c4a.init(tmp(), secret=b"t" * 16)
        forked = []

        @c4a.effect(name="prod.fork")
        def fk(i):
            return i
        with c4a.run("fork"):
            fk(0)                                             # parent opens its connection
            pid = os.fork()
            if pid == 0:
                try:
                    with c4a.run("fork"):
                        fk(1)
                    os._exit(0)
                except BaseException:
                    os._exit(3)
            _, status = os.waitpid(pid, 0)
            fk(2)
        check("P13 parent and forked child both write through their own connections",
              os.WEXITSTATUS(status) == 0 and lg.count_run_kind("fork", "tool_done") == 3, os.WEXITSTATUS(status))

    print(f"\n{len(FAILED)} production check(s) failed" if FAILED else "\nall production checks pass")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
