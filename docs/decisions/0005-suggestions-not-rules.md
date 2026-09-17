# 5. Classification suggestions, not silent rules

**Status:** accepted · in production (a versioned rules engine is planned)

## Context

Every entry needs a category and a ledger account. Most months repeat: the same supplier, the same
payroll, the same bank fees. But the bank's description alone is ambiguous. `PIX EMITIDO OUTRA IF`
reads the same for a supplier payment, a partner's withdrawal and a service, and those go to
different accounts.

## Decision

- The workbench **suggests** the category the accountant already used for a similar case. It is
  pre-selected, and nothing is saved without a click.
- **The key depends on the direction of the money.**
  - For a **payment**, the key is `(description, counterparty)`: who received the money is the
    identity.
  - For a **receipt**, the money lands in the same account whoever paid, so the key is the
    description alone, with the payer's name removed. The name is removed by **prefix** match,
    because banks truncate names to fit.
- When the name is glued into the description with no delimiter, the transaction *type* is
  discovered from the statement itself: types repeat, names branch out. A prefix that continues in
  a few ways is a type; one that continues in dozens is where the name starts.
- After the second statement, repeated cases are applied automatically **only at confidence 1.0**.
  A case that ever received two different categories goes back to the person. Transfer legs are
  excluded, and an entry that is already classified is **never overwritten**.
- Fixing a single entry ("only this one") marks it as an exception, so it doesn't lower the
  confidence of the other 70 entries of the same case.

## Alternatives rejected

- **Majority vote.** It hides a real ambiguity behind a confident-looking answer.
- **Keying receipts by payer.** Measured: 87 receipts became 77 decisions instead of 1.
- **Shortening the fingerprint to N words.** `PIX EMITIDO OUTRA IF` and
  `PIX EMITIDO OUTRA IF - MESMA TIT.` would collapse, and the second one is a transfer between the
  company's own accounts.

## Consequences

- Measured on seven real statements: 3,255 entries needed 380 decisions (receipts: 2,031 → 150;
  payments: 1,224 → 230).
- On one digital-wallet statement: 1,475 receipts, 304 distinct descriptions, and 52 decisions
  reduced to 13, with no new disagreement with the accountant's past choices.

## Code

- [`workbench/suggestions.py`](../../code-samples/workbench/suggestions.py)
