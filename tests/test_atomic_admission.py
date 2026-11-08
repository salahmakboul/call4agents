"""Atomic approval admission. Offline disposable fixtures; no real effects.

Run: python -m tests.test_atomic_admission [--postgres]
The PostgreSQL mode always starts its own disposable pgserver.
"""
import sys
import os
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

import call4agents as c4a
from call4agents.ledger import effect_key, intent_key


class InterruptedAdmission(BaseException):
    pass


class FailBeforeIntent:
    def __init__(self, conn, kind="tool_intent"):
        self.conn = conn
        self.kind = kind

    def __enter__(self):
        self.conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self.conn.__exit__(*exc)

    def execute(self, sql, params=()):
        if sql.startswith("INSERT INTO entries") and params[1] == self.kind:
            raise InterruptedAdmission()
        return self.conn.execute(sql, params)


class AtomicAdmission(unittest.TestCase):
    postgres = "--postgres" in sys.argv

    @classmethod
    def setUpClass(cls):
        cls.server = None
        if cls.postgres:
            import pgserver
            cls.data = Path(tempfile.gettempdir()) / ("c4a-atomic-pg-" + uuid.uuid4().hex)
            cls.data.mkdir()
            cls.server = pgserver.get_server(str(cls.data), cleanup_mode="stop")
            cls.dsn = cls.server.get_uri()

    @classmethod
    def tearDownClass(cls):
        if cls.server is not None:
            cls.server.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="c4a-atomic-")
        self.addCleanup(self.tmp.cleanup)
        self.path = self.dsn if self.postgres else str(Path(self.tmp.name) / "record.sqlite")
        self.lg = c4a.init(self.path, secret=b"synthetic-approval-fixture")
        self.run_id = "atomic-" + uuid.uuid4().hex
        self.calls = []

        @c4a.effect(name="atomic.pay", approval=True, wait=0)
        def pay(amount):
            self.calls.append(amount)
            return amount

        self.pay = pay
        self.lg.record_approval(self.run_id, "atomic.pay", {"amount": 10})

    def tearDown(self):
        conn = self.lg._conn()
        (conn.raw if self.postgres else conn).close()

    def test_failure_before_intent_rolls_back_entire_admission(self):
        original = self.lg._conn
        self.lg._conn = lambda: FailBeforeIntent(original())
        try:
            with c4a.run(self.run_id), self.assertRaises(InterruptedAdmission):
                self.pay(10, c4a_key="order")
        finally:
            self.lg._conn = original
        rows = self.lg.entries(self.run_id)
        self.assertEqual([r["kind"] for r in rows], ["approval"])
        self.assertEqual(self.calls, [])
        with c4a.run(self.run_id):
            self.assertEqual(self.pay(10, c4a_key="order"), 10)
            self.assertEqual(self.pay(10, c4a_key="order"), 10)
        self.assertEqual(self.calls, [10])

    def test_unapproved_and_denied_calls_do_not_run(self):
        with c4a.run(self.run_id):
            with self.assertRaises(c4a.ApprovalRequired):
                self.pay(20, c4a_key="order")
            c4a.deny(self.pay, amount=10)
            with self.assertRaises(c4a.ApprovalRequired):
                self.pay(10, c4a_key="order")
        self.assertEqual(self.calls, [])
        self.assertFalse(any(r["kind"] == "tool_intent" for r in self.lg.entries(self.run_id)))

    def test_legacy_use_approval_remains_single_use(self):
        args = {"amount": 10}
        self.assertTrue(self.lg.use_approval(self.run_id, "atomic.pay", args, "legacy-a"))
        self.assertFalse(self.lg.use_approval(self.run_id, "atomic.pay", args, "legacy-b"))

    def test_legacy_approved_effect_without_intent_resumes_but_partial_use_stays_spent(self):
        args = {"amount": 10}
        old_effect = effect_key(self.run_id, "atomic.pay", {"__c4a_key__": "old"})
        self.assertTrue(self.lg.use_approval(self.run_id, "atomic.pay", args, old_effect))
        with c4a.run(self.run_id):
            self.assertEqual(self.pay(10, c4a_key="old"), 10)
            with self.assertRaises(c4a.ApprovalRequired):
                self.pay(10, c4a_key="new")
        self.assertEqual(self.calls, [10])

        self.run_id = "partial-" + uuid.uuid4().hex
        self.lg.record_approval(self.run_id, "atomic.pay", args)
        approval = self.lg.matching_approvals(self.run_id, "atomic.pay", args)[0]
        self.lg.append(self.run_id, "approval_used", f"{self.run_id}\x00atomic.pay\x00{approval}",
                       {"effect": "historical-partial"})
        with c4a.run(self.run_id), self.assertRaises(c4a.ApprovalRequired):
            self.pay(10, c4a_key="partial")
        self.assertEqual(self.calls, [10])

    def test_legacy_in_doubt_intent_cannot_use_old_approval_after_denial(self):
        args = {"amount": 10}
        effect = effect_key(self.run_id, "atomic.pay", {"__c4a_key__": "in-doubt"})
        self.assertTrue(self.lg.use_approval(self.run_id, "atomic.pay", args, effect))
        self.lg.append(self.run_id, "tool_intent", intent_key(effect),
                       {"tool": "atomic.pay", "args": args})
        self.lg.record_denial(self.run_id, "atomic.pay", args)
        with c4a.run(self.run_id), self.assertRaises(c4a.ApprovalRequired):
            self.pay(10, c4a_key="in-doubt")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.lg.count("tool_intent", intent_key(effect)), 1)

    def pg_ledger(self):
        from call4agents.stores.postgres import PostgresLedger
        return PostgresLedger(self.dsn, secret=b"synthetic-approval-fixture")

    @unittest.skipUnless(postgres, "PostgreSQL admission boundary")
    def test_uncommitted_raw_denial_blocks_admission_and_connection_recovers(self):
        import psycopg
        writer = psycopg.connect(self.dsn)
        contender = self.pg_ledger()
        args = {"amount": 10}
        effect = effect_key(self.run_id, "atomic.pay", {"__c4a_key__": "raw-denial"})
        try:
            writer.execute("INSERT INTO entries (run_id, kind, key, payload, t) VALUES (%s, %s, %s, %s, %s)",
                           (self.run_id, "denial", f"{self.run_id}\x01\x02atomic.pay",
                            '{"mac":"' + self.lg.sign("atomic.pay", args) + '"}', time.time()))
            raw = contender._conn().raw
            raw.execute("SET lock_timeout = '250ms'")
            raw.commit()
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                contender.claim_approved(self.run_id, "atomic.pay", args, effect, intent_key(effect),
                                         {"tool": "atomic.pay", "args": args})
            writer.commit()
            self.assertIsNone(contender.claim_approved(self.run_id, "atomic.pay", args, effect,
                                                       intent_key(effect), {"tool": "atomic.pay", "args": args}))
            self.assertEqual(contender.count("tool_intent", intent_key(effect)), 0)
        finally:
            writer.rollback()
            writer.close()
            contender._conn().raw.close()

    @unittest.skipUnless(postgres, "PostgreSQL admission boundary")
    def test_api_denial_waits_for_admission_commit(self):
        import psycopg
        entered, release = threading.Event(), threading.Event()
        contender = self.pg_ledger()
        denial = self.pg_ledger()
        original = contender._begin_admission
        errors = []
        args = {"amount": 10}
        effect = effect_key(self.run_id, "atomic.pay", {"__c4a_key__": "held"})

        def held(conn):
            original(conn)
            entered.set()
            if not release.wait(10):
                raise AssertionError("admission release timed out")

        def admit():
            try:
                self.assertTrue(contender.claim_approved(self.run_id, "atomic.pay", args, effect,
                                                         intent_key(effect), {"tool": "atomic.pay", "args": args}))
            except BaseException as exc:
                errors.append(exc)
            finally:
                contender._conn().raw.close()

        contender._begin_admission = held
        thread = threading.Thread(target=admit)
        thread.start()
        try:
            self.assertTrue(entered.wait(10), "admission did not acquire lock")
            raw = denial._conn().raw
            raw.execute("SET lock_timeout = '250ms'")
            raw.commit()
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                denial.record_denial(self.run_id, "atomic.pay", args)
            release.set()
            thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            denial.record_denial(self.run_id, "atomic.pay", args)
            self.assertIsNone(denial.claim_approved(self.run_id, "atomic.pay", args,
                                                   effect_key(self.run_id, "atomic.pay", {"__c4a_key__": "later"}),
                                                   "later#1", {"tool": "atomic.pay", "args": args}))
        finally:
            release.set()
            thread.join(10)
            contender._conn().raw.close()
            denial._conn().raw.close()

    @unittest.skipUnless(postgres, "PostgreSQL admission boundary")
    def test_admission_lock_is_released_before_effect(self):
        import psycopg
        @c4a.effect(name="atomic.unlocked", approval=True, wait=0)
        def unlocked():
            with psycopg.connect(self.dsn) as raw:
                raw.execute("SET lock_timeout = '250ms'")
                raw.execute("INSERT INTO entries (run_id, kind, key, payload, t) VALUES (%s, %s, %s, %s, %s)",
                            (self.run_id, "effect_probe", "probe", None, time.time()))
            return 1
        self.lg.record_approval(self.run_id, "atomic.unlocked", {})
        with c4a.run(self.run_id):
            self.assertEqual(unlocked(), 1)
        self.assertEqual(self.lg.count("effect_probe", "probe"), 1)

    @unittest.skipUnless(postgres, "PostgreSQL admission boundary")
    def test_ticket_denial_in_flight_blocks_admission(self):
        import psycopg
        args = {"amount": 10}
        ticket = self.lg.issue_ticket(self.run_id, "atomic.pay", args, 60)
        decision = self.pg_ledger()
        contender = self.pg_ledger()
        inserted, release = threading.Event(), threading.Event()
        original = decision._insert
        errors = []

        def held_insert(conn, run_id, kind, key, payload=None):
            result = original(conn, run_id, kind, key, payload)
            if kind == "denial":
                inserted.set()
                if not release.wait(10):
                    raise AssertionError("ticket denial release timed out")
            return result

        def decide():
            try:
                decision.decide_ticket(ticket, "deny")
            except BaseException as exc:
                errors.append(exc)
            finally:
                decision._conn().raw.close()

        decision._insert = held_insert
        thread = threading.Thread(target=decide)
        thread.start()
        try:
            self.assertTrue(inserted.wait(10), "ticket denial was not inserted")
            effect = effect_key(self.run_id, "atomic.pay", {"__c4a_key__": "ticket"})
            raw = contender._conn().raw
            raw.execute("SET lock_timeout = '250ms'")
            raw.commit()
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                contender.claim_approved(self.run_id, "atomic.pay", args, effect, intent_key(effect),
                                         {"tool": "atomic.pay", "args": args})
            release.set()
            thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertIsNone(contender.claim_approved(self.run_id, "atomic.pay", args, effect,
                                                       intent_key(effect), {"tool": "atomic.pay", "args": args}))
            self.assertEqual(contender.count("tool_intent", intent_key(effect)), 0)
        finally:
            release.set()
            thread.join(10)
            decision._conn().raw.close()
            contender._conn().raw.close()

    def worker(self, role, key="order", amount=10, gate=""):
        return subprocess.Popen([sys.executable, "-m", "tests.test_atomic_admission", "--worker", role,
                                 self.path, self.run_id, key, str(amount), self.tmp.name, str(gate)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def finish(self, process):
        try:
            out, err = process.communicate(timeout=45)
        except BaseException:
            process.kill()  # This test's own child, never another process.
            process.communicate()
            raise
        self.assertEqual(process.returncode, 0, err)
        return out.strip()

    def test_process_crashes_before_and_after_commit(self):
        for stage in ("approval_used", "effect_approved", "tool_intent", "committed"):
            with self.subTest(stage=stage):
                self.run_id = "crash-" + uuid.uuid4().hex
                self.lg.record_approval(self.run_id, "atomic.pay", {"amount": 10})
                p = self.worker("crash-" + stage)
                out, err = p.communicate(timeout=45)
                self.assertEqual(p.returncode, 17, (out, err))
                kinds = [r["kind"] for r in self.lg.entries(self.run_id)]
                expected = ["approval", "approval_used", "effect_approved", "tool_intent"]
                self.assertEqual(kinds, expected if stage == "committed" else ["approval"])
                self.assertFalse((Path(self.tmp.name) / "effect.txt").exists())
                with c4a.run(self.run_id):
                    if stage == "committed":
                        with self.assertRaises(c4a.EffectInDoubt):
                            self.pay(10, c4a_key="order")
                        with self.assertRaises(c4a.KeyConflict):
                            self.pay(20, c4a_key="order")
                    else:
                        with self.assertRaises(c4a.ApprovalRequired):
                            self.pay(20, c4a_key="order")
                        self.assertEqual(self.pay(10, c4a_key="order"), 10)

    def race(self, keys, amounts=None):
        gate = Path(self.tmp.name) / "go"
        amounts = amounts or [10] * len(keys)
        procs = [self.worker("race", key, amount, gate=gate) for key, amount in zip(keys, amounts)]
        try:
            deadline = time.monotonic() + 30
            while len(list(Path(self.tmp.name).glob("ready-*"))) < len(procs):
                self.assertLess(time.monotonic(), deadline, "workers did not reach barrier")
                time.sleep(0.01)
            gate.write_text("go")
            return [self.finish(p) for p in procs]
        finally:
            for p in procs:
                if p.poll() is None:
                    p.kill()
                    p.communicate()

    def test_same_effect_spends_only_one_of_two_approvals(self):
        self.lg.record_approval(self.run_id, "atomic.pay", {"amount": 10})
        self.assertEqual(len(self.lg.matching_approvals(self.run_id, "atomic.pay", {"amount": 10})), 2)
        self.assertEqual(self.race(["order"] * 4), ["10"] * 4)
        kinds = [r["kind"] for r in self.lg.entries(self.run_id)]
        for kind in ("approval_used", "effect_approved", "tool_intent", "tool_done"):
            self.assertEqual(kinds.count(kind), 1, kind)
        self.assertEqual((Path(self.tmp.name) / "effect.txt").read_text().splitlines(), ["10"])

    def test_one_approval_cannot_admit_two_distinct_effects(self):
        results = self.race(["order-a", "order-b"])
        self.assertEqual(sorted(results), ["10", "ApprovalRequired"])
        self.assertEqual((Path(self.tmp.name) / "effect.txt").read_text().splitlines(), ["10"])

    def test_named_key_race_with_two_approved_amounts_reports_conflict(self):
        self.lg.record_approval(self.run_id, "atomic.pay", {"amount": 20})
        original = self.lg.claim_approved

        def another_caller_wins(run_id, tool, args, effect, key, payload):
            # Both callers passed the decorator's initial conflict check. The other caller
            # now commits its intent, but has not finished its effect.
            self.assertTrue(original(run_id, tool, {"amount": 10}, effect, key,
                                     {"tool": tool, "args": {"amount": 10}}))
            return original(run_id, tool, args, effect, key, payload)

        self.lg.claim_approved = another_caller_wins
        try:
            with c4a.run(self.run_id), self.assertRaises(c4a.KeyConflict):
                self.pay(20, c4a_key="order")
        finally:
            self.lg.claim_approved = original
        self.assertEqual(self.calls, [])
        self.assertEqual(sum(r["kind"] == "approval_used" for r in self.lg.entries(self.run_id)), 1)


def worker(role, path, run_id, key, amount, work, gate):
    lg = c4a.init(path, secret=b"synthetic-approval-fixture")
    insert = lg._insert

    def crashing(*args, **kwargs):
        result = insert(*args, **kwargs)
        if role == "crash-" + args[2]:
            os._exit(17)
        return result

    lg._insert = crashing
    admitted = lg.claim_approved

    def committed(*args, **kwargs):
        if gate:
            (Path(work) / ("ready-" + str(os.getpid()))).touch()
            deadline = time.monotonic() + 30
            while not Path(gate).exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("barrier timed out")
                time.sleep(0.01)
        result = admitted(*args, **kwargs)
        if role == "crash-committed" and result is True:
            os._exit(17)
        return result

    lg.claim_approved = committed

    @c4a.effect(name="atomic.pay", approval=True, wait=10)
    def pay(amount):
        with (Path(work) / "effect.txt").open("a") as f:
            f.write(str(amount) + "\n")
        return amount

    with c4a.run(run_id):
        try:
            print(pay(int(amount), c4a_key=key))
        except (c4a.ApprovalRequired, c4a.KeyConflict, c4a.EffectInDoubt) as exc:
            print(type(exc).__name__)


if __name__ == "__main__":
    if "--worker" in sys.argv:
        worker(*sys.argv[2:])
    else:
        unittest.main(argv=[sys.argv[0]] + [a for a in sys.argv[1:] if a != "--postgres"])
