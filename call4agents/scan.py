# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""call4agents scan <path> - experimental. Lists agent tools that act on the outside world with no guard.

It reads Python source with the standard library and runs nothing. A finding means: this function is given to a
model as a tool, its code sends, pays, deletes or writes, and we see no approval or once-only guard on it.
What it cannot see is printed with every report (LIMITS): a clean scan is not a proof of safety.
"""
import ast
import json
import re
import sys
from pathlib import Path

LIMITS = ("It reads Python only and runs nothing. It cannot see a guard in another file or service, tools built at "
          "runtime, or what a call does past one helper in the same file.")
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", "site-packages", "build", "dist", ".tox",
             "tests", "test"}

# A decorator with one of these last names gives the function to a model.
TOOL_DECORATORS = {"tool", "tool_plain", "function_tool", "kernel_function"}
# A call with one of these last names gives its first argument, or its func= argument, to a model.
TOOL_BASES = {"BaseTool", "Tool", "AsyncBaseTool"}
TOOLKIT_BASES = {"Toolkit", "BaseToolkit"}
TOOL_METHODS = ("_run", "_arun", "run", "forward", "execute", "call")
TOOL_MAKERS = {"Tool", "StructuredTool", "FunctionTool", "from_function", "tool"}

STRONG_CALLS = {
    "sendmail": "sends email", "chat_postMessage": "posts a message",
    "system": "runs a command", "popen": "runs a command", "rmtree": "deletes files", "unlink": "deletes a file",
    "remove": "deletes a file", "rmdir": "deletes a folder", "write_text": "writes a file",
    "write_bytes": "writes a file",
}
HTTP_WRITES = {"post": "sends an HTTP POST", "put": "sends an HTTP PUT", "patch": "sends an HTTP PATCH",
               "delete": "sends an HTTP DELETE"}
HTTP_CLIENTS = {"requests", "httpx", "aiohttp", "session", "client", "http_client", "http"}
MESSAGE_ROOTS = {"smtp", "smtplib", "server", "mail", "mailer", "bot", "slack", "telegram"}
COMMANDS = {"run", "call", "check_call", "check_output", "Popen"}
SQL_WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|REPLACE)\b", re.I)
PAYMENT_ROOTS = {"stripe", "paypal", "braintree", "square", "adyen"}
RISKY_NAME = re.compile(r"(?:^|_)(send|pay|charge|refund|transfer|delete|remove|drop|purchase|buy|cancel|book|publish|"
                        r"deploy|wire|email|payment)(?:_|$)", re.I)
# Words that make a risky-looking name a read, a draft or a check, not an action.
READ_NAME = re.compile(r"^a?(get|read|list|search|find|fetch|check|lookup|show|preview|draft|estimate|calculate|validate|grep|"
                       r"query|web_search|ask)(_|$)", re.I)

GUARD_DECORATOR = re.compile(r"(^|\.)effect$")
APPROVAL_WORDS = {"interrupt", "ApprovalRequired", "request_approval", "ask_human", "human_approval", "get_user_approval",
                  "confirm", "require_approval"}
APPROVAL_KWARGS = {"requires_approval", "needs_approval", "require_approval", "approval", "requires_confirmation",
                   "needs_approval_fn", "external_execution", "requires_user_input"}
ONCE_WORDS = re.compile(r"idempoten", re.I)
# In a file, these mean approval may be set up outside the tool, so we do not say it is missing.
FILE_APPROVAL = re.compile(r"interrupt_before|interrupt_on|HumanInTheLoopMiddleware|interrupt_after|DeferredToolRequests|"
                           r"ApprovalRequired|needs_approval")


def _dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        parts.append(_dotted(node.func))
    return ".".join(reversed(parts))


def _text(node) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(str(v.value) for v in node.values if isinstance(v, ast.Constant))
    return ""


def _is_write_mode(call) -> bool:
    mode = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "mode"), None)
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and any(c in mode.value for c in "wax+")


def effects_in(fn) -> list:
    """What this function's own code does to the outside world, as plain words."""
    found = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func)
        last = dotted.rsplit(".", 1)[-1]
        root = dotted.split(".", 1)[0].lower()
        if root in PAYMENT_ROOTS:
            found.append("calls a payment API")
        elif last in HTTP_WRITES and any(q in HTTP_CLIENTS or q.endswith(("client", "session")) for q in dotted.lower().split(".")[:-1]):
            found.append(HTTP_WRITES[last])
        elif last == "send_message" and root in MESSAGE_ROOTS:
            found.append("sends a message")
        elif root == "subprocess" and last in COMMANDS:
            found.append("runs a command")
        elif last in STRONG_CALLS and (last not in ("remove", "system", "popen", "rmdir", "unlink") or root in ("os", "path", "p", "file")):
            found.append(STRONG_CALLS[last])
        elif last == "open" and dotted in ("open", "io.open") and _is_write_mode(node):
            found.append("writes a file")
        elif last in ("execute", "executemany", "exec_driver_sql") and node.args and SQL_WRITE.match(_text(node.args[0])):
            found.append("writes to a database")
    return sorted(set(found))


def guards_in(fn) -> tuple:
    """(has approval, has once-only) seen on the function itself."""
    approval = once = False
    for dec in getattr(fn, "decorator_list", []):
        name = _dotted(dec.func if isinstance(dec, ast.Call) else dec)
        if GUARD_DECORATOR.search(name):
            once = True
            if isinstance(dec, ast.Call) and any(k.arg == "approval" and getattr(k.value, "value", False) for k in dec.keywords):
                approval = True
        if isinstance(dec, ast.Call):
            approval = approval or any(k.arg in APPROVAL_KWARGS and getattr(k.value, "value", True) for k in dec.keywords)
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            if _dotted(node.func).rsplit(".", 1)[-1] in APPROVAL_WORDS:
                approval = True
            if any(k.arg and ONCE_WORDS.search(k.arg) for k in node.keywords):
                once = True
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and ONCE_WORDS.search(node.value):
            once = True
    return approval, once


def _functions(tree) -> dict:
    return {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def toolkit_methods(tree) -> set:
    """Methods a toolkit class hands over in its __init__, such as tools=[self.send] or self.register(self.send)."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and any(_dotted(b).rsplit(".", 1)[-1] in TOOLKIT_BASES for b in n.bases):
            methods = {i.name: i for i in n.body if isinstance(i, (ast.FunctionDef, ast.AsyncFunctionDef))}
            init = methods.get("__init__")
            called = {id(c.func) for c in ast.walk(init) if isinstance(c, ast.Call)} if init else set()
            for a in ast.walk(init) if init else ():
                if isinstance(a, ast.Attribute) and a.attr in methods and _dotted(a) == f"self.{a.attr}" and id(a) not in called:
                    out.add(a.attr)
    return out


def class_tools(tree) -> dict:
    """Tools written as a class: the class name and the method that does the work."""
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and any(_dotted(b).rsplit(".", 1)[-1] in TOOL_BASES for b in n.bases):
            for item in n.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in TOOL_METHODS:
                    out[n.name] = item
                    break
    return out


def tool_names(tree) -> set:
    """Functions given to a model: by decorator, by a Tool(...) maker, or in a tools=[...] list."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in n.decorator_list:
                if _dotted(dec.func if isinstance(dec, ast.Call) else dec).rsplit(".", 1)[-1] in TOOL_DECORATORS:
                    out.add(n.name)
        elif isinstance(n, ast.Call):
            name = _dotted(n.func).rsplit(".", 1)[-1]
            if name in TOOL_MAKERS:
                for arg in n.args[:1] + [k.value for k in n.keywords if k.arg in ("func", "function", "fn", "coroutine")]:
                    if isinstance(arg, ast.Name):
                        out.add(arg.id)
            for k in n.keywords:
                if k.arg == "tools" and isinstance(k.value, (ast.List, ast.Tuple)):
                    out.update(e.id for e in k.value.elts if isinstance(e, ast.Name))
    return out | toolkit_methods(tree)


def _with_helpers(fn, functions) -> list:
    """The function's effects, following calls one level into helpers in the same file."""
    found = effects_in(fn)
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            helper = functions.get(node.func.id)
            if helper is not None and helper is not fn:
                found += [f"{e} (in {node.func.id})" for e in effects_in(helper)]
    return sorted(set(found))


def scan_source(source: str, filename: str = "<source>") -> list:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    functions = {**_functions(tree), **class_tools(tree)}
    file_approval = bool(FILE_APPROVAL.search(source))
    findings = []
    for name in sorted(tool_names(tree) | set(class_tools(tree))):
        fn = functions.get(name)
        if fn is None:
            continue
        effects = _with_helpers(fn, functions)
        if READ_NAME.match(name) and all(e.startswith(("sends an HTTP POST", "runs a command")) for e in effects):
            continue                                  # searches often POST their query, and grep runs a command
        if not effects and RISKY_NAME.search(name) and not READ_NAME.match(name):
            # A name alone is weak evidence, so it is kept apart from what the code was seen doing.
            effects, confidence = ["name suggests an action"], "name only"
        else:
            confidence = "code"
        if not effects:
            continue
        approval, once = guards_in(fn)
        if approval and once:
            continue
        missing = [] if approval else ["unknown: approval may be set up elsewhere in this file" if file_approval else "approval"]
        missing += [] if once else ["once-only guard"]
        findings.append({"file": filename, "line": fn.lineno, "tool": name, "does": effects, "confidence": confidence,
                         "missing": missing})
    return findings


def actions_in(source: str) -> set:
    """Functions that look like actions, tools or not. crashtest watches these in a script with no guard."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    functions = _functions(tree)
    # Saving a file alone is how an agent keeps its own state, so it needs a risky name too.
    found = {name for name, fn in functions.items() if not READ_NAME.match(name)
             and (RISKY_NAME.search(name) or set(effects_in(fn)) - {"writes a file"})}
    # A function that calls an action is the loop around it, not the action.
    return {name for name in found if not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in found - {name}
        for node in ast.walk(functions[name]))}


def python_files(path: Path) -> list:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*.py") if not SKIP_DIRS & set(q.lower() for q in p.relative_to(path).parts[:-1]))


def scan(path) -> dict:
    root = Path(path)
    files = python_files(root)
    findings = []
    tools = 0
    for f in files:
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(source)
            tools += len(tool_names(tree) | set(class_tools(tree)))
        except (SyntaxError, ValueError):
            pass
        findings += scan_source(source, str(f))
    return {"files": len(files), "tools": tools, "findings": findings}


def main(argv) -> int:
    as_json = "--json" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths or not Path(paths[0]).exists():
        print("usage: call4agents scan <file or folder> [--json]")
        return 2
    result = scan(paths[0])
    if as_json:
        print(json.dumps(result, indent=1))
        return 1 if result["findings"] else 0
    print(f"call4agents scan (experimental): {result['files']} Python files, {result['tools']} tools given to a model")
    for f in result["findings"]:
        print(f"\n  {f['file']}:{f['line']}  {f['tool']}")
        print(f"    does:    {', '.join(f['does'])}")
        print(f"    missing: {', '.join(f['missing'])}")
    print(f"\n{len(result['findings'])} tool(s) to look at." if result["findings"] else "\nNothing found.")
    print(LIMITS)
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
