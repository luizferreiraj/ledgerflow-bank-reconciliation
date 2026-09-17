"""The whole pipeline on a synthetic SISBR statement: it accepts, it refuses, and it names the day.

These checks used to run against client statements, and they left the public suite along with
them. They come back here on top of `synthetic_statement`: a PDF with a text layer and fictitious
data, written to `tmp_path` for every test. Nothing in the parser is imitated: what is under test
is `statement.process_file` reading a piece of paper.

The expected numbers are written by hand (see `synthetic_statement.june_statement`), not recomputed
by the generator: if the generator and the parser were wrong together, a comparison between the two
would still pass.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from statement import Direction, process_file
from synthetic_statement import write_pdf, june_statement, statement_lines, flip_sign


@pytest.fixture(autouse=True)
def no_ocr(monkeypatch):
    """The synthetic statement has a text layer: OCR plays no part here.

    On a machine with tesseract, a REFUSED statement still goes through the enlarged re-read of the
    suspect cells (`pipeline._with_reread`), which renders the page at 600 dpi and calls tesseract:
    one to two seconds per file, and the result would depend on the installed recognizer. With OCR
    switched off, the test runs the same on a laptop and on a clean GitHub runner.
    """
    from statement import ocr

    monkeypatch.setattr(ocr, "available", lambda: False)


def read_file(tmp_path, lines=None, statement=None, label="sicoob-sisbr-2026-06.pdf"):
    statement = statement or june_statement()
    return process_file(write_pdf(tmp_path / label, statement, lines))


def without_line(lines, description):
    """The paper with ONE line fewer, and the printed balances still the bank's."""
    (target,) = [l for l in lines if l.description == description]
    return [l for l in lines if l is not target]


def with_daily_balance(lines, day, amount):
    return [
        replace(l, amount=amount) if (l.posting_date, l.description) == (day, "SALDO DO DIA") else l
        for l in lines
    ]


def days_that_dont_close(result):
    return [d.posting_date for d in result.day_checks if not d.closes]


# ---------------------------------------------------------------- what closes


def test_synthetic_statement_is_accepted_by_the_sisbr_parser_and_closes_at_zero(tmp_path):
    """The numbers of the `RESUMO` summary, to the cent, through the whole pipeline.

    PDF → words → router → column parser → signs → line classification → closing gate. If any link
    slips, the difference stops being 0.00.
    """
    result = read_file(tmp_path)

    assert result.accepted, result.diagnostics
    assert result.layout == "sicoob_sisbr"
    assert result.diagnostics == []
    f = result.closing
    assert f.opening_balance == Decimal("8450.00")
    assert f.total_credits == Decimal("5980.00")
    assert f.total_debits == Decimal("2345.25")
    assert f.computed_balance == f.declared_balance == Decimal("12084.75")
    assert f.difference == Decimal("0.00") and f.matches
    assert result.periods == ["2026-06"]


def test_balance_and_blocked_deposit_lines_are_not_entries(tmp_path):
    """12 dated lines, 7 entries.

    The 4 balance lines (`SALDO ANTERIOR` and three `SALDO DO DIA`) and the blocked deposit
    (`1.500,00*`) stay out. Counted as a transaction, a balance would add itself to the sum, and the
    blocked deposit would count twice, because its release the next day is a real credit.
    """
    result = read_file(tmp_path)

    c = result.counts
    assert (c.transaction_lines, c.entries, c.balance_lines, c.non_posting) == (
        12, 7, 4, 1,
    )
    descriptions = [l.description for l in result.entries]
    assert not [h for h in descriptions if h.startswith("SALDO")]
    assert "DEP CH.CANAL ATEND.1D" not in descriptions
    assert "LIBERAÇÃO DEP.BLOQUEADO" in descriptions


def test_each_entry_keeps_the_printed_date_amount_and_sign(tmp_path):
    """Closing at 0.00 proves the sum, not which line each amount belongs to.

    Hence the whole list, in order: date, description, amount and direction of every line, against
    the data that was printed. The two identical `CRÉD.LIQ.COBRANÇA` are still TWO.
    """
    statement = june_statement()
    result = read_file(tmp_path, statement=statement)

    entries_read = [(l.posting_date, l.description, l.amount, l.direction) for l in result.entries]
    printed = [
        (
            m.posting_date,
            m.description,
            m.amount,
            Direction.CREDIT if m.sign == "C" else Direction.DEBIT,
        )
        for m in statement.transactions
        if m.sign != "*"
    ]
    assert entries_read == printed


def test_counterparty_and_document_come_from_context_lines(tmp_path):
    """The CNPJ and the `DOC.:` live on the line below, not on the dated line.

    The counterparty is what tells one payment from another at classification time; without it,
    two different suppliers would share the same suggestion.
    """
    by_description = {l.description: l for l in read_file(tmp_path).entries}

    pix = by_description["PIX RECEB.OUTRA IF"]
    assert pix.raw_counterparty == "21.212.121 0001-78"
    assert pix.context == ["DISTRIBUIDORA NORTE LTDA", "21.212.121 0001-78"]
    assert by_description["DÉB.TIT.COMPE EFETIVADO"].document == "40211"
    assert by_description["PIX EMIT.OUTRA IF"].raw_counterparty == "***.123.123-**"
    assert by_description["TARIFA COBRANÇA"].context == []


def test_header_identifies_holder_account_and_period(tmp_path):
    """This is where the workbench finds (or creates) the bank account."""
    c = read_file(tmp_path).header
    assert c.institution == "SICOOB / SISBR"
    assert c.account_holder == "METALURGICA HORIZONTE LTDA"
    assert c.account == "12345-6"
    assert c.cooperative == "1234 / SICOOB COOPCENTRAL"
    assert (c.period_start, c.period_end) == (date(2026, 6, 1), date(2026, 6, 30))


def test_every_day_closes_against_its_daily_balance(tmp_path):
    """The daily running balance checks every `SALDO DO DIA`, and here they all match."""
    result = read_file(tmp_path)
    assert [(d.posting_date, d.declared, d.closes) for d in result.day_checks] == [
        (date(2026, 6, 2), Decimal("10154.75"), True),
        (date(2026, 6, 3), Decimal("9304.75"), True),
        (date(2026, 6, 4), Decimal("12084.75"), True),
    ]


# ------------------------------------------------------------ what is refused


def test_lost_line_refuses_the_statement_and_names_the_day(tmp_path):
    """One line fewer on the paper, and nothing comes out.

    The R$ 12,90 fee disappears from the table, but the printed balances are still the bank's, just
    as when the reader loses a line. The whole file is refused, with no entries and the exact
    difference; and the daily running balance says which day to look at, instead of "R$ 12,90 is
    missing" somewhere in the month.
    """
    lines = without_line(statement_lines(june_statement()), "TARIFA COBRANÇA")
    result = read_file(tmp_path, lines)

    assert not result.accepted
    assert result.entries == [], "a refusal never carries entries"
    (diagnostic,) = result.diagnostics
    assert diagnostic.code == "CLOSING_MISMATCH"
    assert result.closing.difference == Decimal("12.90")
    assert days_that_dont_close(result) == [date(2026, 6, 2)]
    assert "02/06" in diagnostic.details["days_that_dont_close"]


def test_flipped_sign_is_refused_with_twice_the_amount(tmp_path):
    """The R$ 850,00 Pix read as a credit.

    No line disappears and no number changes, only the side. The difference is twice the amount
    (R$ 1.700,00), and it is the only day that doesn't close.
    """
    lines = [
        flip_sign(l) if l.description == "PIX EMIT.OUTRA IF" else l
        for l in statement_lines(june_statement())
    ]
    result = read_file(tmp_path, lines)

    assert not result.accepted
    assert result.entries == []
    assert [d.code for d in result.diagnostics] == ["CLOSING_MISMATCH"]
    assert result.closing.difference == Decimal("1700.00")
    assert days_that_dont_close(result) == [date(2026, 6, 3)]


def test_wrong_closing_balance_is_refused(tmp_path):
    """The anchor is read too, so it can be wrong too: `12.084,57` instead of `12.084,75`.
    Eighteen cents are enough."""
    lines = with_daily_balance(statement_lines(june_statement()), "04/06", "12.084,57C")
    result = read_file(tmp_path, lines)

    assert not result.accepted
    assert result.closing.difference == Decimal("0.18")
    assert days_that_dont_close(result) == [date(2026, 6, 4)]


def test_wrong_mid_month_daily_balance_becomes_a_warning_naming_the_day(tmp_path):
    """A wrong intermediate `SALDO DO DIA` doesn't change the total: the file closes.

    The daily running balance doesn't refuse yet (see the top of `daily_balance_check`), but it
    doesn't stay silent either: the file is accepted with `DAY_DOES_NOT_CLOSE` naming the day. The
    next day is flagged as well, with the opposite difference, because each day starts from the
    balance the statement DECLARES.
    """
    lines = with_daily_balance(statement_lines(june_statement()), "02/06", "10.145,75C")
    result = read_file(tmp_path, lines)

    assert result.accepted
    assert result.closing.difference == Decimal("0.00")
    assert [d.code for d in result.diagnostics] == ["DAY_DOES_NOT_CLOSE"]
    assert days_that_dont_close(result) == [date(2026, 6, 2), date(2026, 6, 3)]
    first = result.day_checks[0]
    assert first.running - first.declared == Decimal("9.00")


# ----------------------------------------- same layout, different arrangement


def test_sign_in_separate_token_does_not_flip_direction(tmp_path):
    """The most dangerous case in the project.

    In SISBR statements produced by iText, every printed line becomes THREE bands: the number
    5.21 pt above the date, the `C`/`D` 5.21 pt below it. Reading band by band would leave the
    amount without a date and the sign without an amount. The generator prints the whole statement
    this way (credits, debits, the `*` and the balance lines), and every entry has to come out with
    the direction it had.
    """
    statement = june_statement()
    lines = [replace(l, separate_sign=True) for l in statement_lines(statement)]
    result = read_file(tmp_path, lines)

    assert result.accepted, result.diagnostics
    assert result.closing.difference == Decimal("0.00")
    assert [(l.description, l.amount, l.direction) for l in result.entries] == [
        (m.description, m.amount, Direction.CREDIT if m.sign == "C" else Direction.DEBIT)
        for m in statement.transactions
        if m.sign != "*"
    ]


def test_descending_order_closes_by_date(tmp_path):
    """The same layout comes out in ascending or descending order, depending on the file.

    With `SALDO ANTERIOR` at the end and the last `SALDO DO DIA` at the top, picking the balance
    anchors by position would close against the wrong daily balance. They are picked by date
    (see ADR 2).
    """
    result = read_file(tmp_path, list(reversed(statement_lines(june_statement()))))

    assert result.accepted, result.diagnostics
    assert result.closing.opening_balance == Decimal("8450.00")
    assert result.closing.declared_balance == Decimal("12084.75")
    assert [l.posting_date for l in result.entries][0] == date(2026, 6, 4)
