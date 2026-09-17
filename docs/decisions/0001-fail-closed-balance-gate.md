# 1. A statement that doesn't balance to the cent is refused

**Status:** accepted · in production

## Context

A bank statement carries its own checksum: the opening balance plus credits minus debits must
equal the closing balance. Every silent error in reading a PDF (a line lost at a page break, a
`D` read as `C`, a row read twice, an OCR `8` read as `3`) breaks that equation. A wrong entry that
reaches the accounting system costs far more than re-processing a file: the trial balance comes out
wrong and nobody knows which line caused it.

## Decision

The pipeline has a single gate, and the tolerance is **exactly `0.00`**.

```text
opening balance + Σ credits − Σ debits == closing balance
```

- No "accept anyway" path, no rounding, no retry-until-it-passes.
- A refused statement produces **no entries at all**, not a partial list.
- The refusal is a value, not an exception: it carries a diagnostic code and the numbers, so the UI
  can explain it.

Where a bank prints a balance for every day, a second, finer check closes each day on its own and
points to the day that doesn't close. That check **warns** instead of refusing: the corpus that
backs it is still small, and refusing a correct statement is worse than a warning.

## Alternatives rejected

- **A small tolerance (a few cents).** Accepts exactly the one-digit OCR errors the gate exists to
  catch.
- **Accept, and flag for review.** Flags get ignored under deadline; the wrong entry is already in.
- **Deduce the missing value from the difference.** It closes by construction, including when it
  is wrong (see [ADR 3](0003-ocr-corrections-verified-not-deduced.md)).

## Consequences

- OCR becomes *safe to use*: a misread number can only produce a refusal, never a wrong entry.
- Some statements need a human (for example, a layout that doesn't print an opening balance: the
  operator types it, and the typed value goes through the same gate).
- On the private corpus of 67 real statements, 59 are accepted and none of the 8 refusals is a
  misread.

## Code

- [`statement/closing_gate.py`](../../code-samples/statement/closing_gate.py)
- [`statement/daily_balance_check.py`](../../code-samples/statement/daily_balance_check.py)
