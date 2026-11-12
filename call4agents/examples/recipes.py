# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Four recipes: payments, bookings, emails and refunds. Each one runs the real kit, with a real process
crash at the worst moment, against a small fake provider kept in a file (so it survives the crash).

    python -m call4agents.examples.recipes              # all four, with the story of each
    python -m call4agents.examples.recipes payments     # one of: payments bookings emails refunds

Offline, no keys, standard library only. docs/RECIPES.md explains each one; tests/test_recipes.py checks
the numbers printed here."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import call4agents as c4a

CRASH = "C4A_RECIPE_CRASH"


# ------------------------------------------------------------------ small fake providers, kept in a file
class Store:
    """A JSON file: what the outside world remembers after our process dies."""

    def __init__(self, work: Path, name: str, empty: dict) -> None:
        self.path = work / f"{name}.json"
        if not self.path.exists():
            self.save(empty)

    def load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict) -> None:
        self.path.write_text(json.dumps(data), encoding="utf-8")


def crash_here() -> None:
    if os.environ.get(CRASH) == "1":
        os._exit(17)                    # a hard kill: no exception handler, no clean-up, nothing saved


# ------------------------------------------------------------------ 1. payments (an API with idempotency keys)
def payments_tools(work: Path):
    bank = Store(work, "bank", {"charges": [], "by_key": {}})

    def create_charge(amount: int, to: str, idempotency_key: str) -> dict:
        """Like Stripe: a second request with the same Idempotency-Key returns the first charge."""
        data = bank.load()
        if idempotency_key in data["by_key"]:
            return data["by_key"][idempotency_key]
        charge = {"id": f"ch_{len(data['charges']) + 1}", "amount": amount, "to": to}
        data["charges"].append(charge)
        data["by_key"][idempotency_key] = charge
        bank.save(data)
        return charge

    @c4a.effect(approval=True, idempotent_for=24 * 3600, name="pay", wait=0.2)
    def pay(amount: int, to: str) -> dict:
        charge = create_charge(amount, to, idempotency_key=c4a.idempotency_key())
        crash_here()                    # the bank charged; we die before the result is recorded
        return charge
    return pay, bank


def payments_step(work: Path, step: str) -> dict:
    pay, bank = payments_tools(work)
    with c4a.run("order-7"):
        if step == "approve":
            c4a.approve(pay, amount=42, to="acme")
            return {}
        if step == "pay":
            return {"charge": pay(amount=42, to="acme")}
        if step == "changed":
            try:
                pay(amount=4200, to="acme")
                return {"changed": "PAID"}
            except c4a.ApprovalRequired:
                return {"changed": "refused"}
    raise ValueError(step)


def payments_story(work: Path) -> dict:
    step(work, "payments", "approve")
    step(work, "payments", "pay", crash=True)           # charged, then killed
    resumed = step(work, "payments", "pay")             # resent with the SAME key: the bank returns ch_1
    changed = step(work, "payments", "changed")
    charges = Store(work, "bank", {}).load()["charges"]
    return {"charges": len(charges), "resumed_charge": resumed["charge"]["id"], "changed_amount": changed["changed"]}


# ------------------------------------------------------------------ 2. bookings (no keys; a search that lags)
def bookings_tools(work: Path):
    hotel = Store(work, "hotel", {"bookings": [], "lagging": 0})

    def find(guest: str, night: str):
        """The hotel's search. Right after a booking it lags: it cannot tell yet."""
        data = hotel.load()
        if data["lagging"] > 0:
            data["lagging"] -= 1
            hotel.save(data)
            return c4a.UNKNOWN
        for b in data["bookings"]:
            if b["guest"] == guest and b["night"] == night:
                return b
        return None                     # the search is up to date and has no booking: it did not happen

    @c4a.effect(reconcile=find, name="book", wait=0.2)
    def book(guest: str, night: str) -> dict:
        data = hotel.load()
        booking = {"id": f"bk_{len(data['bookings']) + 1}", "guest": guest, "night": night}
        data["bookings"].append(booking)
        data["lagging"] = 1             # the next search will not see it yet
        hotel.save(data)
        crash_here()
        return booking
    return book, hotel


def bookings_step(work: Path, step: str) -> dict:
    book, _ = bookings_tools(work)
    with c4a.run("trip-3"):
        try:
            return {"booking": book(guest="ana", night="2026-12-01")}
        except c4a.EffectInDoubt as exc:
            return {"in_doubt": str(exc)[:80]}


def bookings_story(work: Path) -> dict:
    step(work, "bookings", "book", crash=True)          # booked, then killed
    first = step(work, "bookings", "book")              # the search lags: UNKNOWN, nothing runs
    second = step(work, "bookings", "book")             # the search sees it: recorded, not booked again
    bookings = Store(work, "hotel", {}).load()["bookings"]
    return {"bookings": len(bookings), "first_resume": "in doubt" if "in_doubt" in first else "ran",
            "second_resume": second.get("booking", {}).get("id")}


# ------------------------------------------------------------------ 3. emails (no keys, no search)
def emails_tools(work: Path):
    smtp = Store(work, "smtp", {"sent": [], "down": False})

    @c4a.effect(retry_on=(ConnectionRefusedError,), name="send_email", wait=0.2)
    def send_email(to: str, subject: str) -> str:
        data = smtp.load()
        if data["down"]:
            raise ConnectionRefusedError("mail server refused the connection")   # nothing was sent
        data["sent"].append({"to": to, "subject": subject})
        smtp.save(data)
        crash_here()
        return "sent"
    return send_email, smtp


def emails_step(work: Path, step: str) -> dict:
    send_email, smtp = emails_tools(work)
    if step in ("down", "up"):
        data = smtp.load()
        data["down"] = step == "down"
        smtp.save(data)
        return {}
    if step == "resolve":               # what `call4agents resolve <record> <key> done "note"` does
        from call4agents import ops
        lg = c4a.current_ledger()
        doubt = [k for k, a in ops.actions(lg).items() if "IN DOUBT" in a["state"]]
        for k in doubt:
            ops.resolve(lg, k, "done", note="seen in the Sent folder", result="sent")
        return {"resolved": len(doubt)}
    subject = {"welcome": "Welcome", "invoice": "Your invoice"}[step]
    with c4a.run("customer-12"):
        try:
            return {"result": send_email(to="li@example.com", subject=subject)}
        except ConnectionRefusedError:
            return {"result": "refused"}
        except c4a.EffectInDoubt:
            return {"result": "in doubt"}


def emails_story(work: Path) -> dict:
    step(work, "emails", "down")
    a = step(work, "emails", "welcome")                 # refused: proven not sent
    step(work, "emails", "up")
    b = step(work, "emails", "welcome")                 # so it may run again: sent once
    step(work, "emails", "invoice", crash=True)         # sent, then killed
    c = step(work, "emails", "invoice")                 # no way to check: in doubt, NOT sent again
    step(work, "emails", "resolve")                     # a person checks the Sent folder
    d = step(work, "emails", "invoice")                 # returns what the person recorded
    sent = Store(work, "smtp", {}).load()["sent"]
    return {"emails": len(sent), "steps": [a["result"], b["result"], c["result"], d["result"]]}


# ------------------------------------------------------------------ 4. refunds (a shared limit and rules)
def refunds_tools(work: Path):
    shop = Store(work, "shop", {"refunds": [], "by_key": {}})
    daily = c4a.Limit("refunds-per-day", 500, cost=lambda order, amount: amount, per=86400)
    rules = c4a.Rules({"refund": {"max": {"amount": 400}, "ask_over": {"amount": 100}}})

    @c4a.effect(name="refund", limit=daily, rules=rules, idempotent_for=24 * 3600, wait=0.2)
    def refund(order: str, amount: int) -> dict:
        data = shop.load()
        key = c4a.idempotency_key()
        if key not in data["by_key"]:
            data["by_key"][key] = {"id": f"re_{len(data['refunds']) + 1}", "order": order, "amount": amount}
            data["refunds"].append(data["by_key"][key])
            shop.save(data)
        crash_here()
        return data["by_key"][key]
    return refund, shop


def refunds_step(work: Path, step: str) -> dict:
    refund, _ = refunds_tools(work)
    order, amount = step.split(":")[-2], int(step.split(":")[-1])
    with c4a.run("support-ticket-88"):
        if step.startswith("approve:"):
            c4a.approve(refund, order=order, amount=amount)
            return {}
        try:
            return {"result": refund(order=order, amount=amount)["id"]}
        except c4a.ApprovalRequired:
            return {"result": "needs a yes"}
        except c4a.LimitReached:
            return {"result": "over the daily limit"}
        except c4a.RuleRefused as exc:
            return {"result": f"refused by rule {exc.rule}"}


def refunds_story(work: Path) -> dict:
    out = [step(work, "refunds", "A:80")["result"]]                 # small: allowed without a yes
    out.append(step(work, "refunds", "B:300")["result"])            # over 100: needs a yes
    step(work, "refunds", "approve:B:300")
    step(work, "refunds", "B:300", crash=True)                      # refunded, then killed
    out.append(step(work, "refunds", "B:300")["result"])            # resent with the same key: re_2, not a new one
    step(work, "refunds", "approve:C:200")
    out.append(step(work, "refunds", "C:200")["result"])            # 80 + 300 + 200 > 500
    out.append(step(work, "refunds", "D:450")["result"])            # over the 400 rule
    refunds = Store(work, "shop", {}).load()["refunds"]
    return {"refunds": len(refunds), "total": sum(r["amount"] for r in refunds), "steps": out}


# ------------------------------------------------------------------ running steps in separate processes
RECIPES = {"payments": (payments_step, payments_story), "bookings": (bookings_step, bookings_story),
           "emails": (emails_step, emails_story), "refunds": (refunds_step, refunds_story)}
STORIES = {
    "payments": "One approved payment of 42; the bank charges and the process is killed before the result is "
                "saved. The resume resends with the same idempotency key, so the bank returns the first charge. "
                "Then the agent tries 4200 on the same yes.",
    "bookings": "A booking, then a kill. The hotel's search lags behind, so the first resume cannot tell and "
                "books nothing; the second resume finds the booking and records it.",
    "emails": "The mail server refuses a connection (proven not sent), then accepts. An invoice email is sent and "
              "the process is killed. There is no way to check, so the resume stops in doubt instead of sending "
              "again; a person checks and records it as sent.",
    "refunds": "Refunds share a daily limit of 500, a yes is needed over 100, and nothing over 400 is allowed. "
               "One approved refund is killed after the shop refunded it, and resumed.",
}


def child_env(crash: bool) -> dict:
    """The environment for a step's process: it imports THIS copy of call4agents, wherever it was started from."""
    root = str(Path(c4a.__file__).resolve().parents[1])
    path = os.pathsep.join(p for p in (root, os.environ.get("PYTHONPATH", "")) if p)
    return dict(os.environ, PYTHONPATH=path, **{CRASH: "1" if crash else "0"})


def step(work: Path, recipe: str, name: str, crash: bool = False) -> dict:
    env = child_env(crash)
    proc = subprocess.run([sys.executable, "-m", "call4agents.examples.recipes", "_step", recipe, str(work), name],
                          env=env, capture_output=True, text=True, timeout=120)
    if crash:
        if proc.returncode != 17:
            raise RuntimeError(f"{recipe}/{name}: expected the crash (exit 17), got {proc.returncode}: {proc.stderr[-400:]}")
        return {}
    if proc.returncode != 0:
        raise RuntimeError(f"{recipe}/{name} failed ({proc.returncode}): {proc.stderr[-600:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def run_recipe(recipe: str, work: Path = None) -> dict:
    work = Path(work or tempfile.mkdtemp(prefix=f"c4a-recipe-{recipe}-"))
    return RECIPES[recipe][1](work)


def main(argv) -> int:
    if argv[:1] == ["_step"]:
        recipe, work, name = argv[1], Path(argv[2]), argv[3]
        c4a.init(str(work / ".c4a" / "ledger.sqlite"))
        print(json.dumps(RECIPES[recipe][0](work, name)))
        return 0
    chosen = argv or list(RECIPES)
    for recipe in chosen:
        if recipe not in RECIPES:
            print(f"unknown recipe {recipe!r}; choose from {', '.join(RECIPES)}")
            return 2
        print(f"{recipe}: {STORIES[recipe]}")
        print(f"  -> {json.dumps(run_recipe(recipe))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
