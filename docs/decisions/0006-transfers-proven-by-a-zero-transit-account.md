# 6. Internal transfers are proven by a transit account that nets to zero

**Status:** accepted · in production

## Context

Moving money between two accounts of the same company produces two entries in two different
statements: money out of one, money in to the other. Classified as expense and revenue, they
inflate both sides of the income statement, although the money never left the company.

The export batch carries one record per statement entry, with that entry's bank on one side. If
each leg pointed directly at the other bank, the transfer would be booked twice.

## Decision

- Both legs go to the same **transit account**:

  ```text
  out of bank A  →  D transit / C bank A
  into bank B    →  D bank B  / C transit
  net effect     →  D bank B  / C bank A   and the transit account nets to 0.00
  ```

- **Netting to zero is the proof**: a transit account that doesn't close in the period means a leg
  has no pair. It is the same idea as the statement gate (ADR 1).
- Pairs are only searched **within one company**, between different accounts. Each candidate shows
  its corroborating signals (a transfer keyword, the company's own tax ID as counterparty, its name
  in the text) and a confidence, and **a person confirms**.
- A transfer leg is excluded from bulk "apply to similar entries". Otherwise it would be classified
  as ordinary revenue, and the pair would silently disappear from the pairing panel.
- The proof filters by **category**, not by ledger account code. If the transit account is shared
  with another category, summing by code would mix revenue into the check.

## Alternatives rejected

- **Pair automatically by amount and date.** Measured on real files: amount and date alone matched
  **20 times between two unrelated companies**. Statements with hundreds of entries collide a lot.
- **Book each leg against the other bank.** The transfer would be recorded twice.

## Consequences

- Revenue and expenses are no longer inflated by internal movements.
- An orphan leg shows up as a non-zero transit balance, visible before the batch is exported.

## Code

- [`workbench/transfer_pairing.py`](../../code-samples/workbench/transfer_pairing.py)
