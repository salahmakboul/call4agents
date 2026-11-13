# Property tests

`tests/test_properties.py` checks rules that must hold for every input, not only for a few examples.
[Hypothesis](https://hypothesis.readthedocs.io/) makes the inputs and, when a rule breaks, shrinks the
input to a short case. Hypothesis is a test-only dependency. The `call4agents` package itself still needs
only the Python standard library.

```
pip install hypothesis
python -m tests.test_properties --require-hypothesis        # the CI run
python -m tests.test_properties --long                      # many more examples; prints its seed
python -m tests.test_properties --long --seed 1234          # repeat a long run
python -m tests.test_properties --only machine              # one group: machine, canonical, codec, files
```

Without Hypothesis the suite prints `skip` and passes; `--require-hypothesis` makes that a failure.

## How many examples

| group | CI run (each push, Linux, macOS, Windows, Python 3.12) | `--long` (weekly, Linux) |
|---|---|---|
| machine | 40 runs of up to 20 steps, plus a check that each of 15 paths was taken | 500 runs of up to 50 steps |
| canonical, codec | 200 examples per property | 3,000 per property |
| files | 200 damaged copies | 3,000 damaged copies |

The CI run is derandomized: the same examples every time, so a failure in CI can be repeated on a laptop.
The long run uses a new random seed each week and prints it. Measured on one Windows laptop: the CI run
takes 20 seconds when the machine is idle, and 1.5 to 5 minutes when other heavy jobs run at the same time.

## The properties

### a. The state machine (`--only machine`)

One run record, one approved tool `pay(item)` with `reconcile=` and `retry_on=`, and a fake provider that
counts real outside actions. Hypothesis chooses a sequence of steps: approve, deny, call (with or without
`c4a_key=`, with `retries=0` or `1`), crash inside the action before or after the outside action (with or
without the error being recorded), a `retry_on` error, another error before or after the action, resume,
reconcile answers (truthful, or "lagging": `c4a.UNKNOWN`), two callers at once, a caller that comes while
the first is still inside the action (Fix 17's case), and two callers resuming a call in doubt at once.

After every step:

- Each action key has at most one outside action.
- Every outside action had a "yes" for exactly its arguments, given before it and not cancelled by a "no".
- One "yes" is spent by at most one action (two `c4a_key` actions need two answers).
- A call that finished returns the recorded result on every later call, and does not run again.
- A call in doubt runs again only after the kit has proof it did not happen (a `retry_on` error or a
  reconcile answer of `None`); `c4a.UNKNOWN` never makes it run.
- Every result in the record is the result of a real outside action.
- The record only grows: each row keeps its bytes, and `seq` always rises.

The model also predicts the first decision of each call (replay, refuse, ask reconcile, or run) and
checks it.

On 0.7.1 this machine finds the double payment that Fix 17 removed. Hypothesis shrinks it to one step:
`still_running_and_a_racer(item='a', key=None)`.

### b. `canonical()` and `effect_key()` (`--only canonical`)

- The order of keys in a dict never changes the text or the key.
- Two JSON values get the same key exactly when they are the same value with the same spelling.
- A change in one place always changes the key.
- A different run id, tool name or arguments gives a different key (names without NUL; see below).
- The code's rule: `10`, `10.0` and `True` are three different keys; a tuple is spelled as a list.
- An object with no stable text form raises `TypeError`, wherever it is nested.
- Nesting deeper than 500 levels raises `TypeError`.
- A set gives the same text in three processes with different hash seeds.

### c. The codec (`--only codec`)

- encode, then store as canonical JSON text, then decode gives an equal value of the same types
  (JSON values, tuple, set, frozenset, bytes, bytearray as bytes, datetime, date, time, Decimal, dataclasses,
  models with `model_dump`/`model_validate`, dicts with any hashable keys).
- decode of any JSON, including records that name `os:system`, `builtins:eval`, `subprocess:Popen`,
  functions, plain classes and "half models", calls nothing and returns only plain data, dataclasses or
  models. Dangerous functions are replaced by recorders while this runs, so a bug is seen and does nothing.

### d. Damaged record files (`--only files`)

A record holds: A finished (result `RA`), B in doubt, C approved and unused, D never approved. Each
example damages a copy: random bit flips, flips aimed near row text, a run of random bytes, a run of zeros,
a cut at a random length, and sometimes a damaged, short, empty or missing secret file. Then it asks for A, B,
C and D through the real API.

Checked: A, B and D never run, B never comes back as done, C runs at most once, and every error is
`LedgerError`, `sqlite3.DatabaseError`, `EffectInDoubt` or `ApprovalRequired`.

Measured but not asserted: how often A came back with a different result (0 in our runs).

PF1 is a fixed check: four hand-damaged rows (an approval row that is not JSON, an `effect_approved` row
without its field, a `tool_done` row without its result, a format version that is not a number) each give
`LedgerError`.

## Mutations

`python -m tests.mutations.run_kit_mutations --suite tests.test_properties` puts each bug back in a copy of
the kit and runs the suite. A no-op control must be MISSED.

| mutation | caught by |
|---|---|
| a "yes" is never spent | machine |
| a "no" is ignored | machine |
| a "no" is ignored for a call in doubt | machine |
| every error counts as "did not happen" | machine |
| reconcile `None` is read as done | machine |
| `UNKNOWN` is read as `None` | machine |
| the result row is written twice | machine |
| a finished call is not replayed | machine |
| no owner check before reconcile (Fix 17) | machine |
| the codec calls whatever a record names | codec |
| a tuple comes back as a list | codec |
| keys depend on dict order | canonical |
| any object's `str()` is accepted in a key | canonical |
| a row without its field is not explained (PF1) | files |
| a damaged format version is not explained (PF1) | files |
| approval rows read with `json.loads` (PF1) | files |

## What this does NOT cover

- One process only. Several processes or machines on one record are not modelled here (see
  `tests/test_atomic_admission.py`, `tests/test_shared_record.py` and the crash tests with real kills).
- Crashes are simulated with exceptions and a failing write, not with real process kills.
- The fake provider is honest. A reconcile function that lies is out of scope.
- Limits, rules, tickets, undo, async effects, `idempotent_for` and PostgreSQL are not in the machine.
- Known collisions of the key, by the code's design, are not tested as errors: NUL in a run id or tool name
  (`effect_key("a\x00b", "c", ...)` equals `effect_key("a", "b\x00c", ...)`); int and str dict keys
  (`{1: x}` and `{"1": x}`); bytes and `{"__bytes__": hex}`; a Decimal, date, UUID, path or enum and its text.
- Text with a lone surrogate (half an emoji) makes `effect_key` raise `UnicodeEncodeError`. It fails loudly;
  it is not tested here.
- A damaged byte inside a stored result can change the result silently: SQLite keeps no checksum of row
  content, and `call4agents verify` does not see it. We measured 0 such cases in our runs, but it is possible.
