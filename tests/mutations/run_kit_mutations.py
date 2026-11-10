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
CT, SCAN, AUDIT = "call4agents/crashtest.py", "call4agents/scan.py", "call4agents/audit.py"
PG = "call4agents/stores/postgres.py"
PYT = "call4agents/adapters/pytest_plugin.py"
RULES = "call4agents/rules.py"
SPANS = "call4agents/spans.py"

# (name, file, old text, new text, suite)
MUTATIONS = [
    # --- K17: one yes = one action, deny enforced
    ("control (no-op)", LEDGER, "# Each \"yes\" is spent", "# Each \"yes\" is spent", "tests.test_c4a_kit"),
    ("K17 a yes is never spent", LEDGER, 'self._insert(conn, run_id, "approval_used",', 'self._insert(conn, run_id, "approval_used_x",', "tests.test_c4a_kit"),
    ("K17 a no is ignored", LEDGER, 'denied = max(self._matching_in(conn, run_id, tool, args, "denial"), default=0)', "denied = 0", "tests.test_c4a_kit"),
    ("K17 no deny check when in doubt", LEDGER, 'if (denied > spent["seq"] or', "if (False or", "tests.test_c4a_kit"),
    ("K17 no unique index on a use", LEDGER, "CREATE UNIQUE INDEX IF NOT EXISTS one_use", "CREATE INDEX IF NOT EXISTS one_use", "tests.test_atomic_admission"),
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
    ("K22 a spent yes covers other arguments", LEDGER,
     'or spent["payload"]["approval"] not in approvals):', "or False):", "tests.test_c4a_kit"),
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
    ("F key changes per attempt", INIT, 'token = _EFFECT_KEY.set(plan["key"])' + chr(10) + "                try:" + chr(10) + "                    result = f(",
     'token = _EFFECT_KEY.set(plan["key"] + str(time.time()))' + chr(10) + "                try:" + chr(10) + "                    result = f(",
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
    ("T approval not checked when it runs", INIT, "if owned is None:", "if False:",
     "tests.test_tickets"),
    ("T an open ticket is not reused", LEDGER,
     'if decided is None or (decided["payload"]["decision"] == "approve" and unused):', "if False:", "tests.test_tickets"),
    ("T OpenAI bridge approves whatever waits", "call4agents/adapters/openai_agents.py",
     'item = waiting.get(info["call_id"])', "item = next(iter(waiting.values()), None)", "tests.test_tickets"),
    # --- crashtest and scan: each check must be able to fail, and crashtest must catch a real bug of ours
    ("CT split admission trusts a spent yes", LEDGER,
     '        try:\n            with self._lock, self._conn() as conn:\n                self._begin_admission(conn)',
     '        if self.latest("effect_approved", effect) is None and not self.use_approval(run_id, tool, args, effect):\n'
     '            return None\n        return self.claim(run_id, key, payload)\n'
     '        try:\n            with self._lock, self._conn() as conn:\n                self._begin_admission(conn)',
     "tests.test_crashtest --approval-boundary-only"),
    ("CT nothing is ever killed", CT, "if n == self.crash_at:", "if False:", "tests.test_crashtest"),
    ("CT no kill after the action", CT, 'self.point(f"after {shown}")', "pass", "tests.test_crashtest"),
    ("CT a repeat is never counted", CT, "ran[c] > baseline[c]", "False", "tests.test_crashtest"),
    ("CT a changed call is ignored", CT, "sorted(c for c in ran if c not in baseline)", "[]", "tests.test_crashtest"),
    ("CT SQLite writes are not crash points", CT, "conn.set_trace_callback(trace)", "pass", "tests.test_crashtest"),
    ("CT file writes are not crash points", CT, "sys.addaudithook(c.audit)", "pass", "tests.test_crashtest"),
    ("CT each await counts as a call", CT, "self.open_async.add(id(frame))", "pass", "tests.test_crashtest"),
    ("CT in doubt counts as lost", CT, '"lost": [] if in_doubt else sorted', '"lost": sorted', "tests.test_crashtest"),
    ("CT a failing script looks clean", CT, 'if clean.returncode != 0:', "if False:", "tests.test_crashtest"),
    ("CT the loop counts as an action", SCAN, "node.func.id in found - {name}", "False", "tests.test_crashtest"),
    ("SC toolkit methods are not tools", SCAN, "return out | toolkit_methods(tree)", "return out", "tests.test_crashtest"),
    ("SC a search that POSTs is listed", SCAN, 'if READ_NAME.match(name) and all(e.startswith(', 'if False and all(e.startswith(', "tests.test_crashtest"),
    ("SC guards are ignored", SCAN, "if approval and once:", "if False:", "tests.test_crashtest"),
    ("SC any function counts as a tool", SCAN, "for name in sorted(tool_names(tree) | set(class_tools(tree))):", "for name in sorted(functions):", "tests.test_crashtest"),
    ("SC helpers are not followed", SCAN, 'found += [f"{e} (in {node.func.id})" for e in effects_in(helper)]', "pass", "tests.test_crashtest"),
    ("SC an approval set up elsewhere reads as missing", SCAN, "file_approval = bool(FILE_APPROVAL.search(source))", "file_approval = False", "tests.test_crashtest"),
    ("SC a name alone reads as code", SCAN, 'effects, confidence = ["name suggests an action"], "name only"',
     'effects, confidence = ["name suggests an action"], "code"', "tests.test_crashtest"),
    ("K24 a cancel is not recorded", INIT, "result = await f(*args, **kwargs)\n                    except BaseException as exc:",
     "result = await f(*args, **kwargs)\n                    except Exception as exc:", "tests.test_c4a_kit"),
    # --- K25: reconcile can say "cannot tell yet"
    ("K25 UNKNOWN stored as the result", INIT, "if found is UNKNOWN:", "if False:", "tests.test_c4a_kit"),
    ("K25 UNKNOWN read as did-not-happen", INIT, 'raise EffectInDoubt(tool, call_args, "reconcile could not tell yet")',
     "found = None", "tests.test_c4a_kit"),
    ("K25 UNKNOWN reads as false", INIT, '        raise TypeError("c4a.UNKNOWN has no truth value',
     '        return False\n        raise TypeError("c4a.UNKNOWN has no truth value', "tests.test_c4a_kit"),
    # --- K26: retries only when the record decides the next try
    ("K26 any error is retried", INIT,
     "            if not ((retry_on and isinstance(exc, retry_on)) or idempotent_for is not None or reconcile is not None):",
     "            if False:", "tests.test_c4a_kit"),
    ("K26 a cancel is retried", INIT, "if n >= retries or not isinstance(exc, Exception):", "if n >= retries:",
     "tests.test_c4a_kit"),
    ("K26 nothing is retried", INIT, "return min(30.0, backoff * (2 ** n))", "return None", "tests.test_c4a_kit"),
    ("K26 one try too many", INIT, "if n >= retries or not", "if n > retries or not", "tests.test_c4a_kit"),
    ("K26 retries without a signal accepted", INIT, "if retries and not (retry_on or", "if False and not (retry_on or",
     "tests.test_c4a_kit"),
    # --- K27: shared limits
    ("K27 the limit is never checked", LEDGER, "if used + cost > maximum:", "if False:", "tests.test_c4a_kit"),
    ("K27 the hold is outside the admission lock", LEDGER,
     "                self._begin_admission(conn)" + chr(10) + "                if self._latest_in(conn, \"tool_done\", effect) is not None:"
     + chr(10) + "                    return False" + chr(10) + "                if self._latest_in(conn, \"tool_intent\", key)",
     "                if self._latest_in(conn, \"tool_done\", effect) is not None:"
     + chr(10) + "                    return False" + chr(10) + "                if self._latest_in(conn, \"tool_intent\", key)",
     "tests.test_atomic_admission AtomicAdmission.test_shared_limit_holds_under_contention"),
    ("K27 the cost is never given back", INIT, "                    lg.release_limit(plan[\"run_id\"], plan[\"key\"], held)",
     "                    pass", "tests.test_c4a_kit"),
    ("K27 a retry holds twice", LEDGER, "            return                      # a retry or resume",
     "            pass                        # a retry or resume", "tests.test_c4a_kit"),
    ("K27 the window is ignored", LEDGER, "if since is None or h[\"t\"] >= since", "if True", "tests.test_c4a_kit"),
    ("K27 a refusal keeps the yes spent", LEDGER,
     "                    self._reserve_in(conn, run_id, effect, limit)" + chr(10)
     + "                self._insert(conn, run_id, \"tool_intent\", key, payload)" + chr(10),
     "                    try:" + chr(10) + "                        self._reserve_in(conn, run_id, effect, limit)" + chr(10)
     + "                    except LimitReached:" + chr(10) + "                        return None" + chr(10)
     + "                self._insert(conn, run_id, \"tool_intent\", key, payload)" + chr(10),
     "tests.test_c4a_kit"),
    # --- K28: rules checked against the bound arguments before a new attempt is admitted
    ("control (no-op)", RULES, "KINDS = (", "KINDS = (", "tests.test_c4a_kit"),
    ("K28 max is not enforced", RULES, 'raise RuleRefused(tool, args, f"max.{param}", f"{param}={args[param]} is over {bound}")',
     "pass", "tests.test_c4a_kit"),
    ("K28 a missing amount passes max", RULES, 'raise RuleRefused(tool, args, f"max.{param}", f"{param} is missing or not a number")',
     "continue", "tests.test_c4a_kit"),
    ("K28 NaN is a number", RULES, "return None if exact.is_nan() else exact", "return exact", "tests.test_c4a_kit"),
    ("K28 the allow list is ignored", RULES, "if args.get(param) not in values or not isinstance(args.get(param), str):",
     "if False:", "tests.test_c4a_kit"),
    ("K28 ask needs no yes", INIT, 'if approval or decision == "ask":', "if approval:", "tests.test_c4a_kit"),
    ("K28 per_run is not counted", INIT, "held.append((per_run_name(run_id), per_run, 1.0, None))", "pass",
     "tests.test_c4a_kit"),
    ("K28 per_run is never given back", INIT, "+ ([per_run_name(run_id)] if per_run is not None else [])", "+ []",
     "tests.test_c4a_kit"),
    ("K28 only the first limit holds", LEDGER, "for one in limit:", "for one in limit[:1]:", "tests.test_c4a_kit"),
    ("K28 rules read before the replay", INIT, "            def claim_ruled(attempt: int, payload: Any) -> bool:",
     "            if gate is not None:" + chr(10) + "                gate.decide(tool, call_args)" + chr(10) + chr(10)
     + "            def claim_ruled(attempt: int, payload: Any) -> bool:", "tests.test_c4a_kit"),
    ("K28 a refusal is not recorded", INIT,
     'lg.append(run_id, "rule_refused", k, {"tool": tool, "args": call_args, "rule": refused.rule,',
     'None and lg.append(run_id, "rule_refused", k, {"tool": tool, "args": call_args, "rule": refused.rule,',
     "tests.test_c4a_kit"),
    ("K28 a misspelt rule is accepted", RULES, "if unknown:        # a misspelt rule", "if False:          # a misspelt rule",
     "tests.test_c4a_kit"),
    ("K28 a tool with no section is accepted", RULES, "if not gate.covers(tool):", "if False:", "tests.test_c4a_kit"),
    ("K28 a crashing policy allows", RULES,
     'raise RuleRefused(tool, args, self.source, f"the policy raised {exc!r}") from exc', 'return "allow"',
     "tests.test_c4a_kit"),
    ("K28 an unclear answer allows", RULES,
     'raise RuleRefused(tool, args, self.source, f"the policy answered {answer!r}, not allow, ask or deny")',
     'return "allow"', "tests.test_c4a_kit"),
    ("K28 the policy gets the real arguments", RULES, "answer = self.fn(tool, json.loads(canonical(args)))",
     "answer = self.fn(tool, args)", "tests.test_c4a_kit"),
    ("K28 the decision is not stored", INIT, "payload = dict(payload, rules=dict(gate.stamp(), decision=decision))",
     "payload = dict(payload)", "tests.test_c4a_kit"),
    # --- pytest plugin: it must refuse without test accounts, and fail on a repeat
    ("control (no-op)", PYT, "def _explain(", "def _explain(", "tests.test_pytest_plugin --require-pytest"),
    ("PT runs without test accounts", PYT, "if test_accounts is not True:", "if False:", "tests.test_pytest_plugin --require-pytest"),
    ("PT a repeat passes the test", PYT, 'if found["twice"] or found["unapproved"]:', 'if found["unapproved"]:',
     "tests.test_pytest_plugin --require-pytest"),
    # --- audit export
    ("A the signature is not checked", AUDIT, 'if not hmac.compare_digest(str(doc.get("signature")), _signature(secret, doc)):', "if False:", "tests.test_audit"),
    ("A the end of the chain is not checked", AUDIT, 'if _chain(entries, doc["format"]) != doc.get("head"):', "if False:", "tests.test_audit"),
    ("A an entry's hash is not compared", AUDIT, 'if e.get("hash") != head:', "if False:", "tests.test_audit"),
    ("A the summary is not signed", AUDIT, '"count", "head", "actions", "decisions", "refusals")}', '"count", "head")}',
     "tests.test_audit"),
    ("A refusals are not signed", AUDIT, '"decisions", "refusals")}', '"decisions")}', "tests.test_audit"),
    ("A refusals are not exported", AUDIT, 'for e in entries if e["kind"] == "rule_refused" for p in',
     'for e in entries if e["kind"] == "rule_refused_x" for p in', "tests.test_audit"),
    ("A an older export is refused", AUDIT, 'if doc.get("format") not in SIGNED:', 'if doc.get("format") != FORMAT:',
     "tests.test_audit"),
    # --- spans export
    ("control (no-op)", SPANS, "SCOPE = ", "SCOPE = ", "tests.test_spans"),
    ("SP arguments always written", SPANS, "        if with_args:" + chr(10) + '            pairs["gen_ai.tool.call.arguments"] = a["args"]',
     "        if True:" + chr(10) + '            pairs["gen_ai.tool.call.arguments"] = a["args"]', "tests.test_spans"),
    ("SP in doubt looks fine", SPANS, 'if "IN DOUBT" in a["state"] or a["late"]:', "if False:", "tests.test_spans"),
    ("SP refusals dropped", SPANS, 'if e["kind"] != "rule_refused":', "if True:", "tests.test_spans"),
    ("SP a retry_on error not shown", SPANS, 'gave_up[e["key"]] = e["payload"]["error"]', "pass", "tests.test_spans"),
    ("SP error text leaks", SPANS, "str(last)[:200] if with_args else _error_name(last)", "str(last)[:200]", "tests.test_spans"),
    ("SP refusal reason leaks", SPANS, 'str(p.get("why"))[:200] if with_args', 'str(p.get("why"))[:200] if True',
     "tests.test_spans"),
    ("SP random ids", SPANS, 'return hashlib.sha256(f"{label}\\x00{text}".encode()).hexdigest()[:size]',
     'return __import__("os").urandom(size // 2).hex()', "tests.test_spans"),
    ("SP success marked OK", SPANS, 'status: Dict[str, Any] = {"code": UNSET}', 'status: Dict[str, Any] = {"code": 1}',
     "tests.test_spans"),
    ("A results are exported", AUDIT, 'LEFT_OUT = ("mac", "result")', 'LEFT_OUT = ("mac",)', "tests.test_audit"),
    ("A the approval key signs exports", AUDIT, 'return hmac.new(_key(secret), canonical(signed)', 'return hmac.new(b"public", canonical(signed)', "tests.test_audit"),
    ("S9 verify on a shared record not handled", "call4agents/ops.py", "integrity = PG_INTEGRITY if _is_pg(path) else",
     "integrity = \"?\" if _is_pg(path) else", "tests.test_shared_record"),
    # --- atomic admission: splitting the commit must leave a detectable partial admission
    ("control (atomic SQLite)", LEDGER, 'conn.execute("BEGIN IMMEDIATE")', 'conn.execute("BEGIN IMMEDIATE")',
     "tests.test_atomic_admission"),
    ("A SQLite admission takes no write lock", LEDGER, 'conn.execute("BEGIN IMMEDIATE")', 'conn.execute("BEGIN")',
     "tests.test_atomic_admission AtomicAdmission.test_many_processes_admit_distinct_approved_effects_at_once"),
    ("A SQLite commits before intent", LEDGER,
     '                self._insert(conn, run_id, "tool_intent", key, payload)',
     '                conn.execute("COMMIT")\n                self._insert(conn, run_id, "tool_intent", key, payload)',
     "tests.test_atomic_admission"),
    ("control (atomic PostgreSQL)", PG, 'conn.execute("LOCK TABLE entries IN SHARE ROW EXCLUSIVE MODE")',
     'conn.execute("LOCK TABLE entries IN SHARE ROW EXCLUSIVE MODE")', "tests.test_atomic_admission --postgres"),
    ("A PostgreSQL commits inside admission", PG, '        return row.fetchone()[0]',
     '        result = row.fetchone()[0]\n        conn.execute("COMMIT")\n        return result',
     "tests.test_atomic_admission --postgres"),
]

# This opt-in batch requires the PostgreSQL dependencies already installed by CI.
# Keep the default mutation inventory and its historical count unchanged.
PG_ADMISSION = [
    ("PG admission lock removed", PG,
     'conn.execute("LOCK TABLE entries IN SHARE ROW EXCLUSIVE MODE")',
     'conn.execute("SELECT 1")',
     "tests.test_atomic_admission --postgres AtomicAdmission.test_uncommitted_raw_denial_blocks_admission_and_connection_recovers"),
    ("PG admission lock allows writers", PG,
     'conn.execute("LOCK TABLE entries IN SHARE ROW EXCLUSIVE MODE")',
     'conn.execute("LOCK TABLE entries IN SHARE UPDATE EXCLUSIVE MODE")',
     "tests.test_atomic_admission --postgres AtomicAdmission.test_uncommitted_raw_denial_blocks_admission_and_connection_recovers"),
    ("PG admission commits before intent", PG,
     '        return row.fetchone()[0]',
     '        result = row.fetchone()[0]\n        conn.execute("COMMIT")\n        return result',
     "tests.test_atomic_admission --postgres AtomicAdmission.test_failure_before_intent_rolls_back_entire_admission"),
]


def copy_tree(dst: Path) -> None:
    shutil.copytree(ROOT / "call4agents", dst / "call4agents", ignore=shutil.ignore_patterns("__pycache__"))
    (dst / "tests").mkdir()
    (dst / "tests" / "__init__.py").write_text("", encoding="utf-8")
    for f in ("test_c4a_kit.py", "c4a_codec_types.py", "c4a_k18_child.py", "c4a_prove_sample.py", "test_idempotency.py",
              "test_c4a_production.py", "test_undo.py", "c4a_undo_child.py", "test_adapters.py",
              "test_shared_record.py", "test_tickets.py", "test_crashtest.py", "test_audit.py", "test_atomic_admission.py",
              "test_pytest_plugin.py", "test_spans.py"):
        shutil.copy(ROOT / "tests" / f, dst / "tests" / f)


def main() -> int:
    if sys.argv[1:] == ["--postgres-admission"]:
        return postgres_admission()
    if len(sys.argv) != 1:
        print("usage: python -m tests.mutations.run_kit_mutations [--postgres-admission]")
        return 2
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
            r = subprocess.run([sys.executable, "-m", *suite.split()], cwd=tmp, capture_output=True, text=True, timeout=600)
            fails = [l.split()[1] for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
            caught = r.returncode != 0
            expected = not name.startswith("control")
            print(f"  {'ok  ' if caught == expected else 'BAD '}  {name:36} {'CATCHES ' + ','.join(fails) if caught else 'MISSES'}")
            bad += caught != expected
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("\nall mutations behave as expected" if not bad else f"\n{bad} mutation result(s) not as expected")
    return 1 if bad else 0


def postgres_admission() -> int:
    """Only assertion failures kill a mutant; setup/import errors fail the batch."""
    with tempfile.TemporaryDirectory(prefix="c4a-mut-pg-base-") as base:
        copy_tree(Path(base))
        baseline = subprocess.run([sys.executable, "-m", "tests.test_atomic_admission", "--postgres"],
                                  cwd=base, capture_output=True, text=True, timeout=600)
        if baseline.returncode or "OK" not in baseline.stderr:
            print("BAD pristine PostgreSQL admission suite did not pass")
            print(baseline.stderr[-2000:])
            return 1
    bad = 0
    for name, rel, old, new, suite in PG_ADMISSION:
        with tempfile.TemporaryDirectory(prefix="c4a-mut-pg-") as tmp:
            root = Path(tmp)
            copy_tree(root)
            path = root / rel
            source = path.read_text(encoding="utf-8")
            if source.count(old) != 1:
                print(f"BAD {name}: exact anchor count {source.count(old)}")
                bad += 1
                continue
            path.write_text(source.replace(old, new), encoding="utf-8")
            result = subprocess.run([sys.executable, "-m", *suite.split()], cwd=root,
                                    capture_output=True, text=True, timeout=600)
            killed = (result.returncode != 0 and "FAIL: test_" in result.stderr
                      and "ERROR:" not in result.stderr and "ModuleNotFoundError" not in result.stderr)
            print(f"{'ok ' if killed else 'BAD'} {name}: {'assertion caught' if killed else 'no assertion failure'}")
            if not killed:
                print(result.stderr[-2000:])
                bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
