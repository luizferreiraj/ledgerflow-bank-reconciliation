# Architecture decision records

The decisions that shape the system. Each one records the context, the choice, the alternatives
that were rejected (and why), the consequences, and a link to the code.

Most of these decisions were **measured, not assumed**. When a number appears in a record, it came
from running the system against real bank statements. Those statements are private, so only the
measurements are published.

| # | Decision | In one line |
|---|---|---|
| 1 | [A statement that doesn't balance to the cent is refused](0001-fail-closed-balance-gate.md) | Tolerance is exactly `0.00`; a refusal stores nothing |
| 2 | [Layouts are data, routed by a textual signature](0002-layouts-are-data-routed-by-signature.md) | 20 layouts in YAML; never route by PDF producer |
| 3 | [OCR corrections are verified by arithmetic, never deduced](0003-ocr-corrections-verified-not-deduced.md) | Arithmetic says where, a reread says what, arithmetic checks it |
| 4 | [Deduplication is a database constraint](0004-deduplication-is-a-database-constraint.md) | Canonical identity plus ordinal, enforced by `UNIQUE` |
| 5 | [Classification suggestions, not silent rules](0005-suggestions-not-rules.md) | The key depends on the direction of the money; auto-apply only at confidence 1.0 |
| 6 | [Internal transfers are proven by a transit account that nets to zero](0006-transfers-proven-by-a-zero-transit-account.md) | Pairs are searched within one company and confirmed by a person |
| 7 | [Secure by default for a small team](0007-secure-by-default-for-a-small-team.md) | Path-keyed auth middleware, fail closed, refuse to start exposed |
| 8 | [The export batch is all-or-nothing](0008-export-batch-all-or-nothing.md) | Validate before writing a byte; re-export needs confirmation |
