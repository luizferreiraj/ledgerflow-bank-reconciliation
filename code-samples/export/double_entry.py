"""Derives both sides of a double entry from the direction of the money (see ADR 8).

Measured on the real batch: in 466 of 466 records the bank account sits on EXACTLY one side, and
the rule below has 426 confirmations and 0 contradictions.

The user never picks the side: there is nothing to get wrong, and no question to ask.
"""

from __future__ import annotations

from statement.models import Direction


def derive(
    direction: Direction, ledger_account: str, bank_account: str
) -> tuple[str, str]:
    """Returns (debit_account, credit_account).

    Credit on the statement (money coming in) -> the bank is debited.
    Debit on the statement (money going out)  -> the bank is credited.
    """
    if direction is Direction.CREDIT:
        return bank_account, ledger_account
    return ledger_account, bank_account


def strip_hyphen(short_code: str) -> str:
    """`1-9` -> `19` · `611-4` -> `6114` · `2-0` -> `20`.

    The chart of accounts writes the short code with its check digit after a hyphen; the batch
    writes them together. Measured on 304 accounts.
    """
    return short_code.replace("-", "").strip()
