# 3. OCR corrections are verified by arithmetic, never deduced

**Status:** accepted · in production

## Context

About a quarter of the statements arrive scanned, with no text layer. Tesseract reads a 41-page
table well overall, but a handful of cells come out wrong in one of three ways:

| failure | example | who notices |
|---|---|---|
| unreadable | `R$ 236 ,S0C` | the parser, immediately |
| well-formed but wrong | `372,46` read as `3.772,46` | the daily balance check, which points to the day |
| lost sign | `528,34` without its `C` | the column pairing |

The difference between the declared and computed balance is known. It is tempting to "fix" the
cell with it.

## Decision

The arithmetic says **where** to look; a targeted reread says **what** is there; the arithmetic
**checks** what was read. None of the three decides alone.

1. Only the cells the arithmetic flagged are reread: the unreadable ones and the entries of the
   days that don't close.
2. Each cell is cropped from the PDF, rendered at 600 dpi and read with a digits-only alphabet, in
   three Tesseract page-segmentation modes. Measured: no single mode reads every cell right, and
   every cell is read right by at least one mode. So the modes return **candidates**, not answers.
3. A candidate is accepted **only if it makes that day close**. If no combination does, nothing is
   accepted and the question goes to a person, with the cell's address and what was read.
4. Every number records who supplied it: `bank`, `person` or `reread`.

The loop repeats up to four times, because each answer can reveal the next problem.

## Alternatives rejected

- **Deduce the value from the difference.** It closes by construction, even when wrong. Measured
  on one page: deduction gave R$ 528,74 where the paper says R$ 528,34.
- **Reread every cell at high resolution.** One 600-dpi render and three passes per cell, times
  more than a thousand rows, for cells that were already right.
- **Pick the "most likely" candidate.** The modes disagree cell by cell; picking one is a guess.

## Consequences

- On a 42-page scanned statement, the system resolved every defect by itself (a lost sign, two
  unreadable cells, a day off by thousands of reais) and accepted the file with a 0.00 difference, with
  nobody typing anything.
- On another statement, the firm had found seven defects by hand, page by page. The reread found
  exactly those seven.
- When a person does type a value, it goes through the same gate: a wrong value still doesn't
  close.

## Code

- [`statement/cell_reread.py`](../../code-samples/statement/cell_reread.py)
- [`statement/pipeline.py`](../../code-samples/statement/pipeline.py)
