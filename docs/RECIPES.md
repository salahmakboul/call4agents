# Recipes: payments, bookings, emails, refunds

Each recipe is a real, runnable program: `python -m call4agents.examples.recipes <name>`. It runs the real kit
and kills the process at the worst moment, right after the outside world changed and before the result was
saved. The outside world is a small fake provider kept in a file, so it survives the kill like a real one.
`python -m tests.test_recipes` checks every number on this page in CI, and the mutation runner checks that
those checks fail when the protection is removed.

The four recipes cover the four kinds of outside world you will meet:

| Your provider... | Use | Recipe |
|---|---|---|
| removes repeated requests that carry the same key (Stripe, Adyen, PayPal) | `idempotent_for=` + `c4a.idempotency_key()` | payments, refunds |
| can be asked "did this happen?", but may lag | `reconcile=` returning the result, `None` or `c4a.UNKNOWN` | bookings |
| can neither remove repeats nor be asked | `retry_on=` for errors that prove nothing happened; a person for the rest | emails |
| must not go over an amount, per day or per run | `c4a.Limit`, `c4a.Rules` | refunds |

## Payments: an API with idempotency keys

```python
@c4a.effect(approval=True, idempotent_for=24 * 3600)
def pay(amount: int, to: str) -> dict:
    return bank.create_charge(amount, to, idempotency_key=c4a.idempotency_key())

with c4a.run("order-7"):
    c4a.approve(pay, amount=42, to="acme")    # when the person says yes to what they saw
    pay(amount=42, to="acme")
```

What happens:

1. The bank charges 42, and the process is killed before the result is saved.
2. The resume finds an intent with no result. The bank remembers keys for 24 hours, so the kit resends the
   request with the same key. The bank sees the repeat and returns the first charge, `ch_1`.
3. The agent then tries `pay(amount=4200, to="acme")`. It is refused: the "yes" was for 42.

Result: **1 charge**, and the resume returns `ch_1`. With HTTP, `c4a.idempotency_headers()` gives you
`{"Idempotency-Key": ...}`.

## Bookings: ask the provider, and accept "I can't tell yet"

```python
def find(guest: str, night: str):
    hit = hotel.search(guest=guest, night=night)
    if hotel.search_is_behind():
        return c4a.UNKNOWN      # cannot tell yet: never a reason to book again
    return hit                  # the booking, or None if it surely did not happen

@c4a.effect(reconcile=find)
def book(guest: str, night: str) -> dict:
    return hotel.book(guest, night)
```

What happens:

1. The hotel books, and the process is killed.
2. The first resume asks `find`. The hotel's search lags behind its writes, so `find` returns `c4a.UNKNOWN`. Nothing
   runs, nothing is recorded, and the call raises `EffectInDoubt`.
3. The second resume asks again. This time the search sees the booking, so the kit records it and returns it.

Result: **1 booking**. If `find` had returned `None` on the first resume, the room would have been booked twice.
That is why `None` must mean "surely did not happen".

## Emails: no key, no way to ask

```python
@c4a.effect(retry_on=(ConnectionRefusedError,))
def send_email(to: str, subject: str) -> str:
    return smtp.send(to, subject)
```

What happens:

1. The mail server refuses the connection. That error proves nothing was sent, because it is listed in
   `retry_on`, so the next call may run again. It sends the welcome email once.
2. The invoice email is sent, and the process is killed. There is no key and no way to ask, so the resume
   raises `EffectInDoubt` and does **not** send it again.
3. A person checks the Sent folder and records the answer:
   `call4agents doubt .c4a/ledger.sqlite` lists the action, and
   `call4agents resolve .c4a/ledger.sqlite <key> done "seen in the Sent folder"` records it.
4. The next call returns the recorded answer and sends nothing.

Result: **2 emails for 2 messages**. If a duplicate email is fine for you and a lost one is not, resolve with
`not-done` instead, and the next call sends it once more.

## Refunds: a shared limit, a "yes" over 100, nothing over 400

```python
daily = c4a.Limit("refunds-per-day", 500, cost=lambda order, amount: amount, per=86400)
rules = c4a.Rules({"refund": {"max": {"amount": 400}, "ask_over": {"amount": 100}}})

@c4a.effect(name="refund", limit=daily, rules=rules, idempotent_for=24 * 3600)
def refund(order: str, amount: int) -> dict:
    return shop.refund(order, amount, idempotency_key=c4a.idempotency_key())
```

What happens:

| Call | Result |
|---|---|
| refund A, 80 | runs: under 100, no "yes" needed |
| refund B, 300 | `ApprovalRequired`: over 100 |
| after `c4a.approve(refund, order="B", amount=300)`: refund B, killed after the shop refunded, then resumed | the resume resends with the same key and gets `re_2` back. The limit is held once, not twice |
| refund C, 200, approved | `LimitReached`: 80 + 300 + 200 is over 500. Nothing runs, and its "yes" stays unspent |
| refund D, 450 | `RuleRefused`: over the 400 rule. Nothing runs, and the refusal is recorded |

Result: **2 refunds, 380 in total**. Every process that uses the same record shares the same limit. The check
and the hold are in one transaction with the intent, so two workers cannot both take the last 100.

## Run them

The recipes ship with the package from the next release. Until then, run them from a checkout of the repository.

```bash
pip install call4agents
python -m call4agents.examples.recipes          # all four, about 6 seconds
python -m call4agents.examples.recipes refunds  # one
```

`Limit` and `Rules` are experimental (see [PLAN_1_0.md](PLAN_1_0.md)). The other parts used here are stable.
