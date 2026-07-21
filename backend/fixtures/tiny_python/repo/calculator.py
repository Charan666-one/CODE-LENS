"""Arithmetic helpers."""


def add(a, b):
    """Return the sum of a and b."""
    return a + b


def multiply(a, b):
    """Return a multiplied by b, the long way."""
    total = 0
    for _ in range(b):
        total = add(total, a)
    return total
