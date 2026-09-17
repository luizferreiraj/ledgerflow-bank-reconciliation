"""The pipeline's gate: it fails closed (see ADR 1).

A single equation, because balances are signed. There is no tolerance path: the accepted
difference is exactly 0.00.
"""

from __future__ import annotations

from decimal import Decimal

from .money import ZERO, to_text, sum_amounts
from .models import Diagnostic, Closing, Entry, Balance, Direction

TOLERANCE = ZERO


def compute(
    opening_balance: Balance, closing_balance: Balance, entries: list[Entry]
) -> Closing:
    total_credits = sum_amounts(l.amount for l in entries if l.direction is Direction.CREDIT)
    total_debits = sum_amounts(l.amount for l in entries if l.direction is Direction.DEBIT)
    computed = (opening_balance.amount + total_credits - total_debits).quantize(Decimal("0.01"))
    difference = computed - closing_balance.amount
    return Closing(
        opening_balance=opening_balance.amount,
        total_credits=total_credits,
        total_debits=total_debits,
        computed_balance=computed,
        declared_balance=closing_balance.amount,
        difference=difference,
        matches=abs(difference) <= TOLERANCE,
    )


def diagnose(closing: Closing, entries_read: int) -> Diagnostic | None:
    """None when the statement closes. A diagnostic when it doesn't, and then nothing is output."""
    if closing.matches:
        return None
    return Diagnostic(
        code="CLOSING_MISMATCH",
        message=(
            f"opening balance {to_text(closing.opening_balance)} "
            f"+ credits {to_text(closing.total_credits)} "
            f"- debits {to_text(closing.total_debits)} "
            f"= {to_text(closing.computed_balance)}, but the statement declares "
            f"{to_text(closing.declared_balance)}. "
            f"Difference of R$ {to_text(abs(closing.difference))}."
        ),
        details={
            "difference": to_text(closing.difference),
            "entries_read": str(entries_read),
        },
    )
