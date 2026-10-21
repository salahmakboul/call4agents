# Changelog

## 0.2.1

Metadata only, no code changes: links to the website and the guides (Homepage, Documentation), more keywords and classifiers on PyPI, and a link to the guides in the README.

## 0.2.0

**New**
- **Adapters for Pydantic AI, the OpenAI Agents SDK and MCP** (`call4agents.adapters.pydantic_ai`, `.openai_agents`, `.mcp`). They leave the framework's context argument out of the key; plain `@c4a.effect` refuses it, because it changes on every call. They also take the run id from the framework: the conversation id, the run context or the MCP session id. Each has a copy-and-run example that CI runs against the real framework on Linux, macOS and Windows.
- **Undo:** `@c4a.effect(undo=...)` and `c4a.undo()` reverse a run's finished actions, newest first. Each undo runs once only, a crash half-way continues where it stopped, and a failed undo stops the walk and is "in doubt" until a person resolves it. Developers asked for this in Pydantic AI issues #9141, #4679 and #7247.
- **`exclude=`** on `@c4a.effect`: parameters left out of the key.
- **`call4agents report`:** prints your versions and a link to the issue form with them filled in. It sends nothing.
- **An animated 30-second demo** at the top of the README (`docs/demo.svg`), built from a real `quickstart` run.

**Checks added:** K20, P18, U1–U6, the adapter checks, and mutations for each (`python -m tests.mutations.run_kit_mutations`).

## 0.1.0 (first public release)

**Licence: Apache-2.0.** Companies can use, change and ship it, with a patent grant. The NOTICE file must be kept, so we stay credited, and section 6 gives no right to use the project's name.

**What it does**
- **Once-only actions** (`@c4a.effect`): a crash, retry or resume does not repeat an action. An action interrupted half-way is reported as "in doubt" instead of being repeated.
- **Approvals bound to exact arguments:** one "yes" covers one action, `c4a.deny` is enforced, and racing callers on one "yes" run once.
- **Idempotency keys for outside APIs** (`c4a.idempotency_headers()`, `idempotent_for=`).
- **Record, replay and diff** for model calls, keeping result types; `prove` for mutation-testing your own guards.
- **LangGraph / LangChain adapter;** the decorator also works in Pydantic AI, the OpenAI Agents SDK and plain Python.
- **Commands:** `quickstart`, `selftest`, `doctor`.

**For production**
- **On call:** `status`, `doubt [--fail-if-any]`, `resolve`, `archive`, `verify`, so nobody has to read the database.
- **Long-running systems:** a versioned record format, secret rotation (`old_secrets=`, `C4A_OLD_SECRETS`), and an atomic secret file for many processes.
- **Speed:** about 1.9 ms per new action on our test machine (one reused connection per thread).
- **Clear errors** for damaged or foreign record files, network drives, unstable arguments, generators, unnamed lambdas and results that cannot be recorded.
- **Late results** are recorded and reported, never hidden.
- **Safe replay:** a crafted record cannot make the decoder call a function.
