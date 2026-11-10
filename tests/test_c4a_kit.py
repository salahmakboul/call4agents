"""Call4Agents v0.1 kit - offline checks (0 calls). Run: python -m tests.test_c4a_kit"""

import asyncio
import json
import sqlite3
import sys
import tempfile
import time
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

    # K25 reconcile() can say "cannot tell yet" (c4a.UNKNOWN): stays in doubt, records nothing, runs nothing;
    # the next try asks again (a lookup that lags behind writes must never trigger a re-run)
    lg = fresh()
    sent.clear()
    answers = [c4a.UNKNOWN, c4a.UNKNOWN, None]

    @c4a.effect(reconcile=lambda to: answers.pop(0))
    def mail3(to):
        sent.append(to)
        return "ok"
    k3 = effect_key("r1", mail3.c4a_name, {"to": "bob"})
    lg.append("r1", "tool_intent", intent_key(k3), {"tool": "mail3"})
    outcomes = []
    for _ in range(3):
        try:
            with c4a.run("r1"):
                outcomes.append(mail3("bob"))
        except c4a.EffectInDoubt as exc:
            outcomes.append("doubt: " + ("could not tell" in str(exc) and "unknown" or str(exc)))
        except Exception as exc:  # noqa: BLE001  (a wrong path must FAIL the check below, not crash the suite)
            outcomes.append(type(exc).__name__)
        if len(outcomes) == 2:
            kinds_after_unknown = [e["kind"] for e in lg.entries("r1")]
    check("K25 reconcile() returning c4a.UNKNOWN keeps the call in doubt and runs nothing",
          outcomes[:2] == ["doubt: unknown"] * 2 and kinds_after_unknown == ["tool_intent"], (outcomes, kinds_after_unknown))
    check("K25b after UNKNOWN, a later 'did not happen' (None) runs it once", outcomes[2] == "ok" and sent == ["bob"],
          (outcomes, sent))
    try:
        bool(c4a.UNKNOWN)
        truthy = True
    except TypeError:
        truthy = False
    check("K25c c4a.UNKNOWN has no truth value (`if reconcile(...)` would treat it as an answer)", not truthy)

    # K26 retries=N: retry only when the record decides the next try; any other error is raised at once
    class Busy(Exception):
        pass

    class NoAnswer(Exception):
        pass

    def k26_call(fn, *a):
        try:
            with c4a.run("r1"):
                return fn(*a)
        except c4a.EffectInDoubt as exc:
            return "doubt: " + str(exc)
        except Exception as exc:  # noqa: BLE001  (a wrong path must FAIL a check, not crash the suite)
            return type(exc).__name__

    lg = fresh()
    tries = []

    @c4a.effect(retry_on=(Busy,), retries=3, backoff=0)
    def k26a(to):
        tries.append(to)
        if len(tries) < 3:
            raise Busy("try later")
        return "sent"
    got = k26_call(k26a, "bob")
    kinds = [e["kind"] for e in lg.entries("r1")]
    check("K26 an error that proves nothing happened is retried, and each try is recorded",
          got == "sent" and len(tries) == 3 and kinds.count("tool_abandoned") == 2 and kinds.count("tool_done") == 1,
          (got, tries, kinds))

    lg = fresh()
    tries = []

    @c4a.effect(retry_on=(Busy,), retries=3, backoff=0)
    def k26b(to):
        tries.append(to)
        raise NoAnswer("no answer")
    first, second = k26_call(k26b, "bob"), k26_call(k26b, "bob")
    check("K26b any other error is raised at once, never retried blind; the next call is in doubt and names it",
          first == "NoAnswer" and second.startswith("doubt") and "NoAnswer" in second and tries == ["bob"],
          (first, second, tries))

    lg = fresh()
    keys, bank = [], {}

    @c4a.effect(idempotent_for=3600, retries=2, backoff=0)
    def k26c(amount):
        k = c4a.idempotency_key()
        keys.append(k)
        bank.setdefault(k, amount)          # the provider charges once per key
        if len(keys) == 1:
            raise NoAnswer("charged, but the answer was lost")
        return "charged"
    got = k26_call(k26c, 10)
    check("K26c with idempotent_for the retry resends the SAME key: the provider charges once",
          got == "charged" and len(keys) == 2 and keys[0] == keys[1] and len(bank) == 1, (got, keys, bank))

    lg = fresh()
    tries = []

    @c4a.effect(retry_on=(Busy,), retries=2, backoff=0)
    def k26d(to):
        tries.append(to)
        raise Busy("still busy")
    got = k26_call(k26d, "bob")
    check("K26d after the last retry the real error is raised, after exactly retries + 1 tries",
          got == "Busy" and len(tries) == 3, (got, tries))

    lg = fresh()
    tries = []

    @c4a.effect(approval=True, retry_on=(Busy,), retries=1, backoff=0)
    def k26e(amount):
        tries.append(amount)
        if len(tries) == 1:
            raise Busy("busy")
        return "paid"
    with c4a.run("r1"):
        c4a.approve(k26e, amount=5)
    got = k26_call(k26e, 5)
    check("K26e an approved action retried after a 'nothing happened' error needs no second yes, and finishes once",
          got == "paid" and tries == [5, 5], (got, tries))

    try:
        @c4a.effect(retries=2)
        def k26f(to):
            return to
        refused = False
    except ValueError:
        refused = True
    check("K26f retries= with no retry_on, idempotent_for or reconcile is refused when the action is declared", refused)

    lg = fresh()
    tries = []

    @c4a.effect(reconcile=lambda to: None, retries=3, backoff=0)
    def k26g(to):
        tries.append(to)
        raise KeyboardInterrupt
    try:
        with c4a.run("r1"):
            k26g("bob")
        stopped = False
    except KeyboardInterrupt:
        stopped = True
    check("K26g a cancel or exit (not an Exception) is never retried", stopped and tries == ["bob"], tries)

    lg = fresh()
    tries = []

    @c4a.effect(reconcile=lambda to: "found it", retries=2, backoff=0)
    def k26h(to):
        tries.append(to)
        raise NoAnswer("timed out after sending")
    got = k26_call(k26h, "bob")
    check("K26h with reconcile the retry asks the provider first: it found the action, so it does not run again",
          got == "found it" and tries == ["bob"], (got, tries))

    import asyncio
    lg = fresh()
    tries = []

    @c4a.effect(retry_on=(Busy,), retries=2, backoff=0)
    async def k26i(to):
        tries.append(to)
        if len(tries) < 2:
            raise Busy("busy")
        return "sent"

    async def k26i_run():
        with c4a.run("r1"):
            return await k26i("bob")
    got = asyncio.run(k26i_run())
    check("K26i the async version retries the same way", got == "sent" and tries == ["bob", "bob"], (got, tries))

    # K27 shared limits: the check and the hold are one transaction with the intent
    def k27_call(fn, *a, run="r1"):
        try:
            with c4a.run(run):
                return fn(*a)
        except c4a.LimitReached:
            return "limit"
        except c4a.EffectInDoubt:
            return "doubt"
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__

    lg = fresh()
    sent.clear()
    three = c4a.Limit("k27-mails", 3)

    @c4a.effect(limit=three)
    def k27a(to):
        sent.append(to)
        return "sent"
    got = [k27_call(k27a, who) for who in ("a", "b", "c", "d")]
    kinds = [e["kind"] for e in lg.entries("r1")]
    check("K27 a limit of 3 actions: the 4th is refused, nothing runs and nothing is recorded for it",
          got == ["sent"] * 3 + ["limit"] and sent == ["a", "b", "c"] and kinds.count("tool_intent") == 3
          and kinds.count("limit_reserved") == 3, (got, sent, kinds))

    lg = fresh()
    paid = []
    budget = c4a.Limit("k27-refunds", 500, cost=lambda amount, **_: amount)

    @c4a.effect(limit=budget)
    def k27b(order, amount):
        paid.append(amount)
        return "refunded"
    got = [k27_call(k27b, o, a) for o, a in (("o1", 200), ("o2", 250), ("o3", 100), ("o4", 50))]
    check("K27b costs add up: 200 + 250 held, 100 more is refused, 50 more fits", got == ["refunded", "refunded",
          "limit", "refunded"] and paid == [200, 250, 50], (got, paid))

    lg = fresh()
    tries = []

    @c4a.effect(limit=c4a.Limit("k27-one", 1), retry_on=(Busy,))
    def k27c(to):
        tries.append(to)
        if to == "busy":
            raise Busy("not sent")
        return "sent"
    got = [k27_call(k27c, "busy"), k27_call(k27c, "bob")]
    check("K27c an error that proves nothing happened gives the cost back", got == ["Busy", "sent"]
          and tries == ["busy", "bob"], (got, tries))

    lg = fresh()
    tries = []

    @c4a.effect(limit=c4a.Limit("k27-doubt", 1))
    def k27d(to):
        tries.append(to)
        if to == "lost":
            raise NoAnswer("sent, maybe")
        return "sent"
    got = [k27_call(k27d, "lost"), k27_call(k27d, "lost"), k27_call(k27d, "bob")]
    check("K27d an action in doubt keeps holding its cost: the next action is refused", got == ["NoAnswer", "doubt",
          "limit"] and tries == ["lost"], (got, tries))

    lg = fresh()
    sent.clear()

    @c4a.effect(limit=c4a.Limit("k27-same", 1))
    def k27e(to):
        sent.append(to)
        return "sent"
    got = [k27_call(k27e, "bob"), k27_call(k27e, "bob")]
    kinds = [e["kind"] for e in lg.entries("r1")]
    check("K27e the same action again returns its result and does not hold the limit twice",
          got == ["sent", "sent"] and sent == ["bob"] and kinds.count("limit_reserved") == 1, (got, sent, kinds))

    lg = fresh()
    paid = []

    @c4a.effect(approval=True, limit=c4a.Limit("k27-approved", 100, cost=lambda amount: amount))
    def k27f(amount):
        paid.append(amount)
        return "paid"
    with c4a.run("r1"):
        c4a.approve(k27f, amount=80)
        c4a.approve(k27f, amount=30)
    got = [k27_call(k27f, 80), k27_call(k27f, 30)]
    kinds = [e["kind"] for e in lg.entries("r1")]
    check("K27f with approvals: over the limit is refused and its yes stays unspent (one transaction)",
          got == ["paid", "limit"] and paid == [80] and kinds.count("approval_used") == 1, (got, paid, kinds))

    lg = fresh()
    sent.clear()

    @c4a.effect(limit=c4a.Limit("k27-window", 1, per=0.3))
    def k27g(to):
        sent.append(to)
        return "sent"
    first, second = k27_call(k27g, "a"), k27_call(k27g, "b")
    time.sleep(0.4)
    third = k27_call(k27g, "c")
    check("K27g per=SECONDS: a hold older than the window no longer counts", (first, second, third) ==
          ("sent", "limit", "sent") and sent == ["a", "c"], (first, second, third, sent))

    lg = fresh()

    @c4a.effect(limit=c4a.Limit("k27-bad", 10, cost=lambda to: -1))
    def k27h(to):
        return "sent"
    bad = [k27_call(k27h, "x")]
    for wrong in (lambda: c4a.Limit("", 1), lambda: c4a.Limit("x", -1), lambda: c4a.Limit("x", 1, per=0)):
        try:
            wrong()
            bad.append("accepted")
        except ValueError:
            bad.append("refused")
    check("K27h a negative cost, an empty name, a negative maximum and per=0 are refused",
          bad == ["ValueError", "refused", "refused", "refused"], bad)

    lg = fresh()
    keys = []

    @c4a.effect(limit=c4a.Limit("k27-resend", 1), idempotent_for=3600, retries=1, backoff=0)
    def k27i(amount):
        keys.append(c4a.idempotency_key())
        if len(keys) == 1:
            raise NoAnswer("charged, answer lost")
        return "charged"
    got = k27_call(k27i, 10)
    kinds = [e["kind"] for e in lg.entries("r1")]
    check("K27i a same-key resend of the same action keeps its one hold (a limit of 1 is enough)",
          got == "charged" and len(keys) == 2 and kinds.count("limit_reserved") == 1, (got, keys, kinds))

    # K28 rules checked against the bound arguments before a new attempt is admitted
    def k28_call(fn, *a, run="r1", **kw):
        try:
            with c4a.run(run):
                return fn(*a, **kw)
        except c4a.RuleRefused as exc:
            return "refused " + exc.rule
        except c4a.ApprovalRequired:
            return "ask"
        except c4a.LimitReached:
            return "limit"
        except c4a.EffectInDoubt:
            return "doubt"
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__

    lg = fresh()
    paid = []
    shop = c4a.Rules({"k28.refund": {"max": {"amount": 500}, "allow": {"currency": ["EUR"]},
                                     "ask_over": {"amount": 100}, "per_run": 2}})

    @c4a.effect(name="k28.refund", rules=shop)
    def k28a(order, amount, currency="EUR"):
        paid.append((order, amount))
        return "refunded"
    got = [k28_call(k28a, "o1", 600), k28_call(k28a, "o2", 50, "USD"), k28_call(k28a, "o3", "50"),
           k28_call(k28a, "o4", float("nan"))]
    kinds = [e["kind"] for e in lg.entries("r1")]
    check("K28 over max, a currency not in the list, an amount that is text or NaN: refused, nothing runs, "
          "no intent, each refusal recorded",
          got == ["refused max.amount", "refused allow.currency", "refused max.amount", "refused max.amount"]
          and paid == [] and kinds.count("tool_intent") == 0 and kinds.count("rule_refused") == 4, (got, paid, kinds))

    got = [k28_call(k28a, "o5", 150)]
    with c4a.run("r1"):
        c4a.approve(k28a, order="o5", amount=150, currency="EUR")
    got += [k28_call(k28a, "o5", 150), k28_call(k28a, "o6", 50)]
    check("K28b over ask_over needs a yes for the exact call even without approval=True; under it runs",
          got == ["ask", "refunded", "refunded"] and paid == [("o5", 150), ("o6", 50)], (got, paid))

    got = [k28_call(k28a, "o7", 10), k28_call(k28a, "o5", 150), k28_call(k28a, "o8", 10, run="r2")]
    check("K28c per_run 2: a third action in the run is refused, a repeat of a finished one returns its "
          "result, another run has its own count",
          got == ["refused per_run", "refunded", "refunded"] and paid == [("o5", 150), ("o6", 50), ("o8", 10)],
          (got, paid))

    intent = [e for e in lg.entries("r1") if e["kind"] == "tool_intent"][0]["payload"]
    check("K28d the intent records the rules' digest and the decision",
          intent.get("rules", {}).get("digest") == shop.digest and intent["rules"]["decision"] == "ask", intent)

    lg = fresh()
    paid = []

    @c4a.effect(name="k28.e", rules=c4a.Rules({"k28.e": {"max": {"amount": 500}}}))
    def k28e_before(amount):
        paid.append(amount)
        return "paid"

    @c4a.effect(name="k28.e", rules=c4a.Rules({"k28.e": {"max": {"amount": 100}}}))
    def k28e_after(amount):
        paid.append(amount)
        return "paid again"
    got = [k28_call(k28e_before, 300), k28_call(k28e_after, 300), k28_call(k28e_after, 200)]
    check("K28e a finished action replays its result after the rules are tightened; a new one is refused",
          got == ["paid", "paid", "refused max.amount"] and paid == [300], (got, paid))

    lg = fresh()
    tries = []

    @c4a.effect(name="k28.f", rules=c4a.Rules({"k28.f": {"per_run": 1}}), retry_on=(Busy,))
    def k28f(to):
        tries.append(to)
        if to in ("busy", "lost"):
            raise Busy("not sent") if to == "busy" else NoAnswer("sent, maybe")
        return "sent"
    got = [k28_call(k28f, "busy"), k28_call(k28f, "lost"), k28_call(k28f, "lost"), k28_call(k28f, "bob")]
    check("K28f per_run: an error that proves nothing happened gives the count back; one in doubt keeps it",
          got == ["Busy", "NoAnswer", "doubt", "refused per_run"] and tries == ["busy", "lost"], (got, tries))

    bad = []
    for section in ({"maxx": {"amount": 5}}, {"max": {"amount": "5"}}, {"allow": {"to": "bob"}}, {"per_run": -1},
                    {"max": {}}):
        try:
            c4a.Rules({"k28.g": section})
            bad.append("accepted")
        except ValueError:
            bad.append("refused")
    try:
        c4a.effect(name="k28.other", rules=c4a.Rules({"k28.g": {"per_run": 1}}))(lambda: None)
        bad.append("accepted")
    except ValueError:
        bad.append("refused")
    check("K28g a misspelt rule, a bound that is text, a bad list, a negative count, an empty max and a tool "
          "with no section are refused when declared", bad == ["refused"] * 6, bad)

    lg = fresh()
    paid = []
    seen = []

    def policy(tool, args):
        seen.append(tool)
        args["to"] = "mallory"                       # a policy that changes its input must not change the call
        if args["amount"] == 13:
            raise RuntimeError("policy server down")
        return {1: "allow", 2: "ask", 3: ("deny", "blocked account"), 4: "yes"}.get(args["amount"], "allow")

    @c4a.effect(name="k28.h", rules=policy)
    def k28h(amount, to="alice"):
        paid.append((amount, to))
        return "paid"
    got = [k28_call(k28h, n) for n in (1, 2, 3, 4, 13)]
    recorded = [e["payload"]["args"] for e in lg.entries("r1") if e["kind"] in ("tool_intent", "rule_refused")]
    check("K28h a policy function: allow runs, ask needs a yes, deny and a crashing or unclear policy refuse "
          "(fails closed), and it cannot change the call or what is recorded",
          got[:2] == ["paid", "ask"] and all(g.startswith("refused policy:") for g in got[2:])
          and paid == [(1, "alice")] and len(seen) == 5 and len(recorded) == 4
          and all(a["to"] == "alice" for a in recorded), (got, paid, recorded))

    lg = fresh()
    paid = []

    @c4a.effect(name="k28.i", rules=c4a.Rules({"k28.i": {"per_run": 5}}),
                limit=c4a.Limit("k28-budget", 100, cost=lambda amount: amount))
    def k28i(amount):
        paid.append(amount)
        return "paid"
    got = [k28_call(k28i, 70), k28_call(k28i, 40)]
    held = [e for e in lg.entries("r1") if e["kind"] == "limit_reserved"]
    check("K28i a rule count and a Limit together: the Limit refusing rolls back the rule's count too",
          got == ["paid", "limit"] and paid == [70] and len(held) == 2, (got, paid, held))

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

    # K22 a "yes" spent under a named key covers only the arguments it was given for (issue #1)
    import threading
    k22 = []

    @c4a.effect(name="k22.pay", approval=True)
    def k22_pay(amount):
        k22.append(amount)
        return f"paid {amount}"

    @c4a.effect(name="k22.send")
    def k22_send(amount):
        k22.append(("send", amount))
        return f"sent {amount}"

    def outcome(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (c4a.ApprovalRequired, c4a.KeyConflict) as exc:
            return type(exc).__name__

    # A legacy record can have a spent "yes" without an intent. It must remain resumable.
    lg = fresh()
    with c4a.run("k22"):
        c4a.approve(k22_pay, amount=10)
        from call4agents.ledger import effect_key
        old_key = effect_key("k22", "k22.pay", {"__c4a_key__": "order-1"})
        assert lg.use_approval("k22", "k22.pay", {"amount": 10}, old_key)
    with c4a.run("k22"):
        other = outcome(k22_pay, 20, c4a_key="order-1")
        again = outcome(k22_pay, 10, c4a_key="order-1")
    check("K22 after a crash, a spent yes does not cover other arguments under the same key",
          other == "ApprovalRequired" and 20 not in k22, (other, k22))
    check("K22 the approved call can still finish after that crash", again == "paid 10" and k22 == [10], (again, k22))

    def race(fn, slow_args, a_args, b_args, setup=None):
        """Call A has passed its checks and waits just before recording its intent while B runs."""
        lg = fresh()
        k22.clear()
        claim = lg.claim_approved if fn is k22_pay else lg.claim
        ready, go, out = threading.Event(), threading.Event(), {}

        def slow(*args):
            payload = args[-1]
            if payload and payload["args"] == slow_args:
                ready.set()
                go.wait(10)
            return claim(*args)

        if fn is k22_pay:
            lg.claim_approved = slow
        else:
            lg.claim = slow
        with c4a.run("k22"):
            if setup:
                setup()

        def first():
            with c4a.run("k22"):
                out["a"] = outcome(fn, *a_args, c4a_key="order-2")

        t = threading.Thread(target=first)
        t.start()
        ready.wait(10)
        with c4a.run("k22"):
            out["b"] = outcome(fn, *b_args, c4a_key="order-2")
        go.set()
        t.join()
        return out

    out = race(k22_pay, {"amount": 10}, (10,), (20,), lambda: c4a.approve(k22_pay, amount=10))
    check("K22 two threads: the unapproved amount is refused and the approved one is paid",
          out == {"a": "paid 10", "b": "ApprovalRequired"} and k22 == [10], (out, k22))
    out = race(k22_send, {"amount": 1}, (1,), (2,))
    check("K22 two threads without approval: one runs, the other gets a conflict, not the winner's result",
          out == {"a": "KeyConflict", "b": "sent 2"} and k22 == [("send", 2)], (out, k22))

    # K23 `call4agents incident`: the numbers shown to people come from a real run
    from call4agents.examples.approval_incident import run_all
    inc = run_all()
    charges = {name: (len(r["without"]["charges"]), len(r["with"]["charges"])) for name, r in inc.items()}
    check("K23 incident demo: 2 charges without, 1 with; a changed call or a no pays nothing with the kit",
          charges == {"double_payment": (2, 1), "crash_inside": (2, 1), "changed_call": (1, 0), "ignored_no": (1, 0)}
          and inc["changed_call"]["without"]["charges"] == ["9999 mallory"]
          and inc["crash_inside"]["with"]["stopped"] == "EffectInDoubt"
          and inc["ignored_no"]["with"]["stopped"] == "ApprovalRequired", charges)

    # K24 a cancelled async action may already have reached the outside world: it is in doubt, never run again
    k24 = []
    k24_started = asyncio.Event()

    @c4a.effect(name="k24.refund")
    async def k24_refund(order):
        k24.append(order)
        k24_started.set()
        await asyncio.sleep(5)

    async def k24_run():
        with c4a.run("k24"):
            task = asyncio.create_task(k24_refund("A"))
            await asyncio.wait_for(k24_started.wait(), timeout=10)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            try:
                await k24_refund("A")
                return "ran"
            except c4a.EffectInDoubt as e:
                return str(e)

    said = asyncio.run(k24_run())
    check("K24 a cancelled action is in doubt at once, names the cancel, and does not run again",
          "CancelledError" in said and k24 == ["A"], (said, k24))

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
