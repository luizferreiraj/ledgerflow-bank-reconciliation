# 4. Deduplication is a database constraint

**Status:** accepted · in production

## Context

Statements overlap. A client sends June, then a file covering May to July; an operator re-downloads
the same month; a parser fix means re-importing files that were already imported. Importing an
entry twice inflates revenue or expenses, and once a duplicated batch reaches the accounting system
the damage is hard to undo.

Real statements also contain **legitimately identical** entries: one month had 71 identical
collection credits on the same day.

## Decision

- Each entry has an **identity**: account, accrual period, date, amount, document number,
  description, plus an **ordinal** among entries with that same base.
- The ordinal is counted **after a canonical sort** (date, then position in the file). Some banks
  list entries newest-first. Counting by raw position would give the same statement, exported in
  the other order, entirely different identities, and a re-import would duplicate 100% of it.
- The identity is a `UNIQUE` constraint in SQLite. Deduplication is not a function someone has to
  remember to call.
- Re-importing after a parser improvement matches entries by identity **without the description**,
  so a corrected description updates the existing entry in place and keeps its classification,
  instead of looking like a new entry.

## Alternatives rejected

- **Hash of the whole file.** It catches only byte-identical re-uploads; a re-download with a new
  timestamp gets through.
- **Identity without the ordinal.** It would erase 70 of the 71 legitimate identical credits.
- **Dedup in application code only.** One forgotten call path, and duplicates are back.

## Consequences

- Re-importing a statement, even re-downloaded with different bytes, creates zero entries. The
  public test suite proves it on a synthetic statement.
- A statement imported into the *wrong* bank account is not moved silently: the system says so
  instead of claiming success.

## Code

- [`statement/entry_identity.py`](../../code-samples/statement/entry_identity.py)
- [`workbench/schema.sql`](../../code-samples/workbench/schema.sql)
- [`tests/test_end_to_end.py`](../../code-samples/tests/test_end_to_end.py)
