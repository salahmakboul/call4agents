# Call4Agents v0.1 threat model

This covers the two v0.1 guarantees: **once-only side effects** and **approvals bound to exact arguments**. Everything here runs inside your agent's own process, on your machine.

## What "once-only" means

**At most once, with "in doubt" flagged.** It does not mean exactly once.

Before a decorated effect runs, an *intent* row is written to the run record (SQLite, `synchronous=FULL`). A *done* row with the result is written right after it returns.

| A crash happens... | What happens on the next attempt |
|---|---|
| before the intent row | The effect runs once. It never ran before. |
| after the done row | The recorded result is returned. The effect does not run again. |
| **during the effect** (intent written, done not written) | **We cannot know** whether the email left. We raise `EffectInDoubt` and do **not** repeat it. If you pass `reconcile=`, your check asks the outside system: if it finds the effect, we record it; if it reports the effect missing, we run it once more. |

**Parallel callers:** many callers on the same effect at the same moment are serialised by a unique index on the intent. Exactly one runs it; the others wait for its result (up to `wait` seconds) or get `EffectInDoubt`. Measured in `tests/stress_c4a_ledger.py`: 14 callers, 1 email.

**The default key** is (run id, tool name, canonical arguments). A deliberate second identical action needs `c4a_key="..."`.

## Where the approval secret lives

1. The `C4A_SECRET` environment variable, if it is set.
2. Otherwise a 32-byte random file, `.c4a/secret`, created next to the run record. We set its mode to 0600; on Windows that mode is not enforced, so rely on the folder's access rights.
3. Or `c4a.init(secret=...)` in code.

The secret never enters the model's context, the run record, or any log.

## What the signed approval protects against

An approval is stored as `HMAC-SHA256(secret, tool + canonical arguments)`. At execution time the MAC is recomputed from the arguments **about to run**, and the call is refused unless the two match.

- **Arguments changed after the "yes":**
  - edited saved state (Pydantic AI, LangGraph and LangChain HITL in our tests);
  - a model asked again after a restart (LangGraph one-node);
  - a forged unkeyed fingerprint (OpenAI Agents SDK).
- **A hand-written or edited approval row:** without the secret, nobody can produce a valid MAC (kit check K5).
- **Replaying an approval in another run:** approvals are scoped to the run id.
- **Using one "yes" twice:** each approval can be spent by one action only, and a later `deny` cancels it (K17).
- **A crafted run record used for replay or diff:** the record stores result types by class name. The kit
  only rebuilds real dataclasses and real classes with `model_validate` and `model_dump`. A record naming a
  function (for example `os:system`) is refused before anything is called (K19). Opening a record still imports the module it names, so replay or diff only record files you trust.

## What it cannot protect against

- **Anyone who can read the secret** (same user, same machine, a leaked env var). They can sign any approval. Keep the secret out of shared folders and repositories, and rotate it by deleting `.c4a/secret`.
- **A bad "yes":** if the person approves the wrong thing, or is shown misleading text, the signature binds exactly that wrong thing.
- **Code that calls the effect undecorated,** or deletes the run record.
- **Effects outside the decorated function:** a tool that also writes somewhere else, or sub-processes.
- **Lying about the effect:** we record what the function returned, not what really happened in the outside world.
- **A crash during the effect:** we flag it as in doubt; we cannot resolve it without your `reconcile`.
- **Prompt injection, sandboxing, identity and tokens:** out of scope.
- **Several machines:** the run record is a local SQLite file, so once-only holds only for callers that share that one file.

## How each claim is checked (offline)

| Check | What it shows |
|---|---|
| `tests/test_c4a_kit.py` K1–K19 | Each check is mutation-proven (`python -m tests.mutations.run_kit_mutations`) |
| `tests/stress_c4a_ledger.py` | Parallel writers, kills mid-write, simultaneous callers |
| `python -m call4agents demo` | The demo |

All are synthetic: fake tools, fake crashes (`os._exit`), no real email or payment system.
