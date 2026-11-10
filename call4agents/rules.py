"""Rules checked before an action is admitted (experimental).

A rules file says, per tool, what a call may do:

    {"tools": {"shop.refund": {"max": {"amount": 500},          # a refund over 500 is refused
                               "allow": {"currency": ["EUR"]},  # only these values
                               "ask_over": {"amount": 100},     # over 100 needs a "yes" for the exact call
                               "per_run": 3}}}                  # at most 3 refunds in one run

    rules = c4a.Rules.load("rules.json")
    @c4a.effect(rules=rules)
    def refund(order, amount, currency): ...

The rules are read against the call's bound arguments, after a finished action is replayed and before a new
attempt is admitted, so a resume of a finished action returns its result even if the rules changed. per_run is
counted in the same transaction as the intent (like c4a.Limit): a crash, retry or resume of one action counts
once, and racing workers cannot pass the count together. The decision (which rules, their digest, allow or ask)
is stored with the intent; a refusal is stored as a `rule_refused` row. Nothing runs after a refusal.

Instead of a file, `rules=` takes a function `policy(tool, args)` returning "allow", "ask" or "deny" (or
("deny", reason)): the route to a policy engine such as OPA or Cedar. It fails closed: an error or any other
answer refuses the call.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Optional, Tuple

from call4agents.ledger import canonical

KINDS = ("max", "allow", "ask_over", "per_run")


class RuleRefused(Exception):
    """A rule refused this call. Nothing ran and no intent was recorded."""

    def __init__(self, tool: str, args: Dict[str, Any], rule: str, why: str) -> None:
        super().__init__(f"{tool}{canonical(args)} refused by the rule {rule}: {why}. Nothing ran.")
        self.tool, self.call_args, self.rule, self.why = tool, args, rule, why


def _number(value: Any) -> Optional[Decimal]:
    """The exact value of an int, float or Decimal; None for anything else (bool, text, NaN)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        exact = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return None if exact.is_nan() else exact


def _check_section(tool: str, section: Any) -> Dict[str, Any]:
    where = f"rules for {tool!r}"
    if not isinstance(section, dict):
        raise ValueError(f"{where}: expected an object with {', '.join(KINDS)}")
    unknown = sorted(set(section) - set(KINDS))
    if unknown:        # a misspelt rule must not silently allow everything
        raise ValueError(f"{where}: unknown rule {unknown[0]!r}; the rules are {', '.join(KINDS)}")
    for kind in ("max", "ask_over"):
        bounds = section.get(kind, {})
        if not isinstance(bounds, dict) or not bounds:
            if kind in section:
                raise ValueError(f"{where}: {kind} needs {{\"parameter\": number}}")
            continue
        for param, bound in bounds.items():
            if _number(bound) is None:
                raise ValueError(f"{where}: {kind}.{param} must be a number, not {bound!r}")
    if "allow" in section:
        allowed = section["allow"]
        if not isinstance(allowed, dict) or not allowed:
            raise ValueError(f"{where}: allow needs {{\"parameter\": [\"value\", ...]}}")
        for param, values in allowed.items():
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise ValueError(f"{where}: allow.{param} must be a list of strings")
    if "per_run" in section:
        n = section["per_run"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ValueError(f"{where}: per_run must be a whole number, 0 or more")
    return section


class Rules:
    """Per-tool rules from a file or a dict. See the module docstring."""

    def __init__(self, tools: Dict[str, Any], source: str = "inline") -> None:
        if not isinstance(tools, dict):
            raise ValueError("Rules needs {tool name: {rule: ...}}")
        self.tools = {t: _check_section(t, s) for t, s in tools.items()}
        self.source = source
        self.digest = hashlib.sha256(canonical(self.tools).encode()).hexdigest()[:16]

    @classmethod
    def load(cls, path: str) -> "Rules":
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict) or set(doc) - {"tools", "comment"} or "tools" not in doc:
            raise ValueError(f"{path}: a rules file is {{\"tools\": {{...}}}} (and an optional \"comment\")")
        return cls(doc["tools"], source=str(path))

    def covers(self, tool: str) -> bool:
        return tool in self.tools

    def per_run(self, tool: str) -> Optional[int]:
        return self.tools.get(tool, {}).get("per_run")

    def stamp(self) -> Dict[str, str]:
        return {"source": self.source, "digest": self.digest}

    def decide(self, tool: str, args: Dict[str, Any]) -> str:
        """'allow' or 'ask'; raises RuleRefused. A missing or non-number argument fails closed."""
        section = self.tools[tool]
        for param, bound in section.get("max", {}).items():
            value = _number(args.get(param))
            if value is None:
                raise RuleRefused(tool, args, f"max.{param}", f"{param} is missing or not a number")
            if value > _number(bound):
                raise RuleRefused(tool, args, f"max.{param}", f"{param}={args[param]} is over {bound}")
        for param, values in section.get("allow", {}).items():
            if args.get(param) not in values or not isinstance(args.get(param), str):
                raise RuleRefused(tool, args, f"allow.{param}", f"{param}={args.get(param)!r} is not in the list")
        for param, bound in section.get("ask_over", {}).items():
            value = _number(args.get(param))
            if value is None:
                raise RuleRefused(tool, args, f"ask_over.{param}", f"{param} is missing or not a number")
            if value > _number(bound):
                return "ask"
        return "allow"


class Policy:
    """Wraps policy(tool, args) -> "allow" | "ask" | "deny" | ("deny", reason). Fails closed."""

    def __init__(self, fn: Callable[[str, Dict[str, Any]], Any]) -> None:
        if inspect.iscoroutinefunction(fn):
            raise TypeError("rules= takes a normal (not async) policy function: it runs inside admission")
        self.fn = fn
        self.source = "policy:" + getattr(fn, "__module__", "?") + "." + getattr(fn, "__qualname__", repr(fn))
        self.digest = ""

    def covers(self, tool: str) -> bool:
        return True

    def per_run(self, tool: str) -> Optional[int]:
        return None

    def stamp(self) -> Dict[str, str]:
        return {"source": self.source}

    def decide(self, tool: str, args: Dict[str, Any]) -> str:
        try:
            answer = self.fn(tool, json.loads(canonical(args)))    # a copy: the policy cannot change the call
        except Exception as exc:
            raise RuleRefused(tool, args, self.source, f"the policy raised {exc!r}") from exc
        reason = ""
        if isinstance(answer, tuple) and len(answer) == 2 and answer[0] == "deny":
            answer, reason = "deny", str(answer[1])
        if answer in ("allow", "ask"):
            return answer
        if answer == "deny":
            raise RuleRefused(tool, args, self.source, reason or "the policy said deny")
        raise RuleRefused(tool, args, self.source, f"the policy answered {answer!r}, not allow, ask or deny")


def gate_for(rules: Any, tool: str) -> Tuple[Any, Optional[int]]:
    """(gate, per_run) for @c4a.effect(rules=...): a Rules object or a policy function."""
    gate = rules if isinstance(rules, (Rules, Policy)) else Policy(rules) if callable(rules) else None
    if gate is None:
        raise TypeError("rules= takes c4a.Rules(...), c4a.Rules.load(path) or a function policy(tool, args)")
    if not gate.covers(tool):
        raise ValueError(f"the rules from {gate.source} have no section for {tool!r} (sections: "
                         f"{', '.join(sorted(gate.tools)) or 'none'}). Tool names are module + function name")
    return gate, gate.per_run(tool)
