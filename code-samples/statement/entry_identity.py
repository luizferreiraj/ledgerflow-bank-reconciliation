"""Deduplication as a pure function over two sets (see ADR 4).

The ordinal is counted AFTER a canonical sort. Itaú and Sicoob IB list entries newest-first;
an ordinal by raw position would give the same statement, exported in the two orders, different
identities, and re-importing it would create 100% duplicates: exactly the scenario idempotency
exists to cover.

Ignoring the ordinal doesn't work either: it would erase 70 of the month's 71 legitimately
identical CRED.LIQ.COBRANCA (collection settlement) credits.
"""

from __future__ import annotations

from collections import Counter

from .models import Entry

Key = tuple


def identity_base(entry: Entry) -> tuple:
    return (
        entry.account,
        entry.period,
        entry.posting_date.isoformat(),
        str(entry.amount),
        entry.document,
        entry.description,
    )


def with_ordinals(entries: list[Entry]) -> list[tuple[Entry, Key]]:
    ordered = sorted(
        enumerate(entries), key=lambda pair: (pair[1].posting_date, pair[1].file_order, pair[0])
    )
    seen: Counter = Counter()
    result: list[tuple[Entry, Key]] = []
    for _, entry in ordered:
        base = identity_base(entry)
        seen[base] += 1
        result.append((entry, base + (seen[base],)))
    return result


def keys(entries: list[Entry]) -> set[Key]:
    return {lookup_key for _, lookup_key in with_ordinals(entries)}


def new_entries(already_imported: list[Entry], candidates: list[Entry]) -> list[Entry]:
    existing = keys(already_imported)
    return [
        entry
        for entry, lookup_key in with_ordinals(candidates)
        if lookup_key not in existing
    ]
