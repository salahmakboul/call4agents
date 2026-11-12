# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Property-based tests and fuzzing of the run record and of effect(). Run: python -m tests.test_properties

Needs Hypothesis (a TEST-only dependency; the package itself stays standard library only). Without it the
suite is skipped with a message; --require-hypothesis turns the skip into a failure (CI uses it).

    --long             many more examples (the weekly CI run)
    --only NAME        run one group: machine, canonical, codec, files, race
    --seed N           with --long: use this seed (default: a random one, printed)

The default run is derandomized (the same examples every time, so a CI failure is reproducible).
What each property says, and what it does not cover: docs/PROPERTIES.md.
"""
import argparse
import base64
import builtins
import dataclasses
import datetime as _dt
import decimal
import importlib
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from pathlib import Path

try:
    import hypothesis
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st
    from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, run_state_machine_as_test
    HAVE_HYPOTHESIS = True
except ImportError:                     # pragma: no cover - the skip path
    HAVE_HYPOTHESIS = False

import call4agents as c4a
from call4agents import codec
from call4agents.ledger import LedgerError, canonical, effect_key

FAILED = []
COUNTS = {}


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok else ""), flush=True)
    if not ok:
        FAILED.append(name)


def count(name):
    COUNTS[name] = COUNTS.get(name, 0) + 1


# ------------------------------------------------------------------ codec canaries (decode must never call these)
CALLED = []


def canary_fn(**kw):
    CALLED.append(("canary_fn", kw))
    return "ran"


canary_fn.model_validate = lambda data: CALLED.append(("canary_fn.model_validate", data))
canary_fn.model_dump = lambda **kw: {}


class OnlyValidate:
    """Has model_validate but no model_dump: not a model."""

    @classmethod
    def model_validate(cls, data):
        CALLED.append(("OnlyValidate", data))
        return cls()


class Plain:
    """A plain class, not a dataclass: its constructor must never be called by decode."""

    def __init__(self, **kw):
        CALLED.append(("Plain", kw))


@dataclasses.dataclass
class Point:
    """A real dataclass: decode may rebuild it (that is its job)."""
    x: int = 0
    y: object = None


class Model:
    """Pydantic-style model: model_dump + model_validate."""

    def __init__(self, data):
        self.data = data

    def model_dump(self, mode="python"):
        return self.data

    @classmethod
    def model_validate(cls, data):
        return cls(data)

    def __eq__(self, other):
        return type(other) is type(self) and same(self.data, other.data)


def canary_calls():
    """CALLED here and in the second copy of this module (decode imports it by name when we run as __main__)."""
    calls = list(CALLED)
    other = sys.modules.get("tests.test_properties")
    if other is not None and other is not sys.modules.get(__name__):
        calls += list(other.CALLED)
    return calls


# ------------------------------------------------------------------ strict equality (type and spelling)
def same(a, b):
    """Equal AND the same types all the way down (bytearray comes back as bytes; floats by repr)."""
    ta, tb = type(a), type(b)
    if ta is bytearray:
        ta = bytes
    if ta is not tb:
        return False
    if isinstance(a, float):
        return repr(a) == repr(b)
    if isinstance(a, decimal.Decimal):
        return str(a) == str(b)
    if isinstance(a, (_dt.datetime, _dt.date, _dt.time)):
        return a.isoformat() == b.isoformat()
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        if len(a) != len(b):
            return False
        for k, v in a.items():
            match = [kb for kb in b if same(k, kb)]
            if len(match) != 1 or not same(v, b[match[0]]):
                return False
        return True
    if isinstance(a, (set, frozenset)):
        return len(a) == len(b) and all(any(same(x, y) for y in b) for x in a)
    return a == b


def json_same(a, b):
    """The same JSON value with the same spelling: bool is not int, 1 is not 1.0, -0.0 is not 0.0."""
    if type(a) is not type(b):
        return False
    if isinstance(a, float):
        return repr(a) == repr(b)
    if isinstance(a, list):
        return len(a) == len(b) and all(json_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(json_same(a[k], b[k]) for k in a)
    return a == b


# ================================================================== a. the state machine
ITEMS = ("a", "b", "c")
KEYS = (None, "k1", "k2")
BEHAVIORS = ("ok", "crash_before", "crash_before_hard", "crash_after", "crash_after_hard",
             "retry_error", "error_before", "error_after")
TOOL = "props.pay"
RUN = "props-run"
SECRET = b"props-secret-0123456789abcdef"
HARD_KINDS = ("tool_done", "tool_error", "tool_abandoned", "late_result")


class Crash(BaseException):
    """The process dies here (BaseException: not an error the agent catches)."""


class RetryErr(Exception):
    """In retry_on: it proves the action did not happen (it is raised BEFORE the outside action)."""


class OtherErr(Exception):
    """Any other error: the action may or may not have happened."""


TL = threading.local()          # per thread: behaviors to play, reconcile mode, explicit key, hard-crash flag
CUR = {"m": None}               # the machine of the running example


def oracle_key(item, key):
    return effect_key(RUN, TOOL, {"__c4a_key__": key} if key is not None else {"item": item})


def body(item):
    m = CUR["m"]
    k = c4a.idempotency_key()
    beh = TL.behaviors.pop(0) if getattr(TL, "behaviors", None) else "ok"
    m.event("admit", k, item, beh)
    if k != oracle_key(item, getattr(TL, "key", None)):
        m.violations.append(f"idempotency_key() {k[:8]} is not the key of ({item}, {TL.key})")

    def act():
        with m.lock:
            m.counter += 1
            res = f"res:{item}:{getattr(TL, 'key', None)}:{m.counter}"
            m.provider.setdefault(k, []).append(res)
        return res

    if beh == "ok":
        return act()
    if beh == "crash_before":
        raise Crash("before the outside action")
    if beh == "crash_before_hard":
        TL.hard = True
        raise Crash("before the outside action; nothing more is recorded")
    if beh == "crash_after":
        act()
        raise Crash("after the outside action")
    if beh == "crash_after_hard":
        res = act()
        TL.hard = True                  # the tool_done write fails: the process died before the record
        return res
    if beh == "retry_error":
        m.event("proven", k, item, beh)
        raise RetryErr("refused before anything happened")
    if beh == "error_before":
        raise OtherErr("failed before the outside action")
    if beh == "error_after":
        act()
        raise OtherErr("failed after the outside action")
    raise AssertionError(beh)


def reconcile_fn(item):
    m = CUR["m"]
    k = oracle_key(item, getattr(TL, "key", None))
    if getattr(TL, "mode", "truthful") == "lagging":
        ans = c4a.UNKNOWN
    else:
        with m.lock:
            ans = m.provider[k][0] if m.provider.get(k) else None
    m.event("reconcile", k, item, ans)
    return ans


def _tool(retries, wait):
    def pay(item):
        return body(item)
    return c4a.effect(pay, approval=True, name=TOOL, reconcile=reconcile_fn, retry_on=(RetryErr,),
                      retries=retries, backoff=0.0, wait=wait)


PAY0 = _tool(0, 0.05)       # one try per call
PAY1 = _tool(1, 0.05)       # retries=1: a second try in the same call when the record decides it
PAY_T = _tool(0, 1.0)       # for two threads at once: the loser waits up to 1 s for the winner's result


def read_rows(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT seq, run_id, kind, key, payload, t FROM entries ORDER BY seq").fetchall()
    finally:
        conn.close()


def make_machine():
    class RecordMachine(RuleBasedStateMachine):
        def __init__(self):
            super().__init__()
            count("machine examples")
            self.dir = tempfile.mkdtemp(prefix="c4a-props-")
            self.path = str(Path(self.dir) / "ledger.sqlite")
            self.lg = c4a.init(self.path, secret=SECRET)
            orig = self.lg.append

            def append(run_id, kind, key, payload=None):
                if getattr(TL, "hard", False) and kind in HARD_KINDS:
                    raise Crash(f"power cut before the {kind} write")
                return orig(run_id, kind, key, payload)
            self.lg.append = append
            self.lock = threading.Lock()
            self.counter = 0
            self.ev = 0
            self.events = []
            self.provider = {}                       # effect key -> results of real outside actions
            self.approvals = {i: [] for i in ITEMS}  # [{"ev", "spent_by"}]
            self.denials = {i: [] for i in ITEMS}
            self.K = {}                              # effect key -> model of that action
            self.violations = []
            self.rows = []
            self.threaded = set()
            CUR["m"] = self
            TL.behaviors, TL.mode, TL.key, TL.hard = [], "truthful", None, False

        def teardown(self):
            conn = getattr(self.lg._local, "conn", None)
            if conn is not None:
                conn.close()
            shutil.rmtree(self.dir, ignore_errors=True)

        # ---------------------------------------------------------- the model
        def event(self, kind, k, item, info):
            with self.lock:
                self.ev += 1
                self.events.append((kind, k, item, info, self.ev))

        def st(self, k):
            return self.K.setdefault(k, {"owner": None, "state": "fresh", "spend_ev": 0, "proven": False,
                                         "result": None})

        def last_denial(self, item):
            return max(self.denials[item], default=0)

        def live_unspent(self, item):
            d = self.last_denial(item)
            return [a for a in self.approvals[item] if a["ev"] > d and a["spent_by"] is None]

        def apply(self, events):
            for kind, k, item, info, ev in events:
                s = self.st(k)
                if kind == "admit":
                    if s["owner"] not in (None, item):
                        self.violations.append(f"{k[:8]} named {s['owner']} ran for {item} (key conflict missed)")
                    if s["state"] == "done":
                        self.violations.append(f"{k[:8]} ran again after it finished")
                    elif s["state"] == "fresh":
                        live = self.live_unspent(item)
                        if not live:
                            self.violations.append(f"{k[:8]} ({item}) ran with no live, unspent yes")
                        else:
                            live[0]["spent_by"] = k
                        s["spend_ev"] = ev
                    else:
                        if not s["proven"]:
                            self.violations.append(f"{k[:8]} ran again while in doubt, with no proof it did not happen")
                        if self.last_denial(item) > s["spend_ev"]:
                            self.violations.append(f"{k[:8]} ran again after a no given since its yes was spent")
                    s["owner"], s["state"], s["proven"] = item, "open", False
                elif kind == "proven":
                    s["proven"] = True
                elif kind == "reconcile":
                    if s["state"] != "open":
                        self.violations.append(f"reconcile asked for {k[:8]} in state {s['state']}")
                    if info is None:
                        s["proven"] = True
                    elif info is not c4a.UNKNOWN:
                        s["state"], s["result"] = "done", info

        def predict(self, item, key, mode):
            """What the first decision of this call must be: (expected outcome, first event or None)."""
            k = oracle_key(item, key)
            s = self.st(k)
            if key is not None and s["owner"] not in (None, item):
                return ("conflict", None)
            if s["state"] == "done":
                return ("result", None)
            if s["state"] == "fresh":
                return ("runs", "admit") if self.live_unspent(item) else ("approval", None)
            denied_since = self.last_denial(item) > s["spend_ev"]
            if s["proven"]:
                return ("approval", None) if denied_since else ("runs", "admit")
            if mode == "lagging":
                return ("doubt", "reconcile")
            if self.provider.get(k):
                return ("reconciled", "reconcile")
            return ("approval", "reconcile") if denied_since else ("runs", "reconcile")

        # ---------------------------------------------------------- rules
        @rule(item=st.sampled_from(ITEMS))
        def approve(self, item):
            with c4a.run(RUN):
                c4a.approve(PAY0, item=item)
            self.event("approve", "", item, None)
            self.approvals[item].append({"ev": self.ev, "spent_by": None})

        @rule(item=st.sampled_from(ITEMS))
        def deny(self, item):
            with c4a.run(RUN):
                c4a.deny(PAY0, item=item)
            self.event("deny", "", item, None)
            self.denials[item].append(self.ev)

        def _call(self, item, key, behaviors, mode, retries):
            k = oracle_key(item, key)
            before = dict(self.st(k))
            expected, first = self.predict(item, key, mode)
            start = len(self.events)
            TL.behaviors, TL.mode, TL.key, TL.hard = list(behaviors), mode, key, False
            tool = PAY1 if retries else PAY0
            kwargs = {"c4a_key": key} if key is not None else {}
            outcome, value = None, None
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    with c4a.run(RUN):
                        value = tool(item=item, **kwargs)
                outcome = "returned"
            except c4a.ApprovalRequired:
                outcome = "approval"
            except c4a.EffectInDoubt:
                outcome = "doubt"
            except c4a.KeyConflict:
                outcome = "conflict"
            except (RetryErr, OtherErr, Crash) as exc:
                outcome = type(exc).__name__
            finally:
                TL.hard = False
            new = self.events[start:]
            mine = [e for e in new if e[1] == k]
            self.apply(new)
            s = self.st(k)
            label = f"call({item}, {key}, {behaviors}, {mode}, retries={retries}) in state {before['state']}"
            if outcome == "returned":
                if s["state"] == "done" and before["state"] == "done" and not same(value, before["result"]):
                    self.violations.append(f"{label}: a finished call returned {value!r}, not {before['result']!r}")
                if s["state"] == "done" and not same(value, s["result"]):
                    self.violations.append(f"{label}: returned {value!r}, reconcile said {s['result']!r}")
                if not mine and before["state"] != "done":
                    self.violations.append(f"{label}: returned {value!r} for a call that never ran")
                s["state"], s["result"] = "done", value
            # the first decision of the call
            got_first = mine[0][0] if mine else None
            if got_first != first:
                self.violations.append(f"{label}: expected first event {first}, got {got_first} ({expected})")
            if expected in ("conflict", "approval", "doubt") and first != "reconcile":
                if outcome != expected:
                    self.violations.append(f"{label}: expected {expected}, got {outcome}")
            if expected == "doubt" and (outcome != "doubt" or any(e[0] == "admit" for e in mine)):
                self.violations.append(f"{label}: reconcile could not tell, but outcome {outcome}")
            if expected == "approval" and first == "reconcile":
                if outcome != "approval" or any(e[0] == "admit" for e in mine):
                    self.violations.append(f"{label}: not done and denied since, but outcome {outcome}")
            if expected == "runs" and not any(e[0] == "admit" for e in mine):
                self.violations.append(f"{label}: a call with a live yes did not run (outcome {outcome})")
            if expected in ("result", "reconciled"):
                want = before["result"] if expected == "result" else self.provider[k][0]
                if outcome != "returned" or not same(value, want) or any(e[0] == "admit" for e in mine):
                    self.violations.append(f"{label}: expected the recorded result {want!r}, got {outcome} {value!r}")

        @rule(item=st.sampled_from(ITEMS), key=st.sampled_from(KEYS),
              behaviors=st.lists(st.sampled_from(BEHAVIORS), min_size=1, max_size=2),
              mode=st.sampled_from(("truthful", "lagging")), retries=st.booleans())
        def call(self, item, key, behaviors, mode, retries):
            self._call(item, key, behaviors, mode, retries)

        @rule(data=st.data(), mode=st.sampled_from(("truthful", "lagging")))
        def resume(self, data, mode):
            """A new call of the same arguments as an earlier one (a framework resume after a crash)."""
            seen = sorted({(e[2], e[1]) for e in self.events if e[0] == "admit"})
            if not seen:
                return
            item, k = data.draw(st.sampled_from(seen))
            key = next((x for x in KEYS if oracle_key(item, x) == k), None)
            self._call(item, key, ["ok"], mode, False)

        @rule(item=st.sampled_from(ITEMS), key1=st.sampled_from(KEYS), key2=st.sampled_from(KEYS))
        def two_threads(self, item, key1, key2):
            """Two callers at once (the same action, or two deliberate actions racing for one yes)."""
            ks = {oracle_key(item, key1), oracle_key(item, key2)}
            if any(self.st(k)["state"] == "open" for k in ks):
                return                                  # both would wait 1 s for nothing: not this rule's case
            self.threaded |= ks
            start = len(self.events)
            out = [None, None]
            barrier = threading.Barrier(2)

            def go(i, key):
                TL.behaviors, TL.mode, TL.key, TL.hard = [], "truthful", key, False
                kwargs = {"c4a_key": key} if key is not None else {}
                barrier.wait()
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        with c4a.run(RUN):
                            out[i] = ("returned", PAY_T(item=item, **kwargs))
                except (c4a.ApprovalRequired, c4a.EffectInDoubt, c4a.KeyConflict) as exc:
                    out[i] = (type(exc).__name__, None)
                except BaseException as exc:       # noqa: BLE001 - reported as a violation
                    out[i] = ("unexpected " + repr(exc), None)
            threads = [threading.Thread(target=go, args=(0, key1)), threading.Thread(target=go, args=(1, key2))]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
            with self.lock:
                new = self.events[start:]
            new.sort(key=lambda e: e[4])
            self.apply(new)
            for (kind, value), key in zip(out, (key1, key2)):
                if kind.startswith("unexpected"):
                    self.violations.append(f"two_threads({item}, {key1}, {key2}): {kind}")
                if kind == "returned":
                    s = self.st(oracle_key(item, key))
                    if s["state"] == "done" and not same(value, s["result"]):
                        self.violations.append(f"two_threads: returned {value!r}, recorded {s['result']!r}")
                    s["state"], s["result"] = "done", value
            if oracle_key(item, key1) == oracle_key(item, key2) and out[0][0] == out[1][0] == "returned":
                if not same(out[0][1], out[1][1]):
                    self.violations.append(f"two_threads: the same action gave two results {out}")

        # ---------------------------------------------------------- invariants
        @invariant()
        def no_violation(self):
            assert not self.violations, self.violations

        @invariant()
        def at_most_one_outside_action_per_key(self):
            twice = {k[:8]: v for k, v in self.provider.items() if len(v) > 1}
            assert not twice, f"outside actions per key > 1: {twice}"

        @invariant()
        def record_only_grows(self):
            rows = read_rows(self.path)
            assert rows[:len(self.rows)] == self.rows, "an earlier row of the record changed or disappeared"
            seqs = [r[0] for r in rows]
            assert all(a < b for a, b in zip(seqs, seqs[1:])), f"seq not strictly increasing: {seqs}"
            self.rows = rows

        @invariant()
        def recorded_results_match_the_outside_world(self):
            done = {}
            for r in self.rows:
                if r[2] == "tool_done":
                    done.setdefault(r[3], []).append(codec.decode(json.loads(r[4])["result"]))
            for k, results in done.items():
                assert self.provider.get(k), f"{k[:8]} is recorded as done but never happened outside"
                assert all(same(x, self.provider[k][0]) for x in results), (k[:8], results, self.provider[k])
                if k not in self.threaded:
                    assert len(results) == 1, f"{k[:8]} has {len(results)} results in the record"
            for k, s in self.K.items():
                if s["state"] == "done":
                    assert k in done, f"{k[:8]} returned a result but the record has none"

    return RecordMachine


# ================================================================== b. canonical() and effect_key()
def json_values(text=None):
    text = text if text is not None else st.text()
    leaf = (st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | text)
    return st.recursive(leaf, lambda c: st.lists(c, max_size=4) | st.dictionaries(text, c, max_size=4), max_leaves=12)


def reorder(v, rnd):
    """The same value with every dict's insertion order shuffled."""
    if isinstance(v, dict):
        items = list(v.items())
        rnd.shuffle(items)
        return {k: reorder(x, rnd) for k, x in items}
    if isinstance(v, list):
        return [reorder(x, rnd) for x in v]
    return v


def tweak(v, rnd):
    """A value that differs in exactly one place."""
    if isinstance(v, dict) and v and rnd.random() < 0.7:
        k = rnd.choice(sorted(v))
        return dict(v, **{k: tweak(v[k], rnd)}) if rnd.random() < 0.8 else {**v, k + "\x01": v[k]}
    if isinstance(v, list) and v and rnd.random() < 0.7:
        i = rnd.randrange(len(v))
        return v[:i] + [tweak(v[i], rnd)] + v[i + 1:]
    if isinstance(v, bool):
        return not v
    if isinstance(v, int):
        return float(v) if rnd.random() < 0.5 and abs(v) < 2 ** 52 else v + 1
    if isinstance(v, float):
        return v + 1.0 if abs(v) < 1e15 else -v if v != 0 else (-0.0 if repr(v) == "0.0" else 0.0)
    if isinstance(v, str):
        return v + "x"
    if v is None:
        return 0
    return [v]


class Opaque:
    pass


def unstable_values():
    def gen():
        yield object()
        yield Opaque()
        yield lambda: 1
        yield 1 + 2j
        yield memoryview(b"ab")
        yield iter([1])
        yield threading.Lock()
        yield Opaque
    return st.sampled_from(list(gen()))


def run_canonical(cfg):
    n = cfg["examples"]
    s = settings(max_examples=n, deadline=None, derandomize=cfg["derandomize"], database=None,
                 suppress_health_check=list(HealthCheck), print_blob=True)

    @s
    @given(v=json_values(), rnd=st.randoms(use_true_random=False))
    def order_free(v, rnd):
        count("canonical order")
        w = reorder(v, rnd)
        assert canonical(v) == canonical(w)
        assert effect_key("r", "t", {"a": v}) == effect_key("r", "t", {"a": w})

    @s
    @given(a=json_values(), b=json_values())
    def injective(a, b):
        count("canonical injective")
        assert (canonical(a) == canonical(b)) == json_same(a, b), (a, b, canonical(a), canonical(b))
        assert (effect_key("r", "t", {"x": a}) == effect_key("r", "t", {"x": b})) == json_same(a, b)

    @s
    @given(a=json_values(), rnd=st.randoms(use_true_random=False))
    def one_change(a, rnd):
        count("canonical one change")
        b = tweak(a, rnd)
        if not json_same(a, b):
            assert canonical(a) != canonical(b), (a, b)
            assert effect_key("r", "t", {"x": a}) != effect_key("r", "t", {"x": b})

    names = st.text(alphabet=st.characters(blacklist_characters="\x00"), max_size=6)

    @s
    @given(r1=names, t1=names, r2=names, t2=names, a=json_values(), b=json_values())
    def key_parts(r1, t1, r2, t2, a, b):
        """Run id and tool name are part of the key (NUL is not used in them: see docs/PROPERTIES.md)."""
        count("effect_key parts")
        if (r1, t1) != (r2, t2) or not json_same(a, b):
            assert effect_key(r1, t1, {"x": a}) != effect_key(r2, t2, {"x": b})

    @s
    @given(n=st.integers(min_value=-(2 ** 52), max_value=2 ** 52))
    def number_spelling(n):
        """The code's rule: a key is the JSON spelling, so 10, 10.0 and True are three different actions."""
        count("number spelling")
        assert effect_key("r", "t", {"x": n}) != effect_key("r", "t", {"x": float(n)})
        if n in (0, 1):
            assert effect_key("r", "t", {"x": n}) != effect_key("r", "t", {"x": bool(n)})
        assert canonical({"x": (n, n)}) == canonical({"x": [n, n]})      # a tuple is spelled as a list

    @s
    @given(v=json_values(), bad=unstable_values(), rnd=st.randoms(use_true_random=False))
    def unstable_raises(v, bad, rnd):
        count("unstable raises")
        holder = [v]
        cur = holder
        for _ in range(rnd.randrange(4)):
            nxt = [] if rnd.random() < 0.5 else {}
            if isinstance(cur, list):
                cur.append(nxt)
            else:
                cur["k"] = nxt
            cur = nxt
        if isinstance(cur, list):
            cur.append(bad)
        else:
            cur["bad"] = bad
        for wrap in (lambda x: x, lambda x: {"s": frozenset([("t", 1), (0, x)])} if _hashable(x) else x):
            try:
                canonical(wrap(holder))
            except TypeError:
                continue
            raise AssertionError(f"no TypeError for {bad!r}")

    @s
    @given(depth=st.integers(min_value=501, max_value=700))
    def too_deep(depth):
        count("too deep")
        v = []
        for _ in range(depth):
            v = [v]
        try:
            canonical(v)
        except TypeError:
            return
        raise AssertionError("no TypeError for a value nested deeper than 500")

    for label, fn in [("canonical: dict order never changes the text or the key", order_free),
                      ("canonical: two JSON values get the same key exactly when they are the same value", injective),
                      ("canonical: a one-place change always changes the key", one_change),
                      ("effect_key: a different run id, tool or arguments gives a different key", key_parts),
                      ("canonical: the code's rule - 10, 10.0 and True are different keys; a tuple is a list", number_spelling),
                      ("canonical: an object with no stable text form raises TypeError, nested anywhere", unstable_raises),
                      ("canonical: nesting deeper than 500 raises TypeError", too_deep)]:
        run_prop(label, fn)
    sets_same_in_every_process(cfg)


def _hashable(x):
    try:
        hash(x)
        return True
    except TypeError:
        return False


SET_SCRIPT = ("import json, sys; from call4agents.ledger import canonical; "
              "print(json.dumps([canonical(set(v)) for v in json.load(sys.stdin)]))")


def sets_same_in_every_process(cfg):
    """The _stable promise: "the same in every process". Sets of text iterate in a different order per hash seed."""
    rnd = random.Random(1106)
    batch = [[f"{rnd.random():.6f}"[2:2 + rnd.randrange(1, 7)] + chr(rnd.randrange(97, 123))
              for _ in range(rnd.randrange(1, 12))] for _ in range(cfg["examples"])]
    outs = []
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        r = subprocess.run([sys.executable, "-c", SET_SCRIPT], input=json.dumps(batch), capture_output=True,
                           text=True, env=env, timeout=120)
        outs.append(r.stdout.strip() if r.returncode == 0 else r.stderr[-300:])
    count_n = len(batch)
    COUNTS["sets across processes"] = count_n
    check(f"canonical: a set gives the same text under 3 hash seeds ({count_n} sets)",
          outs[0] == outs[1] == outs[2] and outs[0].startswith("["), [o[:120] for o in outs])


# ================================================================== c. codec
def hashables():
    leaf = (st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(max_size=5)
            | st.binary(max_size=5) | st.dates() | st.decimals(allow_nan=False, allow_infinity=False, places=3))
    return st.recursive(leaf, lambda c: st.tuples(c, c) | st.frozensets(c, max_size=3), max_leaves=5)


def codec_values():
    tz = st.none() | st.just(_dt.timezone.utc) | st.builds(lambda m: _dt.timezone(_dt.timedelta(minutes=m)),
                                                           st.integers(min_value=-23 * 60, max_value=23 * 60))
    leaf = (st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text()
            | st.binary(max_size=8) | st.binary(max_size=8).map(bytearray) | st.datetimes(timezones=tz)
            | st.dates() | st.times(timezones=tz) | st.decimals(allow_nan=False, allow_infinity=False))
    from tests import c4a_codec_types as ct

    def containers(c):
        return (st.lists(c, max_size=3) | st.tuples(c, c)
                | st.dictionaries(st.text(max_size=4) | st.just(codec.TAG), c, max_size=3)
                | st.dictionaries(hashables(), c, max_size=3)
                | st.sets(hashables(), max_size=3) | st.frozensets(hashables(), max_size=3)
                | st.builds(Point, x=st.integers(), y=c)
                | st.builds(ct.Order, id=st.integers(), items=st.tuples(c))
                | st.builds(Model, st.dictionaries(st.text(max_size=3), st.integers() | st.text(max_size=3), max_size=3)))
    return st.recursive(leaf, containers, max_leaves=10)


HOSTILE_CLS = ["os:system", "os:remove", "os:_exit", "os:getcwd", "os:environ", "builtins:eval", "builtins:exec",
               "builtins:open", "builtins:__import__", "builtins:type", "builtins:object", "builtins:SystemExit",
               "builtins:dict", "builtins:getattr", "subprocess:Popen", "subprocess:run", "shutil:rmtree",
               "importlib:import_module", "dataclasses:dataclass", "dataclasses:make_dataclass",
               "dataclasses:Field", "json:loads", "pathlib:Path", "decimal:Decimal", "datetime:datetime",
               "tests.c4a_codec_types:canary", "tests.c4a_codec_types:NotAModel",
               "tests.c4a_codec_types:Order.__init__", "tests.c4a_codec_types:Order.__class__",
               "tests.test_properties:canary_fn", "tests.test_properties:OnlyValidate", "tests.test_properties:Plain",
               "tests.test_properties:Point.__init__", "tests.test_properties:Point.__class__",
               "tests.test_properties:Point", "tests.test_properties:Model", "tests.test_properties:check",
               "tests.test_properties:Opaque", "tests.c4a_codec_types:Order", "tests.c4a_codec_types:FakeModel",
               "zz_c4a_no_such_module:X", "os", "", ":", "os:", "os:path.join", "builtins:object.__subclasses__"]
TAGS = ["dataclass", "model", "dict", "tuple", "set", "frozenset", "bytes", "datetime", "date", "time", "decimal"]
SAFE_MODULES = ["os", "builtins", "subprocess", "shutil", "dataclasses", "tests.c4a_codec_types", "tests.test_properties"]


def hostile_payloads():
    ident = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,12}", fullmatch=True)
    cls = (st.sampled_from(HOSTILE_CLS)
           | st.builds(lambda m, q: f"{m}:{q}", st.sampled_from(SAFE_MODULES), ident)
           | st.builds(lambda m, q: f"{m}:{q}", st.sampled_from(SAFE_MODULES), st.text(max_size=10))
           | st.text(max_size=12).map(lambda t: "zz_c4a_missing_" + t))
    arg_dicts = st.dictionaries(st.sampled_from(["command", "args", "path", "x", "y", "data", "cmd", "id", "items"])
                                | st.text(max_size=4), json_values(st.text(max_size=6)), max_size=3)
    tagged = st.fixed_dictionaries({codec.TAG: st.sampled_from(TAGS) | st.text(max_size=6), "cls": cls,
                                    "v": arg_dicts | json_values(st.text(max_size=6))})

    def nest(c):
        return (st.lists(c, max_size=3) | st.dictionaries(st.text(max_size=3), c, max_size=3)
                | st.fixed_dictionaries({codec.TAG: st.sampled_from(["tuple", "set", "frozenset", "dict"]),
                                         "v": st.lists(c, max_size=3)})
                | st.fixed_dictionaries({codec.TAG: st.just("dataclass"), "cls": cls, "v": st.dictionaries(
                    st.sampled_from(["x", "y", "id", "items"]), c, max_size=2)}))
    return st.recursive(json_values(st.text(max_size=6)) | tagged, nest, max_leaves=8)


ALLOWED = (type(None), bool, int, float, str, list, dict, tuple, set, frozenset, bytes, _dt.datetime, _dt.date,
           _dt.time, decimal.Decimal)


def foreign_objects(v, out):
    """Every object in a decoded value that is not plain data, a dataclass or a model."""
    if isinstance(v, (list, tuple, set, frozenset)):
        for x in v:
            foreign_objects(x, out)
    elif isinstance(v, dict):
        for k, x in v.items():
            foreign_objects(k, out)
            foreign_objects(x, out)
    elif dataclasses.is_dataclass(v) and not isinstance(v, type):
        for f in dataclasses.fields(v):
            foreign_objects(getattr(v, f.name), out)
    elif hasattr(type(v), "model_validate") and hasattr(type(v), "model_dump") and not isinstance(v, type):
        pass
    elif type(v) not in ALLOWED:
        out.append(type(v))
    return out


DANGEROUS = [(os, "system"), (os, "remove"), (os, "unlink"), (os, "rmdir"), (os, "_exit"), (shutil, "rmtree"),
             (subprocess, "Popen"), (subprocess, "run"), (subprocess, "call"), (builtins, "eval"), (builtins, "exec")]


def run_codec(cfg):
    n = cfg["examples"]
    s = settings(max_examples=n, deadline=None, derandomize=cfg["derandomize"], database=None,
                 suppress_health_check=list(HealthCheck), print_blob=True)

    @s
    @given(v=codec_values())
    def round_trip(v):
        count("codec round trip")
        enc = codec.encode(v)
        assert same(codec.decode(enc), v), (v, enc)
        stored = json.loads(canonical(enc))         # what the record really keeps: canonical JSON text
        assert same(codec.decode(stored), v), (v, stored)

    @s
    @given(v=hostile_payloads())
    def hostile(v):
        count("codec hostile")
        calls = []
        saved = [(mod, name, getattr(mod, name)) for mod, name in DANGEROUS]
        for mod, name in DANGEROUS:
            setattr(mod, name, (lambda n_: lambda *a, **k: calls.append(n_))(name))
        try:
            before = len(canary_calls())
            try:
                out = codec.decode(json.loads(json.dumps(v)))
            except Exception:       # noqa: BLE001 - refusing (any error) is allowed; calling is not
                out = None
            except BaseException as exc:   # noqa: BLE001
                raise AssertionError(f"decode raised {exc!r}, not an Exception") from None
        finally:
            for mod, name, orig in saved:
                setattr(mod, name, orig)
        assert not calls, f"decode called {calls}"
        assert len(canary_calls()) == before, f"decode called a canary: {canary_calls()[before:]}"
        foreign = foreign_objects(out, [])
        assert not foreign, f"decode returned objects of {foreign}"

    run_prop("codec: encode, store as canonical JSON, decode gives an equal value of the same types", round_trip)
    run_prop("codec: decode of any JSON (hostile class names too) calls nothing and builds only data, "
             "dataclasses or models", hostile)
    # the fixed hostile list, every entry once, each tag (not left to chance)
    bad = []
    for path in HOSTILE_CLS:
        for tag in ("dataclass", "model"):
            for v in ({"command": "echo pwned"}, {}, {"x": 1}):
                before = len(canary_calls())
                try:
                    out = codec.decode({codec.TAG: tag, "cls": path, "v": v})
                except Exception:      # noqa: BLE001
                    out = None
                if len(canary_calls()) != before or foreign_objects(out, []):
                    bad.append((tag, path, v))
    check(f"codec: each of {len(HOSTILE_CLS)} hostile class paths x 2 tags is refused or builds only allowed types",
          not bad, bad[:5])


# ================================================================== d. record file fuzzing
FUZZ_TOOL = "props.fuzz"


def build_base(root):
    """A record with: A done (result 'RA'), B in doubt (intent, no result), C approved and unused, D never approved."""
    os.environ.pop("C4A_SECRET", None)
    os.environ.pop("C4A_OLD_SECRETS", None)
    path = str(Path(root) / "ledger.sqlite")
    lg = c4a.init(path)

    @c4a.effect(approval=True, name=FUZZ_TOOL, wait=0.0)
    def act(item):
        if item == "B":
            raise Crash("B dies during the action")
        return "R" + item
    orig = lg.append

    def append(run_id, kind, key, payload=None):
        if kind == "tool_error":
            raise Crash("power cut")
        return orig(run_id, kind, key, payload)
    lg.append = append
    with c4a.run("fuzz"):
        for item in ("A", "B", "C"):
            c4a.approve(FUZZ_TOOL, item=item)
        assert act(item="A") == "RA"
        try:
            act(item="B")
        except Crash:
            pass
    conn = lg._local.conn
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    for extra in ("-wal", "-shm"):
        if os.path.exists(path + extra):
            os.remove(path + extra)
    return Path(path).read_bytes(), (Path(root) / "secret").read_bytes()


CLEAR = (LedgerError, sqlite3.DatabaseError)


def fuzz_once(root, db, secret, ran):
    """Open a damaged copy and ask about A, B, C, D. Returns (outcomes, problems)."""
    d = Path(tempfile.mkdtemp(prefix="c4a-fz-", dir=root))
    (d / "ledger.sqlite").write_bytes(db)
    if secret is not None:
        (d / "secret").write_bytes(secret)
    problems, outcomes = [], {}
    lg = None
    try:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                lg = c4a.init(str(d / "ledger.sqlite"))
        except CLEAR as exc:
            return {"open": type(exc).__name__}, []
        except Exception as exc:       # noqa: BLE001
            return {"open": repr(exc)}, [f"opening raised an unclear error {exc!r}"]

        @c4a.effect(approval=True, name=FUZZ_TOOL, wait=0.0)
        def act(item):
            ran.append(item)
            return "R" + item
        for item in ("A", "B", "C", "D"):
            before = len(ran)
            try:
                with c4a.run("fuzz"):
                    out = ("returned", act(item=item))
            except CLEAR as exc:
                out = ("clear", type(exc).__name__)
            except (c4a.EffectInDoubt, c4a.ApprovalRequired) as exc:
                out = ("refused", type(exc).__name__)
            except Exception as exc:   # noqa: BLE001
                out = ("unclear", repr(exc)[:120])
                problems.append(f"{item}: unclear error {exc!r}"[:200])
            outcomes[item] = out
            runs = len(ran) - before
            if item in ("A", "B", "D") and runs:
                problems.append(f"{item} ran ({'done' if item == 'A' else 'in doubt' if item == 'B' else 'never approved'})")
            if item == "C" and runs > 1:
                problems.append("C ran twice")
            if item == "B" and out[0] == "returned":
                problems.append(f"B (in doubt) came back as done: {out[1]!r}")
            if item == "D" and out[0] == "returned":
                problems.append("D (never approved) returned a result")
        return outcomes, problems
    finally:
        if lg is not None:
            conn = getattr(lg._local, "conn", None)
            if conn is not None:
                conn.close()
        shutil.rmtree(d, ignore_errors=True)


def damage(data, how, rnd):
    b = bytearray(data)
    if how == "flip":
        for _ in range(rnd.randrange(1, 4)):
            b[rnd.randrange(len(b))] ^= 1 << rnd.randrange(8)
    elif how == "flip-page":                     # aim at one 4 KiB page (the entries and indexes live in a few)
        page = rnd.randrange(max(1, len(b) // 4096))
        for _ in range(rnd.randrange(1, 4)):
            b[page * 4096 + rnd.randrange(min(4096, len(b) - page * 4096))] ^= 1 << rnd.randrange(8)
    elif how == "random-run":
        start = rnd.randrange(len(b))
        for i in range(start, min(len(b), start + rnd.randrange(1, 64))):
            b[i] = rnd.randrange(256)
    elif how == "zero-run":
        start = rnd.randrange(len(b))
        b[start:start + rnd.randrange(1, 512)] = bytes(len(b[start:start + 512]))[:len(b[start:start + rnd.randrange(1, 512)])]
    elif how == "truncate":
        del b[rnd.randrange(len(b)):]
    return bytes(b)


def run_files(cfg):
    root = tempfile.mkdtemp(prefix="c4a-props-files-")
    try:
        db, secret = build_base(str(Path(root) / "base"))
        ran = []
        outcomes, problems = fuzz_once(root, db, secret, ran)
        check("files: the undamaged copy answers A=RA, B in doubt, C runs once, D refused",
              outcomes.get("A") == ("returned", "RA") and outcomes.get("B") == ("refused", "EffectInDoubt")
              and outcomes.get("C") == ("returned", "RC") and outcomes.get("D") == ("refused", "ApprovalRequired")
              and ran == ["C"] and not problems, (outcomes, ran, problems))
        stats = {"cases": 0, "clear error": 0, "wrong A result": 0, "wrong A result, verify ok": 0}
        s = settings(max_examples=cfg["examples"], deadline=None, derandomize=cfg["derandomize"], database=None,
                     suppress_health_check=list(HealthCheck), print_blob=True)

        @s
        @given(how=st.sampled_from(["flip", "flip-page", "random-run", "zero-run", "truncate"]),
               sec=st.sampled_from(["keep", "keep", "keep", "flip", "truncate", "empty", "missing"]),
               rnd=st.randoms(use_true_random=False))
        def damaged(how, sec, rnd):
            count("files damaged")
            stats["cases"] += 1
            bad_db = damage(db, how, rnd) if sec == "keep" or rnd.random() < 0.5 else db
            if sec == "keep":
                bad_secret = secret
            elif sec == "flip":
                x = bytearray(secret)
                x[rnd.randrange(len(x))] ^= 1 << rnd.randrange(8)
                bad_secret = bytes(x)
            elif sec == "truncate":
                bad_secret = secret[:rnd.randrange(1, len(secret))]
            elif sec == "empty":
                bad_secret = b""
            else:
                bad_secret = None
            got, probs = fuzz_once(root, bad_db, bad_secret, [])
            if any(o[0] == "clear" for o in got.values()) or "open" in got:
                stats["clear error"] += 1
            a = got.get("A")
            if a and a[0] == "returned" and a[1] != "RA":
                stats["wrong A result"] += 1
                if verify_ok(root, bad_db):
                    stats["wrong A result, verify ok"] += 1
            assert not probs, (how, sec, got, probs)

        run_prop("files: a damaged record or secret gives a clear error or a safe answer: a done or in-doubt call "
                 "never runs again, an unapproved call never runs, every error is LedgerError, sqlite3.DatabaseError "
                 "or the kit's own refusal", damaged)
        print(f"        measured (not asserted): {stats}")
        COUNTS["files stats"] = stats
    finally:
        shutil.rmtree(root, ignore_errors=True)


def verify_ok(root, data):
    """`call4agents verify` on this file says integrity ok."""
    d = Path(tempfile.mkdtemp(prefix="c4a-fv-", dir=root))
    try:
        (d / "ledger.sqlite").write_bytes(data)
        conn = sqlite3.connect(str(d / "ledger.sqlite"))
        try:
            return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        except sqlite3.DatabaseError:
            return False
        finally:
            conn.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ================================================================== race: a racer while the action still runs
def run_race(cfg):
    """With reconcile=, a second caller in the same process must not run the action while the first still runs
    it (docstring: "Callers racing on the same effect: exactly one runs it"). Fix 2026-11-11 (PR1)."""
    d = tempfile.mkdtemp(prefix="c4a-props-race-")
    lg = c4a.init(str(Path(d) / "ledger.sqlite"), secret=SECRET)
    provider, started = [], threading.Event()

    def rec(item):
        return provider[0] if provider else None

    @c4a.effect(approval=True, name="props.slow", reconcile=rec, wait=0.2)
    def slow(item):
        started.set()
        time.sleep(0.8)                 # slower than wait=: the racer gives up waiting while this still runs
        provider.append(f"paid {item} #{len(provider) + 1}")
        return provider[-1]
    out = []

    def go():
        with c4a.run("race"):
            try:
                out.append(slow(item="a"))
            except c4a.EffectInDoubt:
                out.append("in doubt")
    with c4a.run("race"):
        c4a.approve(slow, item="a")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t1 = threading.Thread(target=go)
        t1.start()
        started.wait(5)
        t2 = threading.Thread(target=go)
        t2.start()
        t1.join()
        t2.join()
        with c4a.run("race"):
            later = slow(item="a")      # after both: the recorded result, nothing new
    conn = getattr(lg._local, "conn", None)
    if conn is not None:
        conn.close()
    shutil.rmtree(d, ignore_errors=True)
    check("PR1 a racer does not run a reconciled action again while the first caller still runs it",
          len(provider) == 1 and sorted(out) == ["in doubt", "paid a #1"] and later == "paid a #1",
          (provider, out, later))


# ================================================================== driver
def run_prop(label, fn):
    try:
        fn()
        check(label, True)
    except Exception as exc:      # noqa: BLE001 - Hypothesis already printed the falsifying example
        check(label, False, f"{type(exc).__name__}: {str(exc)[:300]}")


def run_machine(cfg):
    Machine = make_machine()
    s = settings(max_examples=cfg["machine_examples"], stateful_step_count=cfg["steps"], deadline=None,
                 derandomize=cfg["derandomize"], database=None, suppress_health_check=list(HealthCheck),
                 print_blob=True)
    if cfg["seed"] is not None:
        hypothesis.seed(cfg["seed"])(Machine)
    label = ("machine: approve / deny / call / crash before or after / retry_on / other errors / resume / "
             "reconcile (truthful or lagging) / two threads keep: <= 1 outside action per key, every action had a "
             "live yes for its exact arguments, one yes per action, finished calls replay their result, "
             "the record only grows")
    try:
        run_state_machine_as_test(Machine, settings=s)
        check(label, True)
    except Exception as exc:      # noqa: BLE001
        check(label, False, f"{type(exc).__name__}: {str(exc)[:600]}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--require-hypothesis", action="store_true")
    p.add_argument("--long", action="store_true")
    p.add_argument("--only", choices=["machine", "canonical", "codec", "files", "race"])
    p.add_argument("--seed", type=int)
    a = p.parse_args(argv)
    if not HAVE_HYPOTHESIS:
        print("  skip  property tests: hypothesis is not installed (pip install hypothesis)")
        if a.require_hypothesis:
            check("hypothesis is installed (--require-hypothesis)", False)
            return 1
        return 0
    seed = None
    if a.long:
        seed = a.seed if a.seed is not None else random.randrange(2 ** 32)
        cfg = {"machine_examples": 400, "steps": 50, "examples": 3000, "derandomize": False, "seed": seed}
    else:
        cfg = {"machine_examples": 60, "steps": 30, "examples": 300, "derandomize": True, "seed": None}
    print(f"hypothesis {hypothesis.__version__}, {'long' if a.long else 'default'} run, "
          + (f"seed {seed} (rerun: --long --seed {seed})" if seed is not None
             else "derandomized (the same examples every run)"))
    t0 = time.monotonic()
    groups = {"machine": run_machine, "canonical": run_canonical, "codec": run_codec, "files": run_files,
              "race": run_race}
    for name, fn in groups.items():
        if a.only in (None, name):
            if seed is not None and name != "machine":
                random.seed(seed)
                hypothesis_seed = hypothesis.seed(seed)
                cfg["wrap"] = hypothesis_seed
            t = time.monotonic()
            fn(cfg)
            print(f"        ({name}: {time.monotonic() - t:.1f} s)")
    print(f"examples run: {json.dumps({k: v for k, v in COUNTS.items() if k != 'files stats'})}")
    print(f"{'all passed' if not FAILED else str(len(FAILED)) + ' FAILED'} in {time.monotonic() - t0:.1f} s")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
