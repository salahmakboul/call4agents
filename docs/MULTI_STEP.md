# A change of several steps that crashes in the middle

One job is often several actions: reserve a room, charge the card, send the confirmation. If the program dies
between two of them, a restart must not repeat a step that already happened, and a person must be able to see
which steps are done and which one is uncertain.

The example is `call4agents/examples/multi_step.py`. Each step writes one line to a fake shop file, so repeats can
be counted. The numbers below are asserted by `python -m tests.test_multi_step` (real processes, hard kills with
`os._exit` before or right after each step's effect, then a restart with the same run id).

## The recipe

```python
@c4a.effect(name="shop.reserve", reconcile=lambda order: "reserved" if shop_has("reserve", order) else None)
def reserve(order): ...

@c4a.effect(name="shop.charge", reconcile=lambda order: "charged" if bank_has("charge", order) else None)
def charge(order): ...

@c4a.effect(name="shop.email")          # no way to ask the mail provider: a crash here stops for a person
def email(order): ...

with c4a.run("order-A-1"):              # the same id on every restart
    reserve(order="A-1")
    charge(order="A-1")
    email(order="A-1")
```

1. Make each step its own `@c4a.effect`, and use one run id for the whole job.
2. Give a step a `reconcile=` when the outside system can say whether it happened. Return `None` only when it
   surely did not.
3. Do not swallow `c4a.EffectInDoubt` and carry on. Let it stop the job, or hand it to a person. If you cannot
   be sure of that (an agent loop that hands every error to the model), use `c4a.run("order-A-1", ordered=True)`:
   then no later step can start while an earlier one is uncertain (0.8).
4. After a crash, read `call4agents story <record> --run order-A-1`.

## What happens at each crash point

| | a step happens twice | the job after a restart |
|---|---|---|
| no kit (plain code) | 5 of 6 crash points (the sixth crashes before anything ran) | runs every step again |
| each step a `@c4a.effect`, no `reconcile=` | 0 of 6 | stops in doubt at the crashed step, every time; no later step runs |
| the same, with `reconcile=` on each step | 0 of 6 | finishes with exactly one reserve, one charge and one email, at every crash point and in the no-crash control |
| `@c4a.effect`, but the loop catches `EffectInDoubt` and carries on | 0 | the later steps run while the earlier one is uncertain; `story` says so |
| the same loop in `c4a.run(..., ordered=True)` (mode `guard_ordered`) | 0 | no later step runs (0 charges, 0 emails after a crash after reserve); each is refused as in doubt until the reserve is resolved |

The fourth row is the case to watch for. An agent loop that turns every error into a message for the model can do
this. The record still knows which step is uncertain, and `call4agents story` prints:

```
In short: 3 actions started, 2 done, 1 may or may not have happened.
A person must check the outside system before this run can be called finished.
Note: after reserve({"order": "A-1"}) was left uncertain, later actions still started: charge(...), email(...). Check that this was safe.
```

## Limits

- The shop and the bank are fake files, on one machine. A real provider's lookup can lag behind its writes;
  return `c4a.UNKNOWN` from `reconcile=` when it cannot tell yet (experimental).
- `story` lists only the steps that were started. A step your code never reached is not in the record.
- Reversing earlier steps when a later one fails is `undo=` and `c4a.undo()` (see API.md); it is not used here.
