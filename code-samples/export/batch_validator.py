"""The exporter's gate: it fails closed on the WHOLE batch (see ADR 8).

A partial batch imported into the accounting system is worse than no batch at all: the trial
balance comes out wrong and nobody knows something is missing. So a single incomplete entry refuses
the whole batch, and not a byte is written.
"""

from __future__ import annotations

from .description import compose, representable
from .models import BatchDiagnostic, ClassifiedEntry, BatchRequest, Source

MAX_DIAGNOSTICS = 20


def period_of(day) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _where(l: ClassifiedEntry) -> str:
    return f"{l.posting_date.isoformat()} {l.batch_description[:24]!r} {l.amount}"


def validate(
    batch_request: BatchRequest, known_accounts: set[str] | None = None
) -> list[BatchDiagnostic]:
    problems: list[BatchDiagnostic] = []

    if not batch_request.entries:
        return [BatchDiagnostic(code="EMPTY_BATCH", message="no entries in the request")]

    for l in batch_request.entries:
        where = _where(l)
        if l.source is not Source.STATEMENT:
            problems.append(
                BatchDiagnostic(
                    code="NON_BANK_SOURCE",
                    message=f"entry from source {l.source.value} stays out of the batch: {where}",
                )
            )
            continue
        if not l.ledger_account:
            problems.append(
                BatchDiagnostic(
                    code="MISSING_ACCOUNT",
                    message=f"entry without a ledger account: {where}",
                )
            )
        elif known_accounts is not None and l.ledger_account not in known_accounts:
            problems.append(
                BatchDiagnostic(
                    code="UNKNOWN_ACCOUNT",
                    message=f"account {l.ledger_account} is not in the chart of accounts: {where}",
                )
            )
        if not l.bank_account:
            problems.append(
                BatchDiagnostic(
                    code="MISSING_BANK_ACCOUNT",
                    message=f"entry without a bank account: {where}",
                )
            )
        if period_of(l.posting_date) != batch_request.period:
            problems.append(
                BatchDiagnostic(
                    code="OUTSIDE_PERIOD",
                    message=(
                        f"entry from {period_of(l.posting_date)} in a batch for "
                        f"{batch_request.period}: {where}"
                    ),
                )
            )
        string = compose(l.batch_description, l.category)
        if not representable(string):
            problems.append(
                BatchDiagnostic(
                    code="UNREPRESENTABLE_CHARACTER",
                    message=f"description {string!r} cannot be encoded in latin-1: {where}",
                )
            )

    if len(problems) > MAX_DIAGNOSTICS:
        remaining = len(problems) - MAX_DIAGNOSTICS
        problems = problems[:MAX_DIAGNOSTICS] + [
            BatchDiagnostic(
                code="DIAGNOSTICS_TRUNCATED",
                message=f"and {remaining} more problem(s) of the same kind",
            )
        ]
    return problems


def split_exportable(
    batch_request: BatchRequest,
) -> tuple[list[ClassifiedEntry], list[ClassifiedEntry]]:
    """Separates what goes into the batch from what stays out, without refusing anything.

    Used when the caller already knows the set holds entries from another period, or entered by
    hand, and wants to filter them out before validating.
    """
    included, excluded = [], []
    for l in batch_request.entries:
        if l.source is Source.STATEMENT and period_of(l.posting_date) == batch_request.period:
            included.append(l)
        else:
            excluded.append(l)
    return included, excluded
