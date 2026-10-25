# Idempotency keys for outside APIs

## The problem

Your agent calls a payment API. The API charges the card. Your process dies before it can
record the answer. On resume, the agent does not know whether the charge happened.

- Without Call4Agents, the agent charges again. Two charges.
- With `@c4a.effect`, the kit refuses to repeat it: `EffectInDoubt`. There is one charge, but a person has to check.
- With `@c4a.effect(idempotent_for=...)` and `c4a.idempotency_headers()`, the retry sends the
  **same key**. The API sees the key, does not charge again, and returns the first charge. There is one
  charge and no person is needed.

## How to use it

```python
import call4agents as c4a
c4a.init()

@c4a.effect(idempotent_for=24 * 3600)          # how long the API remembers keys (Stripe v1: at least 24 h)
def charge(amount, to):
    return requests.post(URL, json={"amount": amount, "to": to},
                         headers=c4a.idempotency_headers()).json()

with c4a.run("order-42"):                      # the id your framework resumes with
    charge(10, "alice")
```

- `c4a.idempotency_key()` returns the key as a string (64 hex characters). You can only call it inside an `@effect`.
- `c4a.idempotency_headers(header="Idempotency-Key")` returns `{header: key}`. For PayPal, pass
  `header="PayPal-Request-Id"`.
- The key depends on the run id, the tool name and the exact arguments. A crash, a retry or a resume of
  the same run gives the same key. A different amount, a different recipient or a different run gives a
  different key.
- With `c4a_key="..."` at call time (or `key=` on the decorator), you choose the key yourself.
  A key names one action: calling it again with arguments that mean something else raises `KeyConflict`
  and nothing runs (numbers are compared by value, so 10, 10.0 and Decimal("10.00") are the same amount).

## The exact rule

| Situation on resume | Result |
|---|---|
| The effect finished and its result is recorded | The recorded result comes back. The API is not called. |
| The effect started, no result, `idempotent_for` not set | `EffectInDoubt` (no blind retry) |
| The effect started, no result, the first attempt is younger than `idempotent_for` | It is sent again with the **same** key (on each such call). The API deduplicates it. |
| The same, but the first attempt is older than `idempotent_for` | `EffectInDoubt`. The API may have forgotten the key, so a retry could charge twice. |
| `reconcile=` given | Your reconcile function decides, as before. |

## Proof (run it yourself)

`python -m tests.test_idempotency` runs offline and uses no keys, in about 5 seconds. A fake payment
server removes duplicates by `Idempotency-Key` within a lifetime, like Stripe. A real child process
is killed after the server charged and before our record has the result. A new process then resumes.

| Check | Measured |
|---|---|
| F1 no kit | 2 charges |
| F2 kit, no key | 1 charge, resume raises `EffectInDoubt` |
| F3 kit + key | 1 charge, resume returns the same `ch_1`, both requests carried the same key |
| F3b | the key is at most 64 characters (Adyen's limit) |
| F4 key lifetime over | 1 charge, still `EffectInDoubt` (no blind retry) |
| F5 different amount | different key, 2 real charges (correct) |

Each guard is mutation-checked (`python -m tests.mutations.run_kit_mutations`). With the bug put back, a check fails. These mutations were caught:
removing the lifetime check (F4 fails), a key that changes per attempt (F3 fails), and removing the
same-key retry (F3 fails). A no-op control was missed, as it should be.

## Limits (what this does not do)

- It only helps if the outside API honours an idempotency key. If it does not, use `reconcile=`.
- Stripe v1 replays a saved response, including a saved `500` error. A same-key retry returns the old
  answer; it does not redo the work.
- If the model changes the arguments on a retry (for example "10" becomes "10.00"), the key changes.
  Use `c4a_key=` (for example an order id) when the arguments may change.
- `idempotent_for` must not be longer than the API's real key lifetime. We do not check that for you.
- Tested against a fake server, not against real Stripe, Adyen or PayPal.
