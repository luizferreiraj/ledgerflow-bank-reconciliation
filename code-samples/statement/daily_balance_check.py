"""Proof by DAY, where the statement declares a balance for each day.

The overall closing compares the two ends: opening balance plus transactions against the closing
balance. It proves the sum is right, but says nothing about where it went wrong. On a statement
with more than a thousand entries, "R$ 312,45 is missing" helps nobody.

Sicoob prints `SALDO DO DIA` (daily balance) for every day. That is 21 extra anchors in a June
file, and each one closes a short stretch: yesterday's balance plus today's transactions must
equal today's balance. When a day doesn't close, the search drops from a thousand lines to a few
dozen.

## Why this does NOT refuse a file (yet)

Because the check is new and the corpus is small: six Sicoob files. It closes on all of them,
but a future layout could print `SALDO DO DIA` with different semantics, and refusing a correct
statement is worse than letting a warning through. Once there is enough corpus to trust it,
promoting this to a refusal means changing the diagnostic's type.

## A theory the facts disproved, kept on record

On the first measurement, ten days of `sicoob-ib-2026-06.pdf` and five of
`sicoob-sisbr-2026-06-a.pdf` didn't close, and the breaks lined up with `DEP.CHEQUE BLOQ.1D`
lines (a cheque deposit with a hold period). The conclusion looked obvious: the deposit appears
in today's list but only enters the balance later, so the day can't close.

It was wrong. The statement already handles that, and says how::

    19/05  DEP CH.CANAL ATEND.1D             R$ 3.200,00*   <- `*` marker
    20/05  LIBERACAO DE DEPOSITO BLOQUEADO   R$ 3.200,00C   <- the credit (hold released)

The `*` is the NON-POSTING convention, which the pipeline already excludes. What counted those
lines as transactions was my measurement script, not the system. The correlation was real; the
cause was something else.

With the classifier fixed (see `line_classification`, the OCR noise before the `SALDO` label),
all six files close day by day WITHOUT a single exception. That is why there is no
"undetermined day" rule here: it would never fire, and on the day it did fire it would be hiding
a real error on a day that has a held deposit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .money import ZERO
from .models import Entry, Direction
from .layout_router import normalize_for_signature


@dataclass(frozen=True, slots=True)
class Day:
    posting_date: date
    running: object
    declared: object
    # The day's entries, so the UI can show WHICH lines add up wrong. Without them the operator
    # knows the day and still has to hunt for the line.
    entries: tuple = ()

    @property
    def difference(self):
        return self.running - self.declared

    @property
    def closes(self) -> bool:
        return self.running == self.declared


def _signed(entry: Entry):
    return -entry.amount if entry.direction is Direction.DEBIT else entry.amount


def check(analysis, layout, entries: list[Entry]) -> list[Day]:
    """One `Day` per `SALDO DO DIA` anchor, in chronological order.

    Transactions come from `entries`, already classified by the pipeline, so balance lines and
    non-posting lines are already gone. One of those fooled me when measuring by hand:
    `SALDO BLOQ.ANTERIOR 3.872,15` (previous held balance) added as a transaction shifted the
    first day by exactly that amount.
    """
    from .layouts.base import line_date

    daily_balance_label = normalize_for_signature(
        layout.markers.get("daily_balance", "SALDO DO DIA")
    ).upper()
    if not daily_balance_label:
        return []

    from .signs import normalize_amount

    anchors: dict[date, object] = {}
    for line in analysis.lines:
        if daily_balance_label not in normalize_for_signature(line.description).upper():
            continue
        try:
            amount = normalize_amount(line.raw_amount, line.marker, allowed=layout.conventions)
        except Exception:
            # One unreadable daily balance must not bring down the whole check: that day simply
            # has no anchor.
            continue
        anchors[line_date(line, analysis.header)] = amount.signed
    if not anchors:
        return []

    transactions: dict[date, list[Entry]] = {}
    for entry in entries:
        transactions.setdefault(entry.posting_date, []).append(entry)

    day_checks: list[Day] = []
    running = analysis.opening_balance.amount
    for posting_date in sorted(set(anchors) | set(transactions)):
        day_entries = transactions.get(posting_date, ())
        running = running + sum((_signed(l) for l in day_entries), ZERO)
        if posting_date not in anchors:
            continue
        declared = anchors[posting_date]
        day_checks.append(
            Day(
                posting_date=posting_date,
                running=running,
                declared=declared,
                entries=tuple(day_entries),
            )
        )
        # Continue from what the statement DECLARES, not from what the sum gave: that way each
        # day is an independent test, and one error doesn't carry the same difference into the
        # following days.
        running = declared
    return day_checks


def failing_days(day_checks: list[Day]) -> list[Day]:
    """The days that don't close."""
    return [d for d in day_checks if not d.closes]


def summary(day_checks: list[Day], limit: int = 5) -> str:
    """One line per day that doesn't close, to go into the diagnostic."""
    failing = failing_days(day_checks)
    if not failing:
        return ""
    pieces = [
        f"{d.posting_date:%d/%m}: the day's entries add up to {d.running}, the statement "
        f"declares {d.declared} (difference {d.difference})"
        for d in failing[:limit]
    ]
    if len(failing) > limit:
        pieces.append(f"... and {len(failing) - limit} more day(s)")
    return " | ".join(pieces)


def pages_of_day(day: Day) -> list[int]:
    """The PDF pages the day appears on: where to check on paper.

    Measured on the scanned May statement: the three days that didn't close fell on three pages
    (14, 37 and 40) out of 41. That narrowing is what makes a visual check feasible: instead of
    rereading the document, you look at three sheets.
    """
    return sorted({l.page for l in day.entries})


def _digits(amount) -> str:
    return f"{abs(amount):.2f}".replace(".", "")


def _one_edit_away(as_read: str, target: str) -> bool:
    """Does `as_read` become `target` with ONE digit inserted, removed or replaced?

    That is the shape of the OCR errors measured in this project, and the three in the scanned
    May statement are exactly that::

        3772,46 -> 372,46    one extra digit
        6174,52 -> 614,52    one extra digit
         238,74 -> 238,34    one digit replaced
    """
    if as_read == target:
        return False
    if len(as_read) == len(target):
        return sum(1 for a, b in zip(as_read, target) if a != b) == 1
    if abs(len(as_read) - len(target)) != 1:
        return False
    shorter, longer = (as_read, target) if len(as_read) < len(target) else (target, as_read)
    return any(longer[:i] + longer[i + 1 :] == shorter for i in range(len(longer)))


def closing_target(day: Day, entry):
    """The amount THIS entry would need for the day to close.

    The UI uses it to pre-fill the field, always as a suggestion to check against the paper,
    never as a value applied on its own. See `top_suspects`.
    """
    return _signed(entry) - day.difference


def top_suspects(day: Day, how_many: int = 5) -> list:
    """The day's entries that MOST LOOK like they were misread.

    It doesn't accuse and doesn't correct; it orders. The person looking at the page still
    decides, and the arithmetic is still the judge.

    ## The criterion, and why the previous one failed

    The first version ordered by how close the entry's amount was to the day's difference. That
    works when OCR INFLATES a number: `372,46` became `3.772,46`, the day was off by `3.400,00`,
    so the culprit was the largest entry. It failed badly on a day off by `-0,40`: it surfaced
    the R$ 2,00 and R$ 15,00 entries and left out the culprit, a `238,74D` that should have been
    `238,34D`.

    The right criterion is not the SIZE of the error but its SHAPE. For each entry you can
    compute the amount it WOULD NEED for the day to close::

        target = amount_as_read - day_difference

    If that target is one digit edit away from what was read, that entry explains the whole day
    with the kind of error OCR makes. For all three errors in the scanned May statement, the
    culprit comes out first.

    This is NOT deducing the amount: the target only sets the ORDER of the list, and is never
    stored as a number. The person reading the page is still the one who supplies the value,
    and if they supply something else, the arithmetic judges it.
    """
    ordered = []
    for entry in day.entries:
        signed = _signed(entry)
        target = closing_target(day, entry)
        plausible = _one_edit_away(_digits(signed), _digits(target))
        # Lowest confidence first. When the day's difference is small, almost any amount turns
        # into another with one cents digit replaced, and the shape criterion narrows nothing:
        # on the `-0,40` day, 41 of 54 candidates were left. That is where the OCR engine's own
        # doubt decides: the culprit had confidence 24 on a page averaging 70.3.
        doubt = 999.0 if entry.confidence is None else entry.confidence
        ordered.append((0 if plausible else 1, doubt, entry))
    ordered.sort(key=lambda item: (item[0], item[1]))
    return [entry for _, _, entry in ordered[:how_many]]
