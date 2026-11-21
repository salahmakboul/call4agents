"""call4agents story. Run: python -m tests.test_story

ST1 a finished run says done for each action, and nothing reads as uncertain.
ST2 an action killed between its effect and its result (a real process kill) is "may or may not have happened",
    never "done", and the page gives the exact resolve commands for its key.
ST3 when later actions started after an uncertain one (a half-done change), the page says so and names them.
ST4 who approved what and when is shown; a no and a rule's refusal are shown, and say that nothing ran.
ST5 an action resolved by hand shows the person's note; a request still waiting for a person is shown.
ST6 --run shows one run only; an unknown run and a missing record fail (exit 1) and create no file.
ST7 it is read-only: the record file is byte-for-byte the same afterwards, and no secret file appears.
ST8 the page always says that steps never started are not listed.
ST9 a yes that expired before any action used it says so and that nothing ran; a used one does not."""
import hashlib
import io
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import call4agents as c4a
from call4agents.story import NOT_SHOWN, main, story

FAILED = []
SECRET = b"story-secret-01234"
ROOT = Path(__file__).resolve().parents[1]


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {str(detail)[:400]}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def run_main(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


CHILD = r'''
import os, sys
sys.path.insert(0, sys.argv[2])
import call4agents as c4a
c4a.init(sys.argv[1], secret=b"story-secret-01234")
bank = sys.argv[1] + ".bank"

@c4a.effect(name="shop.reserve")
def reserve(order):
    open(bank, "a").write("reserve " + order + "\n")
    if sys.argv[3] == "kill":
        os._exit(9)          # the effect happened; the result is never saved
    return "reserved"

with c4a.run("order-A-1"):
    reserve(order="A-1")
'''


def section(page, run_id):
    part = page.split(f"## Run {run_id}", 1)
    return part[1].split("## Run ", 1)[0] if len(part) == 2 else ""


def main_test():
    folder = tempfile.mkdtemp()
    record = str(Path(folder) / "r.sqlite")

    # ST2/ST3: a real kill after the effect, then a loop that carries on past the uncertain step
    child = Path(folder) / "child.py"
    child.write_text(CHILD)
    p = subprocess.run([sys.executable, str(child), record, str(ROOT), "kill"], capture_output=True, text=True)
    check("ST2 setup: the child was killed after the effect", p.returncode == 9 and
          Path(record + ".bank").read_text() == "reserve A-1\n", (p.returncode, p.stderr[-300:]))

    lg = c4a.init(record, secret=SECRET)

    @c4a.effect(name="shop.reserve")
    def reserve(order):
        return "reserved"

    @c4a.effect(name="shop.charge")
    def charge(order, cents):
        return "charged"

    @c4a.effect(approval=True, name="shop.refund")
    def refund(order, cents):
        return "refunded"

    @c4a.effect(name="shop.email")
    def email(order):
        return "sent"

    @c4a.effect(name="shop.discount", rules=c4a.Rules({"shop.discount": {"max": {"cents": 500}}}))
    def discount(order, cents):
        return "ok"

    with c4a.run("order-A-1"):
        try:
            reserve(order="A-1")
        except c4a.EffectInDoubt:
            pass                                    # an agent loop that hands the error to the model and goes on
        charge(order="A-1", cents=4900)
        email(order="A-1")

    with c4a.run("clean"):                          # ST1, ST4
        c4a.approve(refund, order="B-2", cents=1200)
        refund(order="B-2", cents=1200)
        c4a.deny(refund, order="B-2", cents=99999)
        try:
            discount(order="B-2", cents=9999)
        except c4a.RuleRefused:
            pass
        ticket = c4a.pause(refund, order="B-2", cents=300)
        c4a.decide(ticket, "approve", by="sam")
        refund(order="B-2", cents=300)

    with c4a.run("waiting"):                        # ST5: still waiting for a person
        c4a.pause(refund, order="C-3", cents=700)

    with c4a.run("by-hand"):                        # ST5: resolved by hand
        @c4a.effect(name="shop.ship")
        def ship(order):
            raise TimeoutError("carrier did not answer")
        try:
            ship(order="D-4")
        except TimeoutError:
            pass
    from call4agents.ops import find_key, open_record, resolve
    rec = open_record(record)
    resolve(rec, find_key(rec, [a for a in __import__("call4agents.ops", fromlist=["actions"]).actions(rec).values()
                                if a["run_id"] == "by-hand"][0]["key"][:12]), "done", "carrier site shows it shipped")
    lg.close() if hasattr(lg, "close") else None

    before = hashlib.sha256(Path(record).read_bytes()).hexdigest()
    secret_file = Path(folder) / "secret"
    had_secret = secret_file.exists()
    page = story(record)
    after = hashlib.sha256(Path(record).read_bytes()).hexdigest()

    clean = section(page, "clean")
    check("ST1 a finished run: each action done, nothing uncertain",
          clean.count("Done: it ran") == 2 and "may or may not" not in clean and "2 done" in clean, clean)

    half = section(page, "order-A-1")
    lines = half.splitlines()
    i = next((n for n, l in enumerate(lines) if "reserve(" in l and "was started" in l), None)
    block = "\n".join(lines[i:i + 6]) if i is not None else ""
    check("ST2 the killed action may or may not have happened, and is not called done",
          "may or may not have happened" in block and "Done:" not in block.split("charge(")[0], half)
    from call4agents.ops import actions as _actions
    key = [a for a in _actions(open_record(record)).values() if a["tool"] == "shop.reserve"][0]["key"][:12]
    check("ST2 the page gives the resolve commands for that key",
          f"call4agents resolve {record} {key} done" in half and f"call4agents resolve {record} {key} not-done" in half, half)
    check("ST2 the summary counts it as uncertain and asks a person",
          "1 may or may not have happened" in half and "A person must check" in half, half)
    check("ST3 the half-done change is named, with the later actions",
          "later actions still started" in half and "charge(" in half.split("later actions still started", 1)[1].splitlines()[0]
          and "email(" in half.split("later actions still started", 1)[1].splitlines()[0], half)
    check("ST3 a clean run has no half-done note", "later actions" not in clean, clean)

    check("ST4 who approved, and when", "approved this exact call (sam)" in clean and clean.count("A person approved") == 2, clean)
    check("ST4 a no is shown and nothing ran", "a person said no to refund(" in clean and "It will not run." in clean, clean)
    check("ST4 a rule's refusal is shown and nothing ran", "a rule refused discount(" in clean and "Nothing ran." in clean, clean)

    check("ST5 resolved by hand shows the note", "carrier site shows it shipped" in section(page, "by-hand")
          and "marked it done by hand" in section(page, "by-hand"), section(page, "by-hand"))
    check("ST5 a request waiting for a person is shown", "waiting for a person to approve or refuse refund(" in section(page, "waiting"),
          section(page, "waiting"))
    check("ST5 a decided request is not shown as waiting", "waiting for a person" not in clean, clean)

    code, one = run_main([record, "--run", "clean"])
    check("ST6 --run shows one run only", code == 0 and "## Run clean" in one and "## Run order-A-1" not in one, one[:300])
    code, out = run_main([record, "--run", "nope"])
    check("ST6 an unknown run fails", code == 1 and "no entries" in out, out)
    missing = str(Path(folder) / "typo.sqlite")
    code, out = run_main([missing])
    check("ST6 a missing record fails and no file is created", code == 1 and not Path(missing).exists(), out)
    check("ST6 bad usage exits 2", run_main([])[0] == 2 and run_main([record, "--run"])[0] == 2)

    check("ST7 the record is unchanged", before == after)
    check("ST7 no new secret file", secret_file.exists() == had_secret)

    check("ST8 the page says steps never started are not listed", page.rstrip().splitlines()[-2] == NOT_SHOWN, page[-300:])

    p = subprocess.run([sys.executable, "-m", "call4agents", "story", record, "--run", "clean"], capture_output=True,
                       text=True, cwd=str(ROOT), env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    check("ST6 the command line works", p.returncode == 0 and "## Run clean" in p.stdout, (p.returncode, p.stderr[-300:]))

    # ST9: approve(..., ttl=) that expired unused, next to one that was used in time
    import time
    record9 = str(Path(folder) / "r9.sqlite")
    c4a.init(record9, secret=SECRET)

    @c4a.effect(approval=True, name="shop.refund")
    def refund9(order, cents):
        return "refunded"
    with c4a.run("ttl"):
        c4a.approve(refund9, ttl=0.2, order="A-9", cents=900)
        c4a.approve(refund9, ttl=0.2, order="A-8", cents=800)
        refund9(order="A-8", cents=800)
        time.sleep(0.4)
        try:
            refund9(order="A-9", cents=900)
        except c4a.ApprovalRequired:
            pass
    page9 = story(record9)
    check("ST9 an expired, unused yes is shown as expired, with nothing run under it; the used one is not",
          page9.count("the yes expired at") == 1 and '"order": "A-9"' in page9.split("the yes expired")[0].splitlines()[-1]
          and "Nothing ran under it." in page9, page9)

    if FAILED:
        print(f"{len(FAILED)} story check(s) failed")
        return 1
    print("all story checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main_test())
