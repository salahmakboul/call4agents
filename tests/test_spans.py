"""call4agents spans. Run: python -m tests.test_spans

O1 one span per action, named "execute_tool <tool>", kind INTERNAL, with gen_ai.operation.name and gen_ai.tool.name.
O2 a finished action has status UNSET and no error.type; an action in doubt has error.type c4a.in_doubt and ERROR.
O3 an action that failed and was proved not done shows the error's class name.
O4 a call refused by a rule is a span with error.type c4a.rule_refused and its rule.
O5 arguments, error texts and refusal reasons are left out unless asked for; results are never written.
O6 each run is one trace; ids are valid hex of the right length, unique per span, and the same on a second export.
O7 the command writes one line of JSON and refuses an unknown run."""
import json
import sys
import tempfile
from pathlib import Path

import call4agents as c4a
from call4agents.ledger import LedgerError
from call4agents.spans import build, main

FAILED = []


def check(name, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + name + ("" if ok else f"   {detail}"))
    if not ok:
        FAILED.append(name)


class Busy(Exception):
    pass


class NoAnswer(Exception):
    pass


def attrs(span):
    out = {}
    for a in span["attributes"]:
        v = a["value"]
        out[a["key"]] = v.get("stringValue", v.get("intValue", v.get("boolValue", v)))
    return out


def main_test():
    folder = tempfile.mkdtemp()
    record = str(Path(folder) / "r.sqlite")
    c4a.init(record, secret=b"spans-secret-0123")

    @c4a.effect(name="o.pay", approval=True)
    def pay(amount, to):
        return f"secret result {amount}"

    @c4a.effect(name="o.mail", retry_on=(Busy,))
    def mail(to):
        if to == "busy":
            raise Busy("server busy")
        if to == "lost":
            raise NoAnswer("sent, maybe")
        return "secret mail result"

    @c4a.effect(name="o.refund", rules=c4a.Rules({"o.refund": {"max": {"cents": 500}}}))
    def refund(cents):
        return "refunded"

    with c4a.run("r1"):
        c4a.approve(pay, amount=10, to="alice")
        pay(10, "alice")
        for to in ("busy", "lost"):
            try:
                mail(to)
            except (Busy, NoAnswer):
                pass
        try:
            refund(9999)
        except c4a.RuleRefused:
            pass
    with c4a.run("r2"):
        mail("bob")

    doc = build(record)
    spans = doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
    by = {}
    for s in spans:
        a = attrs(s)
        by[(a["c4a.run_id"], a["gen_ai.tool.name"], a.get("c4a.state"))] = (s, a)
    paid = by.get(("r1", "o.pay", "done"))
    check("O1 one span per action and per refusal", len(spans) == 5, [s["name"] for s in spans])
    check("O1 name, kind and GenAI attributes", paid is not None and paid[0]["name"] == "execute_tool o.pay"
          and paid[0]["kind"] == 1 and paid[1]["gen_ai.operation.name"] == "execute_tool" and paid[1]["c4a.approved"] is True,
          paid)
    lost = by.get(("r1", "o.mail", "IN DOUBT"))
    check("O2 a finished action: status UNSET, no error.type", paid[0]["status"] == {"code": 0} and "error.type" not in paid[1],
          paid)
    check("O2 an action in doubt: error.type c4a.in_doubt and status ERROR", lost is not None
          and lost[1]["error.type"] == "c4a.in_doubt" and lost[0]["status"]["code"] == 2, list(by))
    busy = by.get(("r1", "o.mail", "not done (may run again)"))
    check("O3 an error proved not done shows its class name", busy is not None and busy[1].get("error.type") == "Busy"
          and busy[0]["status"]["code"] == 2, busy)
    ref = by.get(("r1", "o.refund", "refused by a rule"))
    check("O4 a refused call is a span with its rule", ref is not None and ref[1]["error.type"] == "c4a.rule_refused"
          and ref[1]["c4a.rule"] == "max.cents" and ref[0]["status"]["code"] == 2, ref)
    text = json.dumps(doc)
    check("O5 arguments, error texts, reasons and results are left out by default", "gen_ai.tool.call.arguments" not in text
          and "alice" not in text and "9999" not in text and "server busy" not in text and "sent, maybe" not in text
          and "secret" not in text, text[:300])
    with_args = build(record, with_args=True)
    text2 = json.dumps(with_args)
    check("O5 --with-args adds the arguments, still no results", "gen_ai.tool.call.arguments" in text2 and "alice" in text2
          and "secret" not in text2)
    traces = {(attrs(s)["c4a.run_id"], s["traceId"]) for s in spans}
    ids = [s["spanId"] for s in spans]
    hexok = all(len(s["traceId"]) == 32 and len(s["spanId"]) == 16 and int(s["traceId"], 16) and int(s["spanId"], 16)
                for s in spans)
    check("O6 one trace per run, valid unique ids, the same on a second export",
          len(traces) == 2 and len({t for _, t in traces}) == 2 and len(set(ids)) == len(ids) and hexok
          and json.dumps(build(record)["resourceSpans"][0]["scopeSpans"][0]["spans"]) == json.dumps(spans), traces)
    out = str(Path(folder) / "spans.json")
    lines = Path(out).read_text(encoding="utf-8").splitlines() if main([record, out]) == 0 else []
    check("O7 the command writes one line of JSON", len(lines) == 1 and json.loads(lines[0])["resourceSpans"], lines[:1])
    try:
        build(record, run="nope")
        check("O7 an unknown run is an error", False)
    except LedgerError:
        check("O7 an unknown run is an error", True)

    if FAILED:
        print(f"{len(FAILED)} spans check(s) failed")
        return 1
    print("all spans checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main_test())
