# 8. The export batch is all-or-nothing, and exporting twice needs confirmation

**Status:** accepted · the generated batch was imported and accepted by the accounting system

## Context

The accounting system imports a fixed-width text file: `latin-1`, `CRLF`, header/entry/trailer
records, positional fields. Two ways it goes wrong are especially costly:

- **A partial batch.** The trial balance closes wrong and nobody knows something is missing.
- **The same period imported twice.** Duplicated entries at the destination, which this system
  can't reach to undo.

## Decision

- **The whole batch is validated before a single byte is written.** One entry without a ledger
  account refuses the batch, and the refusal lists every problem.
- **The writer is driven by a layout table** (field, offset, width, padding, type). The table was
  measured against a real batch that the firm had already imported, and the writer regenerates that
  file byte for byte: 466 records, 249,046 identical bytes.
- **The bank side of each entry is derived from its sign**, never typed: a credit in the statement
  debits the bank, a debit credits it. Measured: 466 of 466 records follow this rule, with no
  contradiction.
- **Every generated batch goes to an append-only registry.** Generating the same period again is
  refused unless the operator explicitly confirms, even when the content is identical. The
  registry answers "this period was exported on X; N entries changed since then".

## Alternatives rejected

- **Export what's ready and skip the rest.** That produces exactly the silent partial batch this
  decision is meant to prevent.
- **Block re-export completely.** Corrections are real; the point is that they are deliberate.

## Consequences

- The batch is a one-way street, and the UI says so. When a period was already exported, a
  correction shows a notice that the same change must be made in the accounting system.

## Code

- [`export/batch_validator.py`](../../code-samples/export/batch_validator.py)
- [`export/layout.py`](../../code-samples/export/layout.py)
- [`export/double_entry.py`](../../code-samples/export/double_entry.py)
- [`export/record.py`](../../code-samples/export/record.py)
