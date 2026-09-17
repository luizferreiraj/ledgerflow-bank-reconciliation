"""Record of what was exported: the audit trail for the output side (see ADR 8).

Append-only JSONL: auditable by nature, and no history is ever lost. If the record moves into a
database, the migration reads this file.

The purpose is NOT to rebuild the batch. Corrections are made directly in the accounting system and
then brought back into the workbench; the batch is a one-way street. The record exists to answer
"this period was already exported, and what has changed since then".
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import BatchDiagnostic, RecordEntry

DEFAULT_PATH = Path("exported_batches.jsonl")


def read_file(pathname: Path = DEFAULT_PATH) -> list[RecordEntry]:
    if not pathname.exists():
        return []
    entries = []
    for line in pathname.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(RecordEntry.model_validate_json(line))
    return entries


def register(entry: RecordEntry, pathname: Path = DEFAULT_PATH) -> None:
    pathname.parent.mkdir(parents=True, exist_ok=True)
    with open(pathname, "a", encoding="utf-8") as file:
        file.write(entry.model_dump_json() + "\n")


def last_export(
    cnpj: str, period: str, pathname: Path = DEFAULT_PATH
) -> RecordEntry | None:
    matching = [
        e for e in read_file(pathname) if e.cnpj == cnpj and e.period == period
    ]
    return matching[-1] if matching else None


def check(
    cnpj: str,
    period: str,
    content_digest: str,
    entry_count: int,
    confirmed: bool,
    pathname: Path = DEFAULT_PATH,
) -> BatchDiagnostic | None:
    """Generating the same period a second time needs EXPLICIT confirmation.

    Confirmation is required even when the hash is identical: importing the file twice duplicates
    the entries at the destination, and identical content changes nothing about that.
    """
    previous = last_export(cnpj, period, pathname)
    if previous is None or confirmed:
        return None
    changed = previous.content_hash != content_digest
    difference = entry_count - previous.entry_count
    return BatchDiagnostic(
        code="PERIOD_ALREADY_EXPORTED",
        message=(
            f"{period} was already exported on "
            f"{previous.when:%d/%m/%Y %H:%M} with {previous.entry_count} entries. "
            + (
                f"The content HAS CHANGED since then ({difference:+d} entries)."
                if changed
                else "The content is identical to what was exported."
            )
            + " Confirm explicitly to generate it again."
        ),
        details={
            "exported_at": previous.when.isoformat(),
            "previous_hash": previous.content_hash,
            "current_hash": content_digest,
            "previous_count": str(previous.entry_count),
            "current_count": str(entry_count),
            "content_changed": str(changed),
        },
    )


def new_record_entry(
    company: str,
    cnpj: str,
    period: str,
    content_digest: str,
    entry_count: int,
    when: datetime,
    regeneration: bool,
) -> RecordEntry:
    return RecordEntry(
        company=company,
        cnpj=cnpj,
        period=period,
        content_hash=content_digest,
        entry_count=entry_count,
        when=when,
        regeneration=regeneration,
    )
