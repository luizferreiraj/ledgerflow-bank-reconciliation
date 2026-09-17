"""The single conversion point between text and money.

No other module in the package builds a Decimal from a raw string. The ban on float is
structural: there is no path in this module that produces or accepts a float.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

_NUMBER = r"(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}"
_BR_AMOUNT = re.compile(rf"^(?P<neg>-)?\s*(?:R\$\s*)?(?P<number>{_NUMBER})$")

NUMBER_PATTERN = _NUMBER


class UnreadableAmount(ValueError):
    """Text that is not a money amount in Brazilian format."""


def to_decimal(raw: str) -> Decimal:
    """'1.482,65' | 'R$ 1.482,65' | '-18,75' | '- R$ 27,90' -> Decimal with 2 places."""
    string = raw.strip()
    matched = _BR_AMOUNT.match(string)
    if matched is None:
        raise UnreadableAmount(f"amount is not in Brazilian format: {raw!r}")
    number = matched.group("number").replace(".", "").replace(",", ".")
    amount = Decimal(number).quantize(CENT, rounding=ROUND_HALF_UP)
    return -amount if matched.group("neg") else amount


def to_text(amount: Decimal) -> str:
    """Serialization for the JSON envelope: a string, never a number."""
    return str(amount.quantize(CENT, rounding=ROUND_HALF_UP))


def is_amount(raw: str) -> bool:
    return _BR_AMOUNT.match(raw.strip()) is not None


def sum_amounts(amounts) -> Decimal:
    total = ZERO
    for amount in amounts:
        total += amount
    return total.quantize(CENT, rounding=ROUND_HALF_UP)
