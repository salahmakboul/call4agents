# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Call4Agents v0.1 - once-only side effects and approvals bound to exact arguments,
for any Python agent. Standard library only.

    import call4agents as c4a
    c4a.init()                                  # ledger at ./.c4a/ledger.sqlite

    @c4a.effect(approval=True)                  # once-only + needs a "yes" for THESE arguments
    def send_email(to, body): ...

    with c4a.run("thread-42"):                  # the id your framework resumes with
        send_email(to="bob", body="hi")
"""

import contextlib
import contextvars
import functools
import inspect
import time
import warnings
from typing import Any, Callable, Dict, Optional

from call4agents import codec as _codec
from call4agents import replay as _replay
from call4agents.ledger import Ledger, canonical, effect_key, intent_key
from call4agents.replay import ReplayMismatch, ReplayMissing, diff, recorded  # noqa: F401
from call4agents.proof import prove  # noqa: F401  (module named proof: a 'prove' module would be hidden)

__all__ = ["init", "run", "effect", "approve", "deny", "current_ledger", "idempotency_key", "idempotency_headers", "recorded", "diff", "prove",
           "ReplayMismatch", "ReplayMissing", "ApprovalRequired", "EffectInDoubt", "Ledger"]
__version__ = "0.1.0"

_LEDGER: Optional[Ledger] = None
_RUN: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("c4a_run", default=None)


def current_run() -> str:
    """The run id set by c4a.run(...). Outside a run this is an error, not a shared default run: a
    thread pool or a callback that lost the context would otherwise mix runs silently."""
    run_id = _RUN.get()
    if run_id is None:
        raise RuntimeError("no current run: call this inside `with c4a.run(<your thread/session id>):` "
                           "(new threads do not inherit it: use contextvars.copy_context().run or enter c4a.run there)")
    return run_id


class ApprovalRequired(Exception):
    """The effect needs a "yes" for exactly these arguments, and none matches."""

    def __init__(self, tool: str, args: Dict[str, Any]) -> None:
        super().__init__(f"{tool} needs approval for exactly {canonical(args)}")
        self.tool, self.call_args = tool, args      # not self.args: that is Exception's own message tuple


class EffectInDoubt(Exception):
    """The intent was recorded but no result: the process died DURING the effect.
    We cannot know if it happened, so we refuse to repeat it (no silent duplicate)."""

    def __init__(self, tool: str, args: Dict[str, Any], why: str = "crash during the effect") -> None:
        super().__init__(f"{tool}{canonical(args)} may already have run ({why}); "
                         "pass reconcile= to check the outside world, or resolve it by hand")
        self.tool, self.call_args = tool, args


def init(path: str = ".c4a/ledger.sqlite", secret: Optional[bytes] = None,
         replay_from: Optional[str] = None, old_secrets: Optional[list] = None,
         allow_network_drive: bool = False) -> Ledger:
    """replay_from: a recorded run record. Model calls and effects then come from it (offline).
    old_secrets: secrets you rotated away from; approvals they signed still count (C4A_OLD_SECRETS works too)."""
    global _LEDGER
    _LEDGER = Ledger(path, secret, old_secrets=old_secrets, allow_network_drive=allow_network_drive)
    _replay.set_source(replay_from)
    return _LEDGER


def current_ledger() -> Ledger:
    # Not named ledger(): that name would hide the call4agents.ledger module.
    if _LEDGER is None:
        raise RuntimeError("call c4a.init() first")
    return _LEDGER


@contextlib.contextmanager
def run(run_id: str):
    token = _RUN.set(run_id)
    try:
        yield
    finally:
        _RUN.reset(token)


def approve(tool: Any, **args: Any) -> str:
    """Record a "yes" for this tool with exactly these arguments (in the current run)."""
    name, args = _tool_and_args(tool, args)
    return current_ledger().record_approval(current_run(), name, args)


def deny(tool: Any, **args: Any) -> str:
    """Record a "no" for this tool with exactly these arguments: earlier "yes" answers no longer count."""
    name, args = _tool_and_args(tool, args)
    return current_ledger().record_denial(current_run(), name, args)


def _bound_args(fn: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
    """The call's arguments by name, defaults filled in. `self`/`cls` are left out: an object's address
    changes per process, so it cannot be part of a key that must survive a restart."""
    bound = inspect.signature(fn).bind(*args, **kwargs)
    bound.apply_defaults()
    out = dict(bound.arguments)
    first = next(iter(inspect.signature(fn).parameters), None)
    if first in ("self", "cls"):
        out.pop(first, None)
    return out


def _tool_name(f: Callable) -> str:
    """Module + qualified name: `email.send` and `slack.send` are two different tools."""
    return f"{getattr(f, '__module__', '?')}.{getattr(f, '__qualname__', getattr(f, '__name__', repr(f)))}"


def _tool_and_args(tool: Any, args: Dict[str, Any]):
    if isinstance(tool, str):
        return tool, args
    name = getattr(tool, "c4a_name", None) or _tool_name(tool)
    fn = getattr(tool, "c4a_fn", None)
    if fn is not None:          # bind like the call will: defaults filled in, so approve(pay, amount=10, to=x)
        bound = inspect.signature(fn).bind_partial(**args)   # matches pay(10, x) with currency="EUR" by default
        bound.apply_defaults()
        args = {k: v for k, v in bound.arguments.items() if k not in ("self", "cls")}
    return name, args


_EFFECT_KEY: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("c4a_effect_key", default=None)


def idempotency_key() -> str:
    """Inside an @effect: the stable key of this effect - the same on every retry, resume or
    re-run of the same (run, tool, arguments). Send it to the outside API."""
    k = _EFFECT_KEY.get()
    if k is None:
        raise RuntimeError("idempotency_key() is only available inside a function decorated with @c4a.effect")
    return k


def idempotency_headers(header: str = "Idempotency-Key") -> Dict[str, str]:
    """{header: key} for HTTP APIs (Stripe/Adyen: Idempotency-Key; PayPal: PayPal-Request-Id)."""
    return {header: idempotency_key()}


def effect(fn: Optional[Callable] = None, *, approval: bool = False, name: Optional[str] = None,
           reconcile: Optional[Callable[..., Any]] = None, key: Optional[Callable[..., str]] = None,
           wait: float = 2.0, idempotent_for: Optional[float] = None, retry_on: tuple = ()):
    """Mark a function that changes the outside world.

    - Once-only per (run, tool, exact arguments): a repeat returns the recorded result.
    - Crash between intent and result: EffectInDoubt, unless reconcile(**args) returns the
      result (the effect happened) or None (it did not; then it runs once).
    - approval=True: runs only if c4a.approve(...) recorded a "yes" for these exact arguments.
      One "yes" covers ONE action: a retry or resume of the same action reuses it, a deliberate
      second action (new c4a_key) needs a new "yes". c4a.deny(...) cancels earlier "yes" answers,
      also for an action that is in doubt. Callers racing with a single "yes": one runs, the
      others get ApprovalRequired (nothing runs twice).
    - Explicit key: pass c4a_key="..." at call time, or key=lambda **args: ... here.
      A deliberate second identical action uses a new key. Default: same run + tool + arguments.
    - Callers racing on the same effect: exactly one runs it; the others wait up to `wait`
      seconds for its result, then raise EffectInDoubt.
    - idempotent_for=SECONDS: the function passes c4a.idempotency_key() to an outside API
      that removes duplicate requests with the same key, for that long (Stripe v1: 24 h). An
      in-doubt effect is then retried with the SAME key instead of raising (each later call
      within that time may resend it; the API, not luck, prevents the double charge). After that
      time the key may be forgotten by the API, so the effect stays in doubt (a late retry could
      charge twice).
    - The function raises: the action may or may not have happened (a timeout after the charge
      raises too), so the next call raises EffectInDoubt at once, naming the error. retry_on=
      (SomeError, ...) lists errors that prove nothing happened: after those, the next call runs
      the action again.
    - The tool name is module + qualified name; for methods, `self` is not part of the key (pass
      c4a_key= to tell two objects apart). Arguments without a stable text form raise TypeError.
    """
    def wrap(f: Callable) -> Callable:
        if inspect.isgeneratorfunction(f) or inspect.isasyncgenfunction(f):
            raise TypeError(f"@c4a.effect cannot wrap the generator {f.__qualname__}: its work would run after the "
                            "result is recorded. Do the side effect in a normal function and stream around it.")
        if name is None and getattr(f, "__name__", "") == "<lambda>":
            raise TypeError("@c4a.effect on a lambda needs name=...: two lambdas in one place would share one key")
        tool = name or _tool_name(f)

        def base_key(run_id: str, call_args: Dict[str, Any], explicit: Optional[str]) -> str:
            if explicit is None and key is not None:
                explicit = str(key(**call_args))
            if explicit is not None:
                return effect_key(run_id, tool, {"__c4a_key__": explicit})
            return effect_key(run_id, tool, call_args)

        def check(call_args: Dict[str, Any], explicit: Optional[str]) -> Dict[str, Any]:
            lg, run_id = current_ledger(), current_run()
            k = base_key(run_id, call_args, explicit)
            if approval and _replay.SOURCE is not None:
                if not lg.approved(run_id, tool, call_args):
                    raise ApprovalRequired(tool, call_args)
            elif approval and lg.latest("tool_done", k) is None:     # it may still run: it needs a live "yes"
                spent = lg.latest("effect_approved", k)
                if spent is not None:
                    # this effect already spent its "yes" (a retry or a resume): a later "no" still stops it
                    if lg.last_denial(run_id, tool, call_args) > spent["seq"]:
                        raise ApprovalRequired(tool, call_args)
                elif not lg.use_approval(run_id, tool, call_args, k):
                    # no unused "yes" for exactly these arguments; one "yes" = one action (K17)
                    raise ApprovalRequired(tool, call_args)
            if _replay.SOURCE is not None:          # replay: never touch the outside world
                done = _replay.SOURCE.done(k)
                if done is None:
                    raise ReplayMissing(f"{tool}{canonical(call_args)} has no recorded result")
                if lg.latest("tool_done", k) is None:      # keep the replayed run comparable with diff()
                    lg.claim(run_id, intent_key(k, 1), {"tool": tool, "args": call_args, "replayed": True})
                    lg.append(run_id, "tool_done", k, {"result": done["result"], "replayed": True})
                return {"replay": _codec.decode(done["result"])}
            for _ in range(2):
                done = lg.latest("tool_done", k)
                if done is not None:
                    return {"replay": _codec.decode(done["payload"]["result"])}
                attempt = lg.count("tool_abandoned", k) + lg.count("tool_retry_same_key", k) + 1
                if lg.claim(run_id, intent_key(k, attempt), {"tool": tool, "args": call_args}):
                    return {"key": k, "run_id": run_id, "attempt": attempt}
                err = lg.latest("tool_error", k)
                failed = err is not None and err["payload"].get("attempt") == attempt
                why = f"it raised {err['payload'].get('error')}" if failed else "crash during the effect"
                deadline = time.monotonic() + (0 if failed else wait)   # someone else may hold it: wait
                while time.monotonic() < deadline:
                    done = lg.latest("tool_done", k)
                    if done is not None:
                        return {"replay": _codec.decode(done["payload"]["result"])}
                    time.sleep(0.02)
                if idempotent_for is not None and reconcile is None:
                    first = lg.first_intent_time(k)
                    if first is not None and time.time() - first <= idempotent_for:
                        # The outside API still remembers this key: resending with the SAME key cannot
                        # create a second effect. Record the retry, then claim the next attempt.
                        lg.append(run_id, "tool_retry_same_key", k, {"attempt": attempt})
                        if lg.claim(run_id, intent_key(k, attempt + 1), {"tool": tool, "args": call_args,
                                                                         "same_key_retry": True}):
                            return {"key": k, "run_id": run_id, "attempt": attempt + 1}
                    raise EffectInDoubt(tool, call_args, why)
                if reconcile is None:
                    raise EffectInDoubt(tool, call_args, why)
                found = reconcile(**call_args)
                if found is not None:
                    lg.append(run_id, "tool_done", k, {"result": _codec.encode(found), "reconciled": True})
                    return {"replay": found}
                lg.append(run_id, "tool_abandoned", k, {"attempt": attempt})   # it did not happen: one new try
            raise EffectInDoubt(tool, call_args)

        def record_result(plan: Dict[str, Any], result: Any) -> None:
            """Store the result. If this attempt was given up while it ran (a person resolved it as
            "not done", or reconcile said so) and a newer attempt exists, the effect may have happened
            twice: say so loudly, record it, never hide it."""
            lg = current_ledger()
            try:
                encoded = _codec.encode(result)
            except TypeError:
                lg.append(plan["run_id"], "tool_error", plan["key"], {"attempt": plan["attempt"],
                                                                      "error": "result could not be recorded"})
                raise
            lg.append(plan["run_id"], "tool_done", plan["key"], {"result": encoded, "attempt": plan["attempt"]})
            given_up = [e for e in lg.entries_for_key(plan["key"], "tool_abandoned") if e["payload"].get("attempt") == plan["attempt"]]
            if given_up:
                lg.append(plan["run_id"], "late_result", plan["key"], {"attempt": plan["attempt"]})
                warnings.warn(f"{tool}: attempt {plan['attempt']} returned AFTER it was given up; "
                              "the action may have happened twice - check `call4agents doubt`", stacklevel=3)

        def failed(plan: Dict[str, Any], exc: BaseException) -> None:
            lg = current_ledger()
            if retry_on and isinstance(exc, retry_on):      # the caller says: this error means "did not happen"
                lg.append(plan["run_id"], "tool_abandoned", plan["key"], {"attempt": plan["attempt"], "error": repr(exc)[:200]})
            else:
                lg.append(plan["run_id"], "tool_error", plan["key"], {"attempt": plan["attempt"], "error": repr(exc)[:200]})

        if inspect.iscoroutinefunction(f):
            @functools.wraps(f)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                import asyncio
                explicit = kwargs.pop("c4a_key", None)
                call_args = _bound_args(f, args, kwargs)
                # in a worker thread: waiting for another caller must not block the event loop
                plan = await asyncio.to_thread(contextvars.copy_context().run, check, call_args, explicit)
                if "replay" in plan:
                    return plan["replay"]
                token = _EFFECT_KEY.set(plan["key"])
                try:
                    result = await f(*args, **kwargs)
                except BaseException as exc:
                    failed(plan, exc)
                    raise
                finally:
                    _EFFECT_KEY.reset(token)
                record_result(plan, result)
                return result
            async_wrapper.c4a_name, async_wrapper.c4a_fn = tool, f
            return async_wrapper

        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            explicit = kwargs.pop("c4a_key", None)
            call_args = _bound_args(f, args, kwargs)
            plan = check(call_args, explicit)
            if "replay" in plan:
                return plan["replay"]
            token = _EFFECT_KEY.set(plan["key"])
            try:
                result = f(*args, **kwargs)
            except BaseException as exc:
                failed(plan, exc)
                raise
            finally:
                _EFFECT_KEY.reset(token)
            record_result(plan, result)
            return result
        wrapper.c4a_name, wrapper.c4a_fn = tool, f
        return wrapper

    return wrap(fn) if fn is not None else wrap
