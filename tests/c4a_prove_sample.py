"""Sample for c4a prove (kit check K12): a guard that can fail, and one that cannot."""


def refund(amount: int) -> int:
    if amount <= 0:
        raise ValueError("refund must be positive")
    return amount


def real_guard():
    try:
        refund(-5)
    except ValueError:
        return True
    raise AssertionError("a negative refund went through")


def decorative_guard():
    refund(5)                      # never exercises the rule, so it cannot fail
    return True
