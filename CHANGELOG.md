# Changelog

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
