"""Put each bug back, one at a time, in a temporary copy of the kit, and check that a suite catches it.

Run from the repository root:  python -m tests.mutations.run_kit_mutations
No repository file is changed and no LLM call is made. Each batch starts with a no-op control,
which must be MISSED: a control that "catches" means the test setup itself is broken.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INIT, LEDGER = "call4agents/__init__.py", "call4agents/ledger.py"
PG = "call4agents/stores/postgres.py"

# (name, file, old text, new text, suite)
MUTATIONS = [
    # --- K17: one yes = one action, deny enforced
    ("control (no-op)", LEDGER, "# Each \"yes\" is spent", "# Each \"yes\" is spent", "tests.test_c4a_kit"),
    ("K17 a yes is never spent", LEDGER, 'self.append(run_id, "approval_used",', 'self.append(run_id, "approval_used_x",', "tests.test_c4a_kit"),
    ("K17 a no is ignored", LEDGER, 'return max(self._matching(run_id, tool, args, "denial"), default=0)', "return 0", "tests.test_c4a_kit"),
    ("K17 no deny check when in doubt", INIT, 'if (lg.last_denial(run_id, tool, call_args) > spent["seq"]', "if (False", "tests.test_c4a_kit"),
    ("K17 no unique index on a use", LEDGER, "CREATE UNIQUE INDEX IF NOT EXISTS one_use", "CREATE INDEX IF NOT EXISTS one_use", "tests.test_c4a_kit"),
    # --- K18: bugs found by an outside tester
    ("K18 keys from str() again", LEDGER, "default=_stable)", "default=str)", "tests.test_c4a_kit"),
    ("K18 tool name = function name", INIT, "tool = name or _tool_name(f)", "tool = name or f.__name__", "tests.test_c4a_kit"),
    ("K18 silent shared default run", INIT, "    if run_id is None:\n        raise RuntimeError(\"no current run",
     "    if run_id is None:\n        return \"default\"\n        raise RuntimeError(\"no current run", "tests.test_c4a_kit"),
    ("K18 wait 2 s after an error", INIT, "failed = err is not None and", "failed = False and", "tests.test_c4a_kit"),
    ("K18 retry_on ignored", INIT, "if retry_on and isinstance(exc, retry_on):", "if False:", "tests.test_c4a_kit"),
    ("K18 approve ignores defaults", INIT, "        bound.apply_defaults()\n        skip = (",
     "        skip = (", "tests.test_c4a_kit"),
    ("K18 exception message lost", INIT, "self.tool, self.call_args = tool, args      #", "self.tool, self.args = tool, args      #", "tests.test_c4a_kit"),
    ("K18 async wait blocks the loop", INIT,
     "plan = await asyncio.to_thread(contextvars.copy_context().run, check, call_args, explicit)",
     "plan = check(call_args, explicit)", "tests.test_c4a_kit"),
    # --- K21: a named action reused with other arguments is a conflict (issue #1)
    ("K21 a reused key returns another call's result", INIT,
     "if first is not None and not _same_args(first", "if False and first is not None and not _same_args(first",
     "tests.test_c4a_kit"),
    ("K21 numbers compared as text", INIT, "        return na == nb" + chr(10), "        return a == b" + chr(10), "tests.test_c4a_kit"),
    # --- K22: a spent "yes" covers only its own arguments; a losing caller gets a conflict
    ("K22 a spent yes covers other arguments", INIT,
     'or spent["payload"]["approval"] not in lg.matching_approvals(run_id, tool, call_args)):', "or False):", "tests.test_c4a_kit"),
    ("K22 the race loser gets the winner's result", INIT, 'if "replay" in plan:                # a caller', "if False:                # a caller", "tests.test_c4a_kit"),
    # --- K19: a record cannot make the codec call a function
    ("K19 codec calls whatever is named", "call4agents/codec.py", "    if not isinstance(obj, type):" + chr(10),
     "    return obj" + chr(10) + "    if not isinstance(obj, type):" + chr(10), "tests.test_c4a_kit"),
    ("K19 no model check", "call4agents/codec.py", 'if kind == "model" and not', 'if False and not', "tests.test_c4a_kit"),
    # --- production checks (P1-P12)
    ("control (no-op)", LEDGER, "SCHEMA_VERSION = 1", "SCHEMA_VERSION = 1", "tests.test_c4a_production"),
    ("P1 newer format read anyway", LEDGER, "if found > SCHEMA_VERSION:", "if False:", "tests.test_c4a_production"),
    ("P2 bad file not explained", LEDGER, "except sqlite3.DatabaseError as exc:", "except ZeroDivisionError as exc:", "tests.test_c4a_production"),
    ("P3 secret written in place, not linked", LEDGER, "                os.link(tmp, p)", "                os.replace(tmp, p)", "tests.test_c4a_production"),
    ("P4 old secrets ignored", LEDGER, '[self.secret] + list(getattr(self, "old_secrets", []))', "[self.secret]", "tests.test_c4a_production"),
    ("P5 network mounts not detected", LEDGER, "    return best[1] in NETWORK_FS", "    return False", "tests.test_c4a_production"),
    ("P6 generators accepted", INIT, "if inspect.isgeneratorfunction(f) or inspect.isasyncgenfunction(f):", "if False:", "tests.test_c4a_production"),
    ("P7 late result not flagged", INIT, "            if given_up:", "            if False:", "tests.test_c4a_production"),
    ("P8 not-done keeps it in doubt", "call4agents/ops.py", '{"attempt": max(a["attempts"]), **stamp}', '{"attempt": 0, **stamp}', "tests.test_c4a_production"),
    ("P9 ops create a missing record", "call4agents/ops.py", "    if not Path(path).is_file():", "    if False:", "tests.test_c4a_production"),
    ("P10 archive moves runs in doubt", "call4agents/ops.py", "if t < cutoff and r not in open_runs]", "if t < cutoff]", "tests.test_c4a_production"),
    ("P11 unrecordable result not marked", INIT, '"error": "result could not be recorded"})', '"error": "x"})', "tests.test_c4a_production"),
    ("P12 one connection shared by threads", LEDGER, "self._local = threading.local()", "self._local = self", "tests.test_c4a_production"),
    ("P19 a busy new record is called unusable", LEDGER, "if not any(w in str(exc) for w in (\"locked\", \"busy\")) or time.monotonic() > deadline:",
     "if True:", "tests.test_c4a_production"),
    ("P14 any object's str() accepted", LEDGER, "    raise TypeError(f\"{type(o).__name__} is not a type with a stable", "    return str(o)" + chr(10) + "    raise TypeError(f\"{type(o).__name__} is not a type with a stable", "tests.test_c4a_production"),
    ("P14 deep nesting not caught", LEDGER, "    if _too_deep(value):", "    if False:", "tests.test_c4a_production"),
    ("P15 damaged row unexplained", LEDGER, "        except ValueError as exc:" + chr(10) + "            raise LedgerError(f\"row", "        except ZeroDivisionError as exc:" + chr(10) + "            raise LedgerError(f\"row", "tests.test_c4a_production"),
    # --- K20: framework context left out of the key
    ("K20 exclude ignored", INIT, "    for name_ in exclude:" + chr(10) + "        out.pop(name_, None)", "    pass", "tests.test_c4a_kit"),
    ("K20 context only as first parameter", "call4agents/adapters/_common.py", "    for p in params:" + chr(10) + "        ann = p.annotation",
     "    for p in params[:1]:" + chr(10) + "        ann = p.annotation", "tests.test_c4a_kit"),
    ("P18 report phones home", "call4agents/__main__.py", '    return ISSUE_FORM + "?"',
     '    import urllib.request; urllib.request.urlopen("https://example.com", timeout=3)' + chr(10) + '    return ISSUE_FORM + "?"',
     "tests.test_c4a_production"),
    # --- U: undo path
    ("control (no-op)", INIT, "# newest first, like a stack", "# newest first, like a stack", "tests.test_undo"),
    ("U undo oldest first", INIT, "    for e in reversed(finished):", "    for e in finished:", "tests.test_undo"),
    ("U undo not once-only", INIT, '                fn(c4a_key=e["key"], **args)', '                fn(c4a_key=e["key"] + str(time.time()), **args)', "tests.test_undo"),
    ("U no-undo actions skipped silently", INIT, '            out["no_undo"].append(tool)', "            pass", "tests.test_undo"),
    ("U failed undo ignored", INIT, '            raise UndoFailed(tool, exc, out["undone"]) from exc', "            continue", "tests.test_undo"),
    ("U async undo accepted", INIT, "            if inspect.iscoroutinefunction(undo):", "            if False:", "tests.test_undo"),
    # --- F: idempotency keys
    ("control (no-op)", INIT, "# The outside API still remembers", "# The outside API still remembers", "tests.test_idempotency"),
    ("F no lifetime check", INIT, "if first is not None and time.time() - first <= idempotent_for:", "if True:", "tests.test_idempotency"),
    ("F key changes per attempt", INIT, 'token = _EFFECT_KEY.set(plan["key"])' + chr(10) + "            try:" + chr(10) + "                result = f(",
     'token = _EFFECT_KEY.set(plan["key"] + str(time.time()))' + chr(10) + "            try:" + chr(10) + "                result = f(",
     "tests.test_idempotency"),
    ("F no same-key retry", INIT, "if idempotent_for is not None and reconcile is None:", "if False:", "tests.test_idempotency"),
    # --- S: one run record shared by several machines (PostgreSQL)
    ("control (no-op)", PG, "One run record shared by several machines", "One run record shared by several machines", "tests.test_shared_record"),
    ("S no unique intent across machines", LEDGER, "CREATE UNIQUE INDEX IF NOT EXISTS one_intent",
     "CREATE INDEX IF NOT EXISTS one_intent", "tests.test_shared_record"),
    ("S NUL not escaped for PostgreSQL", PG, "    return v.replace(", "    return v" + chr(10) + "    return v.replace(", "tests.test_shared_record"),
    ("S shared record without a secret", PG, "        if not secret:" + chr(10) + "            raise LedgerError(\"a shared run record",
     "        if False:" + chr(10) + "            raise LedgerError(\"a shared run record", "tests.test_shared_record"),
    ("S10 commands create a missing shared record", "call4agents/ops.py", "secret=_NO_SIGNING, create=False)",
     "secret=_NO_SIGNING, create=True)", "tests.test_shared_record"),
    ("S11 archive moves runs in doubt (shared)", "call4agents/ops.py", "if t < cutoff and r not in open_runs]", "if t < cutoff]",
     "tests.test_shared_record"),
    # --- T: approval tickets (experimental)
    ("control (no-op)", LEDGER, "approval tickets (experimental)", "approval tickets (experimental)", "tests.test_tickets"),
    ("T ticket signature not checked", LEDGER, "        if not any(hmac.compare_digest(mac, self._ticket_mac(",
     "        if False and not any(hmac.compare_digest(mac, self._ticket_mac(", "tests.test_tickets"),
    ("T a ticket decided twice", LEDGER, "CREATE UNIQUE INDEX IF NOT EXISTS one_decision", "CREATE INDEX IF NOT EXISTS one_decision",
     "tests.test_tickets"),
    ("T expiry ignored", LEDGER, 'if time.time() > p["exp"]:', "if False:", "tests.test_tickets"),
    ("T deny recorded as a yes", LEDGER, 'kind = "approval" if decision == "approve" else "denial"', 'kind = "approval"',
     "tests.test_tickets"),
    ("T approval not checked when it runs", INIT, "elif not lg.use_approval(run_id, tool, call_args, k):", "elif False:",
     "tests.test_tickets"),
    ("T an open ticket is not reused", LEDGER,
     'if decided is None or (decided["payload"]["decision"] == "approve" and unused):', "if False:", "tests.test_tickets"),
    ("T OpenAI bridge approves whatever waits", "call4agents/adapters/openai_agents.py",
     'item = waiting.get(info["call_id"])', "item = next(iter(waiting.values()), None)", "tests.test_tickets"),
    ("S9 verify on a shared record not handled", "call4agents/ops.py", "integrity = PG_INTEGRITY if _is_pg(path) else",
     "integrity = \"?\" if _is_pg(path) else", "tests.test_shared_record"),
]


def copy_tree(dst: Path) -> None:
    shutil.copytree(ROOT / "call4agents", dst / "call4agents", ignore=shutil.ignore_patterns("__pycache__"))
    (dst / "tests").mkdir()
    (dst / "tests" / "__init__.py").write_text("", encoding="utf-8")
    for f in ("test_c4a_kit.py", "c4a_codec_types.py", "c4a_k18_child.py", "c4a_prove_sample.py", "test_idempotency.py",
              "test_c4a_production.py", "test_undo.py", "c4a_undo_child.py", "test_adapters.py",
              "test_shared_record.py", "test_tickets.py"):
        shutil.copy(ROOT / "tests" / f, dst / "tests" / f)


def main() -> int:
    bad = 0
    for name, rel, old, new, suite in MUTATIONS:
        tmp = Path(tempfile.mkdtemp(prefix="c4a-mut-"))
        try:
            copy_tree(tmp)
            path = tmp / rel
            text = path.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"  ??    {name:36} pattern not found once (the code moved: update this script)")
                bad += 1
                continue
            path.write_text(text.replace(old, new), encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", suite], cwd=tmp, capture_output=True, text=True, timeout=600)
            fails = [l.split()[1] for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
            caught = r.returncode != 0
            expected = not name.startswith("control")
            print(f"  {'ok  ' if caught == expected else 'BAD '}  {name:36} {'CATCHES ' + ','.join(fails) if caught else 'MISSES'}")
            bad += caught != expected
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("\nall mutations behave as expected" if not bad else f"\n{bad} mutation result(s) not as expected")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
