# Code samples

Selected modules from the production codebase, translated from Portuguese into English. They are
here to be **read**, not run: imports point to modules that aren't published, such as the 20
layout parsers, the full HTTP API, the web UI and the repository layer.

**The translation is faithful to the code in production.** Every Python file was checked by a
script: after renaming identifiers through a single glossary and ignoring the text inside strings
and docstrings, its syntax tree matches the production module. One dead assignment was removed.
Comments and docstrings were rewritten in English with the same reasoning and measurements.

Files marked *excerpt* contain only part of a module. In those, `...` marks omitted code, and every
function that is shown is complete.

Text printed on Brazilian bank statements stays as printed, because the parsers match it:
`SALDO DO DIA` (daily balance), `PIX RECEB.OUTRA IF` (PIX received from another bank),
`1.250,00D` (a debit, Brazilian number format).

## Where to start

If you have ten minutes, read these in order:

1. [`statement/closing_gate.py`](statement/closing_gate.py): the fail-closed balance gate,
   53 lines.
2. [`statement/pipeline.py`](statement/pipeline.py): `_proven_by_reread`, the OCR reread loop
   that only accepts what makes the statement close.
3. [`tests/test_synthetic_statement.py`](tests/test_synthetic_statement.py): the pipeline run on a
   PDF written byte by byte, then damaged on purpose.
4. [`workbench/app.py`](workbench/app.py): authentication as a path-keyed middleware.

## `statement/`: from PDF to proven entries

| File | What to look at | Read first |
|---|---|---|
| [`models.py`](statement/models.py) | The output contract: money is a `Decimal` serialized as a string, a refusal can never carry entries, and every number records who supplied it (bank, person, reread) | `IngestionResult`, `RawLine`, `Suspect` |
| [`money.py`](statement/money.py) | The only place text becomes money; no code path accepts or produces a float | `to_decimal`, `sum_amounts` |
| [`closing_gate.py`](statement/closing_gate.py) | The balance equation with a tolerance of exactly 0.00, and a diagnostic that carries the numbers instead of raising | `compute`, `diagnose` |
| [`signs.py`](statement/signs.py) · [`signs.yaml`](statement/signs.yaml) | Eight sign conventions declared as data; the sign is never assumed, and the YAML examples generate the tests | `normalize_amount`, `load_conventions` |
| [`layout_router.py`](statement/layout_router.py) · [`layout_signature_example.yaml`](statement/layout_signature_example.yaml) | Routing by required and forbidden phrases, never by PDF producer; an unknown file is refused with its observed signature. The YAML is one of the 20 registered layouts | `route`, `observed_signature` |
| [`daily_balance_check.py`](statement/daily_balance_check.py) | Each day is closed against the printed daily balance, and suspects are ranked by the shape of an OCR error, not its size | `check`, `top_suspects`, `_one_edit_away` |
| [`cell_reread.py`](statement/cell_reread.py) | One cell reread at 600 dpi with a digits-only alphabet in three Tesseract modes; it returns candidates and doesn't choose | `candidates` |
| [`entry_identity.py`](statement/entry_identity.py) | Dedup identity with an ordinal counted after a canonical sort, so a statement exported in reverse order gets the same keys | `with_ordinals`, `new_entries` |
| [`pipeline.py`](statement/pipeline.py) *(excerpt)* | The reread loop: arithmetic flags the cells, the reread supplies candidates, and a candidate is kept only if the statement then closes | `_proven_by_reread`, `_combination_that_closes` |

## `workbench/`: persistence, classification, security

| File | What to look at | Read first |
|---|---|---|
| [`auth_gate.py`](workbench/auth_gate.py) | Fail-closed login against Supabase: no JWT secret, team membership checked with the user's own token, a cache keyed by SHA-256 with a size cap and real expiry | `AuthGate.identify`, `AuthGate._is_team_member` |
| [`app.py`](workbench/app.py) *(excerpt)* | Authentication as a path-keyed middleware, so a new route is protected by default; requests arriving through a proxy are refused while login is off | `require_login`, `refuse_if_published` |
| [`__main__.py`](workbench/__main__.py) *(excerpt)* | The server refuses to start on a network address without login, and the comments explain why the other guards can't cover that case | `_is_loopback`, `main` |
| [`schema.sql`](workbench/schema.sql) *(excerpt)* | Money as exact decimal text, `UNIQUE (bank_account_id, identity)` as the dedup mechanism, the corrected-reading trail and the audit table | `entry`, `corrected_reading`, `audit_trail` |
| [`suggestions.py`](workbench/suggestions.py) *(excerpt)* | The suggestion key depends on the direction of the money, payee names are stripped by prefix, and transaction types are discovered by counting how prefixes branch | `lookup_key`, `operation_kinds`, `useful_counterparty` |
| [`transfer_pairing.py`](workbench/transfer_pairing.py) | Pairs searched only within one company, evidence required, one-to-one matching by confidence, and the transit-account proof filtered by category | `candidates`, `check_transit_account` |
| [`import_queue.py`](workbench/import_queue.py) | A single-worker import queue sized to the real CPU limit, with tickets and queue position, and a documented decision not to survive restarts | `ImportQueue.accept`, `ImportQueue._work` |

## `export/`: the batch file the accounting system imports

| File | What to look at | Read first |
|---|---|---|
| [`batch_validator.py`](export/batch_validator.py) | Fails closed on the whole batch; every problem is collected, so one refusal lists all of them | `validate` |
| [`layout.py`](export/layout.py) | A fixed-width writer driven by a table; the table is checked for overlapping fields at load time, and a value that doesn't fit is an error, never silently truncated | `format_field`, `assemble` |
| [`double_entry.py`](export/double_entry.py) | The bank side of each entry is derived from the direction of the money, never chosen by a person | `derive` |
| [`record.py`](export/record.py) | An append-only export log; re-exporting a period needs confirmation even when the content is identical | `check`, `last_export` |

## `tests/`: how the guarantees are proven without real data

| File | What to look at | Read first |
|---|---|---|
| [`synthetic_statement.py`](tests/synthetic_statement.py) | A bank statement written as a real PDF, byte by byte and with no dependencies, that the production parser accepts; the table is computed separately from the drawing, so a test can damage the paper | `statement_lines`, `write_pdf` |
| [`test_synthetic_statement.py`](tests/test_synthetic_statement.py) | A lost line, a flipped sign and a wrong closing balance are each refused, and the failing day is named | `test_lost_line_refuses_the_statement_and_names_the_day` |
| [`test_end_to_end.py`](tests/test_end_to_end.py) *(excerpt)* | From PDF to batch through the real APIs: dedup when a statement is re-downloaded with different bytes, a corrected reading updated in place, one unclassified entry refusing the batch, re-export needing confirmation | `test_same_statement_downloaded_again_does_not_duplicate` |
