# 2. Layouts are data, routed by a textual signature

**Status:** accepted · 20 layouts from 13 banks in production

## Context

The firm receives statements from 13 banks, and several banks have more than one layout. The same
bank ships PDFs made by different producers (iOS Quartz, iText, Chrome/Skia), and one producer
renders layouts from different banks. The PDF `Producer` field therefore says nothing about how to
read the file.

Column positions, line spacing and where the sign sits vary per layout, and every one of those
numbers has to be right, or entries get misread.

## Decision

- A statement is routed by its **textual signature**: phrases that must appear (column headers,
  fixed titles) and phrases that must not. It is never routed by producer or by file name.
- Each layout's signature, column boundaries, tolerances and markers (`OPENING BALANCE`,
  `DAILY BALANCE`) live in a YAML registry, **not in code**. Every geometric constant was measured
  on real files, and the YAML says which ones.
- Chronological order is never declared: two files of the same layout arrive in opposite orders,
  so balance anchors are chosen by date, not by position.
- A file that matches no layout is refused **with the signature it observed**, which is exactly the
  input needed to register a new layout.

## Alternatives rejected

- **One `if bank == ...` branch per bank.** It breaks on the second layout from the same bank, and
  the magic numbers end up scattered through code.
- **Detect by producer or by file name.** Measured to be wrong in both directions.

## Consequences

- Adding a layout is mostly data plus a small parser class; the shared column parser, sign
  conventions and gate are reused.
- Sign handling is declarative as well: eight conventions (`1.250,00D`, `35,10 C`, `-R$ 89,90`,
  `89,90-`, separate credit/debit columns, and others), and the tests for them are generated from
  the YAML.

## Code

- [`statement/layout_router.py`](../../code-samples/statement/layout_router.py)
- [`statement/layout_signature_example.yaml`](../../code-samples/statement/layout_signature_example.yaml)
- [`statement/signs.py`](../../code-samples/statement/signs.py) ·
  [`signs.yaml`](../../code-samples/statement/signs.yaml)
