# Running Call4Agents in production

This page says what the kit guarantees in a real product, what it costs, how to run it, and what is
still not proven. The numbers below were measured on one Windows 10 machine (Python 3.12, local SSD).
You can reproduce them with the commands shown. They will differ on your hardware.

## What it costs

`python -m tests.bench_c4a 2000`:

| | median | p99 |
|---|---|---|
| a plain Python call (no kit) | 0.2 µs | 0.4 µs |
| a **new** action (intent + result written to disk with a full sync) | 1.9 ms | 6.0 ms |
| a **repeated** action (recorded result returned) | 45 µs | 91 µs |
| approve + spend the "yes" + run the action | 5.0 ms | 14 ms |

- **Memory:** about 45 bytes kept per action (tracemalloc). The record lives on disk, not in memory.
- **Disk:** about 275 bytes per row in the main file. An action is 2 rows: the intent and the result.
  An approval adds 2 to 3 rows. The write-ahead file next to the record grows to about 4 MB, then is
  folded back in and shrunk to 1 MB.
- **Before this release,** a new action cost 22 ms (median), because a new database connection was
  opened for every read and write. Connections are now reused per thread. The durability rule did not
  change: the intent is on disk before the action runs.

If the action itself calls a network API (tens to hundreds of ms), the kit's 2 ms is small. If you
call very cheap local functions thousands of times per second, it is not small. Decorate only the
calls that change the outside world.

## What the big test shows

Run `python -m tests.bigtest_c4a`. It takes about 75 seconds, makes 0 LLM calls and uses no network
outside this machine.

**A. Chaos.**
- **Setup:** 12 processes × 6 rounds charge the same 300 orders at the same time through a fake payment
  API. The API answers late (25% of requests) and drops answers after charging (15%).
- **Disruption:** 46 of the 72 worker processes were killed at random points: before the call, after
  the API charged but before the record, or between actions. Half-way through, the approval secret was
  changed.

| | without the kit (control) | with the kit |
|---|---|---|
| orders charged twice or more | **244 of 300** (up to 6 times) | **0** |
| orders charged and recorded | 300 | 300 / 300 |
| approved refunds run twice / without approval | – | 0 / 0 (30 refunds, approvals from before the secret change) |
| left in doubt at the end | – | 0 |
| record integrity | – | ok |

**B. Long run.**
- 20,639 actions in one record, across 12 kills. On every restart, the 50 previous actions were called
  again on purpose.
- The secret changed half-way.
- Result: **0 actions sent twice**, integrity ok, 41,278 rows, 11.4 MB main file.
- `archive` moved the finished run out in 1.5 s.

**C. Bad input.** Each of these either worked or gave a clear error, with no unclear crash:
- random bytes as a record;
- a record cut in half;
- another program's SQLite file;
- a hand-edited row with broken JSON;
- a 10 MB argument;
- emoji, right-to-left and NUL characters;
- NaN and infinity;
- an argument nested 5,000 deep;
- a socket object as an argument.

**What the big test does not cover:**
- **several real machines:** all processes ran on one machine;
- **other systems at full size:** the full-size run above was on Windows 10. On Linux and macOS, CI runs
  the same test at a smaller size (`--quick`) on every change;
- **real payment providers:** the API was a fake one;
- **power loss:** not simulated. We rely on SQLite's full sync, and we did not test a power cut.

## Running it

**Where the record lives.** The record is one SQLite file. Put it on a **local disk**, shared by every
process on that machine that runs the same agents.
- **Network drives are refused,** because SQLite's locking is not reliable there. On Windows: a network
  share. On Linux: mount types NFS, SMB/CIFS, sshfs and 9p, read from `/proc/mounts` (check P5 runs on
  every change). On macOS, network drives are **not** detected; keep the record on a local disk.
  Pass `allow_network_drive=True` only if you know your drive is safe.
- **Several machines need one shared record, and that is not supported yet.** Once-only holds per
  record file. If two machines can run the same action, give each action an explicit `c4a_key`. Make
  sure the outside API honours idempotency keys (`idempotent_for=`), so it drops the duplicate.

**On call.** Nobody needs to read the database:
```
call4agents status  .c4a/ledger.sqlite                 # rows, actions, in doubt, integrity
call4agents doubt   .c4a/ledger.sqlite --fail-if-any   # list; exit 3 if any (for cron / alerts)
call4agents resolve .c4a/ledger.sqlite 3fa9c2 not-done "provider dashboard shows no charge"
call4agents resolve .c4a/ledger.sqlite 7b01de done     "charge ch_123 exists"
call4agents verify  .c4a/ledger.sqlite                 # exit 1 on damage or on a late result
```
- **`not-done`:** the next call runs the action again.
- **`done`:** the next call returns without running it.
- **Every resolution is a new row,** stamped "operator", with your note.
- **A late result:** if an action you marked `not-done` later returns after all, it is recorded as
  `LATE RESULT (may have happened twice)` and Python raises a warning. `verify` fails on it. Check the
  outside system by hand.

**Growth.** `call4agents archive <record> <archive.sqlite> 30` moves runs older than 30 days, in one
transaction, into an archive file with the same format.
- **What stays:** a run with anything in doubt, or with a late result, is never moved.
- **The record format is versioned.** A record written by a newer kit is refused, with a message to upgrade.

**Changing the secret.**
1. Set the new one with `C4A_SECRET`, or `c4a.init(secret=...)`.
2. List the old one in `C4A_OLD_SECRETS` (comma-separated), or `c4a.init(old_secrets=[...])`.

Approvals signed with the old secret keep working, and new approvals use the new secret. When no open
approval is older than the change, remove the old secret.

**What is stored.** The record keeps each action's arguments and results, and each approval's
arguments, **in plain text**. Treat it like your database: set access rights, back it up, and use
`archive` for retention. The approval secret file (`.c4a/secret`) must not be readable by anyone who
should not approve actions.

## Errors you may see, and what they mean

| Error | Meaning | What to do |
|---|---|---|
| `EffectInDoubt ... (crash during the effect)` | the process died while the action ran | `call4agents doubt`, get an authoritative answer from the provider (a read that finds nothing can lag), `resolve` |
| `EffectInDoubt ... (it raised TimeoutError(...))` | the function raised; the action may or may not have happened | same; or list errors that prove "nothing happened" in `retry_on=` |
| `ApprovalRequired` | no unused "yes" for exactly these arguments in this run | ask the person again |
| `RuntimeError: no current run` | called outside `with c4a.run(...)`, or in a thread that lost it | enter `c4a.run` in that thread |
| `TypeError: ... no stable text form` | an argument would give a different key after a restart | pass plain values or `c4a_key=` |
| `LedgerError: ... newer Call4Agents` | the record was written by a newer kit | upgrade; never downgrade a record |
| `LedgerError: ... network drive` | the record is not on a local disk | move it, or `allow_network_drive=True` |
| `LedgerError: row N ... is damaged` | someone edited the file, or it is corrupted | `call4agents verify`, restore from backup |

## Platforms

The systems and Python versions tested on every change are listed at the top of the
[README](../README.md#call4agents). They come from the continuous tests in `.github/workflows/tests.yml`. Anything not
listed there is not tested.

- **async (`asyncio`):** tested (async effects, concurrent calls in one event loop, the async LangGraph adapter).
- **Streaming (generators):** refused on purpose. A generator's work would run after its result was recorded.
- **Several machines sharing one record:** not supported. See "Where the record lives" above.
