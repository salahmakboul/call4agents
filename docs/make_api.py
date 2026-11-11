# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Write docs/API.md from the code itself: every public name, its signature and its docstring.

    python docs/make_api.py            # rewrite docs/API.md
    python docs/make_api.py --check    # exit 1 if docs/API.md is out of date (CI)

Standard library only. Adapters whose framework is not installed are still listed: their module is read
with `ast`, so nothing is imported that needs the framework."""
import ast
import importlib
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "API.md"
sys.path.insert(0, str(ROOT))

STABLE = {"init", "run", "effect", "approve", "deny", "current_ledger", "idempotency_key", "idempotency_headers",
          "ApprovalRequired", "EffectInDoubt", "KeyConflict", "UNKNOWN"}
EXPERIMENTAL = {"pause", "decide", "TicketRefused", "Limit", "LimitReached", "Rules", "RuleRefused"}
TOOLS = {"recorded", "diff", "prove", "undo", "UndoFailed", "ReplayMismatch", "ReplayMissing", "Ledger"}
GROUPS = [("Stable", STABLE, "Covered by the 1.0 promise (see PLAN_1_0.md)."),
          ("Experimental", EXPERIMENTAL, "Works and is tested; the exact form may still change in a 1.x release."),
          ("Testing and inspection tools", TOOLS, "Useful around the once-only promise; not part of it.")]
ADAPTERS = ["langgraph", "pydantic_ai", "openai_agents", "mcp", "pytest_plugin", "crewai", "llamaindex",
            "smolagents", "google_adk", "agent_framework"]


def clean(doc):
    return inspect.cleandoc(doc).strip() if doc else "(no docstring)"


def signature(obj):
    if inspect.isclass(obj):
        try:
            return f"class {obj.__name__}{inspect.signature(obj)}"
        except (TypeError, ValueError):
            return f"class {obj.__name__}"
    if callable(obj):
        try:
            return f"{obj.__name__}{inspect.signature(obj)}"
        except (TypeError, ValueError):
            return obj.__name__
    return repr(obj)


def describe(name, obj):
    if not (inspect.isclass(obj) or inspect.isfunction(obj) or inspect.isbuiltin(obj)):
        doc = clean(type(obj).__doc__)
        return f"### `{name}`\n\n```\n{name} = {obj!r}\n```\n\n{doc}\n"
    return f"### `{name}`\n\n```python\n{signature(obj)}\n```\n\n{clean(obj.__doc__)}\n"


def adapter_section(mod):
    path = ROOT / "call4agents" / "adapters" / f"{mod}.py"
    if not path.exists():
        return ""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parts = [f"### `call4agents.adapters.{mod}`\n\n{clean(ast.get_docstring(tree))}\n"]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            args = ast.unparse(node.args)
            parts.append(f"#### `{mod}.{node.name}`\n\n```python\n{node.name}({args})\n```\n\n"
                         f"{clean(ast.get_docstring(node))}\n")
    return "\n".join(parts)


def build():
    c4a = importlib.import_module("call4agents")
    listed = set().union(*(g[1] for g in GROUPS))
    missing = sorted(set(c4a.__all__) - listed)
    if missing:
        raise SystemExit(f"make_api: names in __all__ with no group: {missing} (add them to a group)")
    out = ["# API reference",
           "",
           "This page is generated from the code by `python docs/make_api.py`. Do not edit it by hand; CI fails if",
           "it is out of date. Import the package as `import call4agents as c4a`.",
           ""]
    for title, names, note in GROUPS:
        out += [f"## {title}", "", note, ""]
        for name in [n for n in c4a.__all__ if n in names]:
            out.append(describe(name, getattr(c4a, name)))
    out += ["## Framework adapters", "",
            "Each adapter leaves the framework's context object out of the key and finds the run id for you.", ""]
    for mod in ADAPTERS:
        section = adapter_section(mod)
        if section:
            out.append(section)
    return "\n".join(out).rstrip() + "\n"


def main(argv):
    text = build()
    if "--check" in argv:
        old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if old != text:
            print("docs/API.md is out of date: run python docs/make_api.py")
            return 1
        print("docs/API.md is up to date")
        return 0
    OUT.write_bytes(text.encode("utf-8"))
    print(f"wrote {OUT.relative_to(ROOT)} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
