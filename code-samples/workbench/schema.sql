-- Excerpt of the workbench's SQLite schema with the statement, entry, corrected_reading and
-- audit_trail tables; the company, ledger_account, bank_account, category, setting,
-- monthly_closing and dismissed_transfer tables, the indexes, and the two columns a later
-- in-code migration adds to entry (`source`, `supplied_amount`) are left out.
--
-- SQLite in a file, not a database server: the workbench is an office tool used by one operator
-- at a time. The schema runs with `PRAGMA foreign_keys = ON`.
--
-- Two structural rules live in the schema itself, not only in the code:
--
-- 1. Money is never stored as `REAL`. It is kept as exact decimal TEXT, for the same reason as in
--    the statement parser: with `float`, "the statement closes at 0.00" becomes fiction.
-- 2. `identity` is UNIQUE per bank account. It is the canonical deduplication key (see ADR 4):
--    re-importing the same statement doesn't duplicate an entry, because the database refuses it
--    before any application logic runs.

CREATE TABLE IF NOT EXISTS statement (
    id                INTEGER PRIMARY KEY,
    company_id        INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    bank_account_id   INTEGER REFERENCES bank_account(id) ON DELETE SET NULL,
    file              TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    layout            TEXT,
    accepted          INTEGER NOT NULL,
    opening_balance   TEXT,
    total_credits     TEXT,
    total_debits      TEXT,
    closing_balance   TEXT,
    difference        TEXT,
    diagnostic        TEXT,
    -- Last day covered by the statement. The balance shown on the dashboard is the one for the
    -- most RECENT period, not the one from the file imported last: importing May after June
    -- must not send the dashboard back in time.
    period_end        TEXT,
    -- First day covered. Without it there is no way to tell whether the file covers a WHOLE
    -- month, and a partial monthly slice would pass for the month's closing.
    period_start      TEXT,
    imported          INTEGER NOT NULL DEFAULT 0,
    duplicates        INTEGER NOT NULL DEFAULT 0,
    imported_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entry (
    id                 INTEGER PRIMARY KEY,
    company_id         INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    bank_account_id    INTEGER NOT NULL REFERENCES bank_account(id) ON DELETE CASCADE,
    statement_id       INTEGER REFERENCES statement(id) ON DELETE SET NULL,
    identity           TEXT NOT NULL,
    posting_date       TEXT NOT NULL,
    period             TEXT NOT NULL,
    description        TEXT NOT NULL,
    edited_description TEXT,
    amount             TEXT NOT NULL,
    direction          TEXT NOT NULL,
    document           TEXT,
    counterparty       TEXT,
    context            TEXT,
    category_id        INTEGER REFERENCES category(id) ON DELETE SET NULL,
    ledger_account     TEXT,
    -- A classification that applies to THIS entry ONLY. It is set when the accountant changes
    -- the category of an entry that was already classified and picks "change only this one":
    -- a decision about that document, not a rule. It is left out of the suggestion vote count
    -- (see `suggestions.suggestion_map`), so a one-off fix doesn't drag down the confidence of
    -- the whole group.
    one_off_classification INTEGER NOT NULL DEFAULT 0,
    page               INTEGER NOT NULL DEFAULT 1,
    file_order         INTEGER NOT NULL DEFAULT 0,
    UNIQUE (bank_account_id, identity)
);

-- A reading that OCR couldn't make (or made and got wrong) and that a PERSON supplied by
-- looking at the paper statement.
--
-- The trail exists because a statement with seven amounts typed in by hand must not be
-- indistinguishable from one read entirely by the machine. Six months from now, in a
-- discrepancy, the first question will be "did this number come from the bank or from someone
-- in a hurry?", and without a record nobody can answer it.
--
-- It stores what OCR returned (`ocr_text`) next to what was supplied: that pair is what lets
-- someone audit the decision, not just the result. `supplied_by` holds the email of whoever
-- typed the amount, or `reread` when it was the machine re-reading the PDF (see ADR 3). It is
-- NULL only when the workbench runs locally without the login gate, because then there is no
-- user.
CREATE TABLE IF NOT EXISTS corrected_reading (
    id              INTEGER PRIMARY KEY,
    statement_id    INTEGER NOT NULL REFERENCES statement(id) ON DELETE CASCADE,
    address         TEXT NOT NULL,
    page            INTEGER,
    ocr_text        TEXT NOT NULL,
    supplied_amount TEXT NOT NULL,
    supplied_at     TEXT NOT NULL,
    supplied_by     TEXT,
    UNIQUE (statement_id, address)
);

-- Who changed what, and when. It covers EVERY request that changes data, not only the ones
-- someone remembered to instrument: a middleware writes it, the same idea as the login gate, so
-- a new route is audited from day one.
--
-- It stores method, path and status, not the body. The body would carry the data itself
-- (amount, description, classification), duplicating in the log what is already in the tables
-- and adding one more place for it to leak from. The path already names the target: to find out
-- what changed on entry 4712, look for `/api/entries/4712/` here and read the current state in
-- the entry table.
--
-- Refused attempts are recorded too. A trail with holes is worth less than a trail with noise:
-- "so-and-so tried and got a 403" is exactly the kind of row people go looking for later.
CREATE TABLE IF NOT EXISTS audit_trail (
    id          INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    person      TEXT,
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    status      INTEGER NOT NULL
);
