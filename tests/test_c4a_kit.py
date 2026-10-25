"""Call4Agents v0.1 kit - offline checks (0 calls). Run: python -m tests.test_c4a_kit"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import call4agents as c4a

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        FAILED.append(name)


def fresh():
    return c4a.init(str(Path(tempfile.mkdtemp(prefix="c4a-test-")) / "ledger.sqlite"), secret=b"test-secret")


def main():
    sent = []

    # K1 once-only: the same call in the same run returns the recorded result, effect runs once
    fresh()

    @c4a.effect
    def send(to, body="x"):
        sent.append(to)
        return f"sent to {to}"
    with c4a.run("r1"):
        a, b = send("bob"), send(to="bob", body="x")
    check("K1 a repeated call (same run, same args) runs the effect once", sent == ["bob"] and a == b, sent)
    with c4a.run("r2"):
        send("bob")
    check("K1b another run is another effect", sent == ["bob", "bob"], sent)

    # K2 in doubt: intent recorded, no result (crash during the effect) -> refuse, never repeat silently
    lg = fresh()
    sent.clear()

    @c4a.effect
    def mail(to):
        sent.append(to)
        return "ok"
    from call4agents.ledger import effect_key, intent_key
    lg.append("r1", "tool_intent", intent_key(effect_key("r1", mail.c4a_name, {"to": "bob"})), {"tool": "mail"})
    try:
        with c4a.run("r1"):
            mail("bob")
        doubt = False
    except c4a.EffectInDoubt:
        doubt = True
    check("K2 a crash during the effect is reported in doubt, not repeated", doubt and sent == [], sent)

    # K3 reconcile decides: found -> recorded, not repeated
    lg = fresh()
    sent.clear()

    @c4a.effect(reconcile=lambda to: "already sent")
    def mail2(to):
        sent.append(to)
        return "ok"
    lg.append("r1", "tool_intent", intent_key(effect_key("r1", mail2.c4a_name, {"to": "bob"})), {"tool": "mail2"})
    with c4a.run("r1"):
        got = mail2("bob")
    check("K3 reconcile() finding the effect stops a repeat", got == "already sent" and sent == [], (got, sent))

    # K4 approvals bound to exact arguments (key order does not matter, values do)
    fresh()
    paid = []

    @c4a.effect(approval=True)
    def pay(amount, to):
        paid.append((amount, to))
        return "paid"
    with c4a.run("r1"):
        c4a.approve(pay, to="alice", amount=10)
        try:
            pay(9999, "mallory")
            refused = False
        except c4a.ApprovalRequired:
            refused = True
        pay(amount=10, to="alice")
    check("K4 a changed payment after the yes is refused; the approved one runs",
          refused and paid == [(10, "alice")], paid)

    # K5 forging an approval without the secret fails
    lg = fresh()
    paid.clear()
    with sqlite3.connect(lg.path) as conn:
        conn.execute("INSERT INTO entries (run_id, kind, key, payload, t) VALUES (?, 'approval', ?, ?, 0)",
                     ("r1", "r1\x00pay", json.dumps({"mac": "0" * 64, "args": {"amount": 9999, "to": "mallory"}})))
    try:
        with c4a.run("r1"):
            pay(9999, "mallory")
        forged = True
    except c4a.ApprovalRequired:
        forged = False
    check("K5 a hand-written approval entry without the secret is refused", not forged and paid == [], paid)

    # K8 explicit key: a deliberate second identical action runs; the same key does not repeat
    fresh()
    sent.clear()

    @c4a.effect(wait=0.1)
    def remind(to):
        sent.append(to)
        return "ok"
    with c4a.run("r1"):
        remind("bob")
        remind("bob", c4a_key="second reminder")
        remind("bob", c4a_key="second reminder")
        remind("bob")
    check("K8 c4a_key allows a deliberate second identical action, once per key", sent == ["bob", "bob"], sent)

    # K9 race: the same effect from 10 threads at once runs exactly once
    import threading
    import time as _t
    fresh()
    hits, outcomes = [], []

    @c4a.effect
    def slow_send(to):
        hits.append(to)
        _t.sleep(0.05)
        return "sent"

    def caller():
        with c4a.run("race"):
            try:
                outcomes.append(slow_send("bob"))
            except c4a.EffectInDoubt:
                outcomes.append("in_doubt")
    ts = [threading.Thread(target=caller) for _ in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("K9 10 simultaneous callers: the effect runs exactly once, everyone gets its result",
          len(hits) == 1 and outcomes == ["sent"] * 10, (len(hits), outcomes))

    # K10 record, then replay offline: same answers, no model call, no effect re-run
    import tempfile as _tf
    from pathlib import Path as _P
    d = _P(_tf.mkdtemp(prefix="c4a-replay-"))
    calls, mails = [], []

    def make(model_answer):
        @c4a.recorded
        def ask_model(prompt):
            calls.append(prompt)
            return model_answer

        @c4a.effect
        def email(to):
            mails.append(to)
            return "sent"
        return ask_model, email

    c4a.init(str(d / "rec.sqlite"), secret=b"s")
    ask, email = make("email bob")
    with c4a.run("t1"):
        plan = ask("what next?")
        email("bob")
    c4a.init(str(d / "replay.sqlite"), secret=b"s", replay_from=str(d / "rec.sqlite"))
    ask, email = make("SHOULD NOT BE CALLED")
    with c4a.run("t1"):
        plan2 = ask("what next?")
        r = email("bob")
    check("K10 replay returns the recording offline: 1 model call, 1 email in total",
          plan2 == plan and len(calls) == 1 and mails == ["bob"] and r == "sent", (plan2, calls, mails))
    c4a.init(str(d / "replay2.sqlite"), secret=b"s", replay_from=str(d / "rec.sqlite"))   # fresh cursor
    ask, email = make("SHOULD NOT BE CALLED")
    try:
        with c4a.run("t1"):
            ask("a changed prompt")             # call #1 exists, its arguments differ
        outcome = "answered"
    except c4a.ReplayMismatch:
        outcome = "mismatch"
    except c4a.ReplayMissing:
        outcome = "missing"
    check("K10b a changed call in replay is reported as a mismatch, not answered", outcome == "mismatch", outcome)

    # K11 diff: the same task with another model shows the first difference
    c4a.init(str(d / "model_b.sqlite"), secret=b"s")
    ask, email = make("email carol")
    with c4a.run("t1"):
        ask("what next?")
        email("carol")
    same = c4a.diff(str(d / "rec.sqlite"), str(d / "replay.sqlite"), "t1")
    changed = c4a.diff(str(d / "rec.sqlite"), str(d / "model_b.sqlite"), "t1")
    check("K11 diff: replay equals the recording; another model differs at step 1 (its answer)",
          same.get("same") and not changed["same"] and changed["first_difference"] == 1
          and changed["field"] == "result", (same, changed))

    # K12 prove: a real guard is caught, a decorative one is MISSED, the control passes
    real = c4a.prove("tests.c4a_prove_sample:real_guard",
                     [{"module": "tests.c4a_prove_sample", "old": "if amount <= 0:", "new": "if False:"}])
    deco = c4a.prove("tests.c4a_prove_sample:decorative_guard",
                     [{"module": "tests.c4a_prove_sample", "old": "if amount <= 0:", "new": "if False:"}])
    check("K12 prove: real guard proven, decorative guard not proven, control passes",
          real["proven"] and real["control"] == "PASSES" and not deco["proven"]
          and deco["mutations"][0]["result"] == "MISSED", (real, deco))

    # K13 replay keeps types: no silent conversion to text
    import datetime as _dtm
    import decimal as _dec
    from tests.c4a_codec_types import FakeModel, Order
    values = [(1, "a"), bytes([0, 255]), _dtm.datetime(2026, 10, 15, 20, 0), _dec.Decimal("10.50"), {1: "x"},
              {"n", "m"}, Order(7, ("tea",)), FakeModel("hi", 3), {"plain": [1, 2.5, None, True]}]
    d2 = _P(_tf.mkdtemp(prefix="c4a-types-"))
    c4a.init(str(d2 / "rec.sqlite"), secret=b"s")
    produced = []

    @c4a.recorded
    def model_call(i):
        produced.append(i)
        return values[i]
    with c4a.run("types"):
        for i in range(len(values)):
            model_call(i)
    c4a.init(str(d2 / "rep.sqlite"), secret=b"s", replay_from=str(d2 / "rec.sqlite"))
    with c4a.run("types"):
        back = [model_call(i) for i in range(len(values))]
    same = [type(x) is type(y) and x == y for x, y in zip(values, back)]
    check("K13 replay returns the same type and value for 9 kinds of result (tuple, bytes, datetime, Decimal, "
          "int-key dict, set, dataclass, model, JSON)", all(same) and len(produced) == len(values),
          [type(v).__name__ for v, ok in zip(values, same) if not ok])

    class Opaque:
        pass
    c4a.init(str(d2 / "rec2.sqlite"), secret=b"s")

    @c4a.recorded
    def opaque_call():
        return Opaque()
    try:
        with c4a.run("types"):
            opaque_call()
        loud = False
    except TypeError:
        loud = True
    check("K13b a type we cannot keep fails when RECORDED, not silently at replay", loud)

    # K14 an approval is scoped to its run: a "yes" in run A does not approve the same call in run B
    fresh()
    paid.clear()
    with c4a.run("run-A"):
        c4a.approve(pay, amount=10, to="alice")
    try:
        with c4a.run("run-B"):
            pay(amount=10, to="alice")
        crossed = True
    except c4a.ApprovalRequired:
        crossed = False
    check("K14 an approval in one run cannot be used in another run", not crossed and paid == [], paid)

    # K17 one "yes" = one action; a "no" is enforced, also for an action in doubt (research 2026-10:
    # Mastra #26133 double run on concurrent approvals, ADK #7148 deny not enforced)
    def refused(fn, **kw):
        try:
            fn(**kw)
            return False
        except c4a.ApprovalRequired:
            return True
    fresh()
    paid.clear()
    with c4a.run("k17"):
        c4a.approve(pay, amount=10, to="alice")
        pay(amount=10, to="alice")
        pay(amount=10, to="alice")                     # a retry of the SAME action: recorded result
        second = refused(pay, amount=10, to="alice", c4a_key="order-2")
        c4a.approve(pay, amount=10, to="alice")
        pay(amount=10, to="alice", c4a_key="order-2")  # a new "yes" covers the second action
    check("K17 one yes covers one action: a retry reuses it, a deliberate second action needs a new yes",
          second and paid == [(10, "alice"), (10, "alice")], (second, paid))

    fresh()
    paid.clear()
    with c4a.run("k17b"):
        c4a.approve(pay, amount=10, to="alice")
        c4a.deny(pay, amount=10, to="alice")
        no = refused(pay, amount=10, to="alice")
    check("K17b a no after the yes is enforced", no and paid == [], paid)

    def in_doubt_retry(say_no):
        lg = fresh()
        paid.clear()
        rid = "k17c-no" if say_no else "k17c-ctrl"
        with c4a.run(rid):                              # the process died DURING the approved payment
            c4a.approve(pay, amount=10, to="alice")
            from call4agents.ledger import effect_key as _ek, intent_key as _ik
            k = _ek(rid, pay.c4a_name, {"amount": 10, "to": "alice"})
            assert lg.use_approval(rid, pay.c4a_name, {"amount": 10, "to": "alice"}, k)
            lg.claim(rid, _ik(k, 1), {"tool": pay.c4a_name})
            if say_no:
                c4a.deny(pay, amount=10, to="alice")     # the human now says no
            pay_rc = c4a.effect(lambda amount, to: paid.append((amount, to)), approval=True, name=pay.c4a_name,
                                reconcile=lambda **a: None, wait=0.05)   # reconcile: "did not happen" -> retry
            return refused(pay_rc, amount=10, to="alice"), list(paid)
    stopped, paid_no = in_doubt_retry(True)
    ran, paid_ctrl = in_doubt_retry(False)
    check("K17c a no stops the retry of an in-doubt approved action (control: without the no it retries once)",
          stopped and paid_no == [] and not ran and paid_ctrl == [(10, "alice")], (stopped, paid_no, ran, paid_ctrl))
    import threading as _th
    fresh()
    paid.clear()
    with c4a.run("k17d"):
        c4a.approve(pay, amount=10, to="alice")
    outcomes = []

    def racer(i):
        with c4a.run("k17d"):
            outcomes.append("paid" if not refused(pay, amount=10, to="alice", c4a_key=f"click-{i}") else "refused")
    threads = [_th.Thread(target=racer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("K17d 8 simultaneous actions on ONE yes: exactly one runs", len(paid) == 1 and outcomes.count("paid") == 1,
          (paid, outcomes))

    # K18 one check per bug found by an outside tester
    import os as _os
    import subprocess as _sp
    import time as _time
    work = Path(tempfile.mkdtemp(prefix="c4a-k18-"))
    for seed in ("1", "2", "3"):                       # 3 processes, 3 hash seeds: the key must not move
        _sp.run([sys.executable, "-m", "tests.c4a_k18_child", str(work)], cwd=Path(__file__).resolve().parents[1],
                env={**_os.environ, "PYTHONHASHSEED": seed}, check=True, capture_output=True)
    lines = (work / "outbox.txt").read_text(encoding="utf-8").splitlines()
    check("K18a a method with a set argument, resumed in 3 new processes, sends once", len(lines) == 1, lines)

    fresh()
    out18 = []

    def send(to):
        out18.append(("slack", to))
        return "slack"
    send.__module__ = "slack_tools"
    slack_send = c4a.effect(send)

    def send(to):  # noqa: F811 - same function name, another module
        out18.append(("email", to))
        return "email"
    send.__module__ = "email_tools"
    email_send = c4a.effect(send)
    with c4a.run("k18b"):
        r18 = (slack_send("bob"), email_send("bob"))
    check("K18b two tools named `send` in different modules are two tools", r18 == ("slack", "email")
          and out18 == [("slack", "bob"), ("email", "bob")], (r18, out18))

    try:
        email_send("bob")
        outside = "ran"
    except RuntimeError:
        outside = "refused"
    lost = []

    def in_thread():
        try:
            email_send("zed")
            lost.append("ran")
        except RuntimeError:
            lost.append("refused")
    with c4a.run("k18c"):
        th = threading.Thread(target=in_thread)
        th.start()
        th.join()
    check("K18c no silent shared run: outside c4a.run, and in a thread that lost the run id, it refuses",
          outside == "refused" and lost == ["refused"], (outside, lost))

    fresh()
    tries = []

    @c4a.effect
    def flaky(to):
        tries.append(to)
        raise TimeoutError("gateway timeout")
    with c4a.run("k18d"):
        try:
            flaky("bob")
        except TimeoutError:
            pass
        t0 = _time.monotonic()
        try:
            flaky("bob")
            msg = "ran"
        except c4a.EffectInDoubt as e:
            msg = str(e)
        took = _time.monotonic() - t0
    check("K18d an effect that raised is in doubt at once (it may have happened), naming the error",
          tries == ["bob"] and "TimeoutError" in msg and took < 1.0, (tries, msg, round(took, 2)))

    fresh()
    tries.clear()

    @c4a.effect(retry_on=(ConnectionRefusedError,))
    def refused_conn(to):
        tries.append(to)
        if len(tries) == 1:
            raise ConnectionRefusedError("nothing sent")
        return "sent"
    with c4a.run("k18e"):
        try:
            refused_conn("bob")
        except ConnectionRefusedError:
            pass
        try:
            r18e = refused_conn("bob")
        except c4a.EffectInDoubt as e:
            r18e = repr(e)
    check("K18e retry_on: an error that proves nothing happened lets the next call run it", r18e == "sent"
          and tries == ["bob", "bob"], (r18e, tries))

    fresh()
    paid3 = []

    @c4a.effect(approval=True)
    def pay3(amount, to, currency="EUR"):
        paid3.append((amount, to, currency))
        return "paid"
    with c4a.run("k18f"):
        c4a.approve(pay3, amount=10, to="alice")
        try:
            pay3(10, "alice")
        except c4a.ApprovalRequired:
            pass
        try:
            pay3(10, "alice", currency="USD")
            usd = "paid"
        except c4a.ApprovalRequired as e:
            usd = str(e)
    check("K18f a yes without the default argument matches the call; another currency is refused, with a message",
          paid3 == [(10, "alice", "EUR")] and "needs approval" in usd and "USD" in usd, (paid3, usd))

    fresh()
    acount = []

    @c4a.effect
    async def aslow(to):
        acount.append(to)
        await _aio_mod.sleep(0.3)
        return "sent"
    import asyncio as _aio_mod

    async def two_at_once():
        with c4a.run("k18g"):
            return await _aio_mod.gather(aslow("bob"), aslow("bob"))
    try:
        both = _aio_mod.run(two_at_once())
    except c4a.EffectInDoubt as e:
        both = repr(e)
    check("K18g two concurrent async calls of one effect: it runs once, both get the result",
          both == ["sent", "sent"] and acount == ["bob"], (both, acount))

    class Opaque2:
        pass
    fresh()

    @c4a.effect
    def take(obj):
        return "taken"
    with c4a.run("k18h"):
        try:
            take(Opaque2())
            loud18 = False
        except TypeError:
            loud18 = True
    check("K18h an argument with no stable text form fails loudly (it would be resent on every resume)", loud18)

    # K19 a crafted record is data, never code: it cannot make the codec call a function (security review)
    from call4agents import codec as _codec19
    from tests import c4a_codec_types as _ct
    crafted = [
        {"__c4a__": "dataclass", "cls": "tests.c4a_codec_types:canary", "v": {"cmd": "x"}},
        {"__c4a__": "model", "cls": "tests.c4a_codec_types:canary", "v": {"cmd": "x"}},
        {"__c4a__": "model", "cls": "tests.c4a_codec_types:NotAModel", "v": {"cmd": "x"}},
        {"__c4a__": "dataclass", "cls": "os:system", "v": {"command": "echo pwned"}},
        {"__c4a__": "dataclass", "cls": "tests.c4a_codec_types:Order.__init__", "v": {}},
    ]
    refused19 = 0
    for item in crafted:
        try:
            _codec19.decode(item)
        except (ValueError, TypeError):
            refused19 += 1
    ok_dc = _codec19.decode(_codec19.encode(_ct.Order(1, ("a",)))) == _ct.Order(1, ("a",))
    check("K19 crafted records naming a function or a non-model are refused; nothing is called; real types still load",
          refused19 == len(crafted) and _ct.CALLED == [] and ok_dc, (refused19, _ct.CALLED, ok_dc))

    # K20 exclude= leaves a framework context out of the key; the adapters find that parameter anywhere
    from call4agents.adapters._common import context_param, wrap_tool
    fresh()
    sent20 = []

    class FakeCtx:                                     # like a framework context: no stable text form
        def __init__(self, conv):
            self.conversation_id = conv

    def tool20(ctx, to):
        sent20.append(to)
        return "sent"
    t20 = wrap_tool(tool20, ("FakeCtx",), lambda c: c.conversation_id, "test", {"name": "k20.send"})
    try:
        t20(FakeCtx("conv-1"), "bob")
        t20(FakeCtx("conv-1"), "bob")                  # a NEW context object, same conversation: no resend
        t20(FakeCtx("conv-2"), "bob")                  # another conversation: a new action
        err20 = None
    except Exception as exc:  # noqa: BLE001 - reported as a FAIL, not a crash
        err20 = repr(exc)[:120]

    def late(title: str, mcp_context: "Context") -> str:   # MCP style: the context is the LAST parameter
        return title
    found20 = context_param(late, ("Context",))
    check("K20 a context parameter is left out of the key; the run id comes from it; found anywhere in the signature",
          err20 is None and sent20 == ["bob", "bob"] and found20 == "mcp_context", (err20, sent20, found20))

    # K15 async effects and async recorded model calls behave like the sync ones
    import asyncio as _aio
    fresh()
    asent, acalls = [], []

    @c4a.effect
    async def asend(to):
        asent.append(to)
        return "sent"

    @c4a.recorded
    async def amodel(prompt):
        acalls.append(prompt)
        return ("plan", 1)

    async def flow():
        with c4a.run("async"):
            a1 = await amodel("p")
            await asend("bob")
            await asend("bob")
            return a1
    got = _aio.run(flow())
    check("K15 async effect runs once; async recorded call keeps its tuple type",
          asent == ["bob"] and acalls == ["p"] and got == ("plan", 1), (asent, acalls, got))

    # K16 a REAL pydantic v2 model (nested model + datetime) survives record -> replay
    from tests.c4a_codec_types import ModelReply, Usage
    if ModelReply is None:
        print("  skip  K16 pydantic not installed")
    else:
        reply = ModelReply(text="ok", created=_dtm.datetime(2026, 10, 16, 9, 0), usage=Usage(prompt_tokens=3, completion_tokens=1))
        d3 = _P(_tf.mkdtemp(prefix="c4a-pyd-"))
        c4a.init(str(d3 / "rec.sqlite"), secret=b"s")

        @c4a.recorded
        def real_model(q):
            return reply
        with c4a.run("pyd"):
            real_model("q")
        c4a.init(str(d3 / "rep.sqlite"), secret=b"s", replay_from=str(d3 / "rec.sqlite"))
        with c4a.run("pyd"):
            back = real_model("q")
        check("K16 a real pydantic model comes back as the same class with equal fields",
              type(back) is ModelReply and back == reply and isinstance(back.usage, Usage), repr(back)[:80])

    # K6 the offline demo shows the promised numbers
    from call4agents.__main__ import demo
    r = demo()
    nums = [r[k]["emails"] for k in list(r)[:3]]
    check("K6 demo: 2 emails without the record, 1 with it (both crash points)", nums == [2, 1, 1], nums)
    a = r["approval"]
    check("K6b demo: changed payment refused, approved one paid once",
          a["changed_payment"].startswith("REFUSED") and a["approved_payment"].startswith("PAID") and a["payments"] == 1, a)

    # K21 a named action (c4a_key= or key=) reused with other arguments is a conflict, not a cache hit (issue #1)
    import decimal
    fresh()
    k21 = []

    @c4a.effect(name="k21.pay")
    def k21_pay(amount):
        k21.append(amount)
        return f"paid {amount}"

    @c4a.effect(name="k21.charge", key=lambda order, amount: order)
    def k21_charge(order, amount):
        k21.append((order, amount))
        return f"charged {amount}"

    def conflict(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except c4a.KeyConflict as exc:
            return f"conflict: {exc}"

    with c4a.run("k21"):
        first = k21_pay(10, c4a_key="order-7")
        same = [conflict(k21_pay, v, c4a_key="order-7") for v in (10, 10.0, decimal.Decimal("10.00"))]
        other = conflict(k21_pay, 20, c4a_key="order-7")
        k21_charge("o-9", 5)
        other_fn = conflict(k21_charge, "o-9", 6)
    check("K21 the same key with the same amount (10, 10.0, Decimal 10.00) returns the recorded result",
          first == "paid 10" and same == ["paid 10"] * 3, same)
    check("K21 the same key with another amount is a conflict and nothing runs",
          other.startswith("conflict:") and "order-7" in other and k21.count(10) == 1 and 20 not in k21, (other, k21))
    check("K21 the same for a key= function", other_fn.startswith("conflict:") and ("o-9", 6) not in k21, other_fn)

    # K7 the core imports only the standard library
    import ast
    std = set(sys.stdlib_module_names)
    bad = []
    for f in Path(c4a.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mods = [n.name for n in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else []
            bad += [m for m in mods if m.split(".")[0] not in std and not m.startswith("call4agents")]
    check("K7 the core uses only the Python standard library", not bad, bad)

    print(f"\n{len(FAILED)} kit check(s) failed" if FAILED else "\nall kit checks pass")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
