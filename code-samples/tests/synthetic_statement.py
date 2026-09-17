"""A synthetic Sicoob SISBR statement: a real PDF with made-up data.

## Why it exists

The project's core guarantees (the statement closes at 0.00, a lost line or a flipped sign is
refused, re-importing does not duplicate, the batch goes out whole or not at all) used to be proven
against client statements. Those can't be public, and without them no test in the public suite
read a PDF all the way to acceptance: the parts were proven, the whole path was not.

This module writes the missing statement: a PDF with a text layer, written by hand byte by byte
(no new dependency, for the same reason as the other hand-made test PDFs), that the REAL SISBR
parser accepts. Nothing here imitates the parser. It is the paper the bank would print, with every
word at the coordinate where the layout expects it, and the pipeline is still what decides whether
it closes.

## Why SISBR

- It prints `SALDO DO DIA`, so the same PDF exercises both the overall closing and the daily
  running balance (`daily_balance_check`), which says WHICH day doesn't close.
- The text-layer path is direct: the amount has its sign attached, no `R$`, and the columns are
  absolute positions declared in the layout signature YAML. The Internet Banking layout prefixes
  amounts with `R$`, which goes through the fixes that merge words across bands.
- The SISBR parser is the shared column parser with nothing on top (the base that the Internet
  Banking layout inherits), and it is the first layout the router tries.
- It declares `separate_token_suffix`: the sign on a `y` of its own. The generator can print that
  way, and it is the case where reading line by line silently flips the sign.

## How the tests use it

In two steps, on purpose:

1. `statement_lines` works out what the bank would print: the opening balance, each transaction
   line and each day's `SALDO DO DIA`, added up HERE from the data;
2. `write_pdf` draws a list of lines.

Between the two steps a test can damage the paper (drop a line, swap `C` for `D`, get a daily
balance wrong), and the printed balances are still those of the correct statement. That is exactly
what the closing gate exists to catch: what reached the reader is not what the bank added up.

All data is fictitious: company, CNPJ, cooperative, account, counterparties and amounts.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ACCOUNT_HOLDER = "METALURGICA HORIZONTE LTDA"
CNPJ = "11.111.111/0001-92"
COOPERATIVE = "1234 / SICOOB COOPCENTRAL"
ACCOUNT = "12345-6"

# ------------------------------------------------------------------- geometry
#
# The SISBR column ranges in the layout signature YAML: date [90, 150], description [150, 430],
# amount [430, 510]. The parser assigns a word to a column by the word's CENTER, so each item goes
# inside its range with room to spare on both sides.

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0
FONT_SIZE = 8.0
X_DATE = 100.0
X_DESCRIPTION = 160.0
# Amounts are RIGHT-aligned, as on the printed statement: `12,90D` and `12.084,75C` end at the same
# edge. At font size 8 the widest amount in these tests is ~42 pt wide, so its center falls between
# 475 and 486, inside [430, 510] with room to spare on both sides.
AMOUNT_RIGHT_EDGE = 496.0
# From one table line to the next. At font size 8, 16 pt keep the bands apart even when the sign
# sits on its own `y` (see `SIGN_Y_OFFSET`): the sign of one line and the number of the next are
# 5.58 pt apart, against the 2 pt tolerance of `bands_by_y`.
LINE_SPACING = 16.0
# Each context line (`FAV.:`, CNPJ, `DOC.:`) comes right below the line it belongs to.
CONTEXT_LINE_SPACING = 11.0
# The `separate_token_suffix` convention, measured on SISBR statements produced by iText: the
# number sits 5.21 pt ABOVE the date and the sign 5.21 pt BELOW it. At font size 8 the three bands
# overlap by 35%, less than the 50% the split-line merge requires, so they stay three bands, as in
# the real file.
SIGN_Y_OFFSET = 5.21
HEADER_TOP = 800.0
BOTTOM_MARGIN = 40.0

# Helvetica widths (AFM metrics, in thousandths of the font size) of the characters that can appear
# in an amount. They are only needed for right alignment; the rest of the text is left-aligned and
# needs no measuring.
GLYPH_WIDTHS = {**{d: 556 for d in "0123456789"}, ".": 278, ",": 278, "C": 722, "D": 722, "*": 389}


def width(string: str, body: float = FONT_SIZE) -> float:
    return sum(GLYPH_WIDTHS[c] for c in string) * body / 1000


# ----------------------------------------------------------------------- data


@dataclass(frozen=True)
class Transaction:
    """An entry as the bank recorded it."""

    posting_date: date
    description: str
    amount: Decimal
    # `C` credit, `D` debit, `*` non-posting (a blocked deposit, which is listed but does not
    # count toward the balance)
    sign: str
    context: tuple[str, ...] = ()


@dataclass(frozen=True)
class Statement:
    """One account's statement for a period. The opening balance is SIGNED."""

    opening_balance: Decimal
    transactions: tuple[Transaction, ...]
    begin: date = date(2026, 6, 1)
    end: date = date(2026, 6, 30)
    account_holder: str = ACCOUNT_HOLDER
    cnpj: str = CNPJ
    cooperative: str = COOPERATIVE
    account: str = ACCOUNT
    # Downloading the same statement twice gives the same transactions in different bytes: the
    # bank stamps the time of issue. That is what separates "the same file again" from "the same
    # statement, in another file".
    issued_at: datetime = datetime(2026, 7, 1, 8, 30)


@dataclass(frozen=True)
class Line:
    """A table line as it comes out on paper."""

    posting_date: str
    description: str
    # number and sign together, `1.482,35D`
    amount: str
    context: tuple[str, ...] = ()
    # the sign on its own `y`, below the date, and the number above it
    separate_sign: bool = False


def effect(transaction: Transaction) -> Decimal:
    """How much the transaction changes the balance."""
    return {"C": transaction.amount, "D": -transaction.amount, "*": Decimal("0.00")}[
        transaction.sign
    ]


def total_credits(statement: Statement) -> Decimal:
    return sum((m.amount for m in statement.transactions if m.sign == "C"), Decimal("0.00"))


def total_debits(statement: Statement) -> Decimal:
    return sum((m.amount for m in statement.transactions if m.sign == "D"), Decimal("0.00"))


def closing_balance(statement: Statement) -> Decimal:
    return statement.opening_balance + sum(
        (effect(m) for m in statement.transactions), Decimal("0.00")
    )


def number(amount: Decimal) -> str:
    """`Decimal('12084.75')` -> `12.084,75`, without a sign."""
    return f"{abs(amount):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def printed_balance(balance: Decimal) -> str:
    """An overdrawn balance is printed with `D`, a positive one with `C`."""
    return number(balance) + ("D" if balance < 0 else "C")


def flip_sign(line: Line) -> Line:
    """The same line with `C` in place of `D`, and vice versa."""
    swap = {"C": "D", "D": "C"}
    return replace(line, amount=line.amount[:-1] + swap[line.amount[-1]])


def statement_lines(statement: Statement) -> list[Line]:
    """The table body, in the order SISBR prints it.

    `SALDO ANTERIOR` dated the day before the period; then, day by day, the transactions in the
    given order and the `SALDO DO DIA` added up to that point.
    """
    balance = statement.opening_balance
    day_before = statement.begin - timedelta(days=1)
    result = [Line(f"{day_before:%d/%m}", "SALDO ANTERIOR", printed_balance(balance))]
    for day in sorted({m.posting_date for m in statement.transactions}):
        for transaction in (m for m in statement.transactions if m.posting_date == day):
            balance += effect(transaction)
            result.append(
                Line(
                    f"{day:%d/%m}",
                    transaction.description,
                    number(transaction.amount) + transaction.sign,
                    transaction.context,
                )
            )
        result.append(Line(f"{day:%d/%m}", "SALDO DO DIA", printed_balance(balance)))
    return result


# -------------------------------------------------------------- the statement


def june_statement(**overrides) -> Statement:
    """Three days of transactions in a fictitious account.

    Chosen so that every line carries a guarantee:

    - a `*` (blocked deposit) that must not become an entry, and its release the next day, which
      must;
    - a counterparty in a CNPJ context line, and a document number in a `DOC.:` line;
    - two identical `CRÉD.LIQ.COBRANÇA` on the same day, which are TWO entries and not a duplicate:
      deduplication tells them apart by their ordinal.

    The arithmetic, done by hand::

        opening balance             8.450,00 C
        02/06   +3.200,00  -1.482,35  -12,90       balance 10.154,75
        03/06   (1.500,00 blocked)    -850,00      balance  9.304,75
        04/06   +1.500,00  +640,00  +640,00        balance 12.084,75

        credits 5.980,00 · debits 2.345,25 · 7 entries
    """
    data = {
        "opening_balance": Decimal("8450.00"),
        "transactions": (
            Transaction(
                date(2026, 6, 2), "PIX RECEB.OUTRA IF", Decimal("3200.00"), "C",
                ("DISTRIBUIDORA NORTE LTDA", "21.212.121 0001-78"),
            ),
            Transaction(
                date(2026, 6, 2), "DÉB.TIT.COMPE EFETIVADO", Decimal("1482.35"), "D",
                ("TORNEARIA PRECISAO LTDA", "DOC.: 40211"),
            ),
            Transaction(date(2026, 6, 2), "TARIFA COBRANÇA", Decimal("12.90"), "D"),
            Transaction(date(2026, 6, 3), "DEP CH.CANAL ATEND.1D", Decimal("1500.00"), "*"),
            Transaction(
                date(2026, 6, 3), "PIX EMIT.OUTRA IF", Decimal("850.00"), "D",
                ("FAV.: JOAO PEREIRA SANTOS", "***.123.123-**"),
            ),
            Transaction(date(2026, 6, 4), "LIBERAÇÃO DEP.BLOQUEADO", Decimal("1500.00"), "C"),
            Transaction(date(2026, 6, 4), "CRÉD.LIQ.COBRANÇA", Decimal("640.00"), "C"),
            Transaction(date(2026, 6, 4), "CRÉD.LIQ.COBRANÇA", Decimal("640.00"), "C"),
        ),
    }
    data.update(overrides)
    return Statement(**data)


# ------------------------------------------------------------------------ PDF


def write_pdf(
    destination: Path, statement: Statement, lines: list[Line] | None = None
) -> Path:
    """Draws the statement on an A4 page and saves it to `destination`.

    `lines` is the table body; without it, the body comes from `statement_lines`. The header and
    the `RESUMO` summary always come from `statement`: on damaged paper the summary still tells the
    truth, as the bank's would.

    The header carries the three phrases the router requires from SISBR (`PLATAFORMA DE SERVICOS
    FINANCEIROS DO SICOOB`, `HISTORICO DE MOVIMENTACAO`, `DATA HISTORICO VALOR`), each on a single
    band, and with accents: the signature is compared without accents, so the accents prove that
    normalization works. `COOP.:` and `CONTA:` sit alone on their lines, because the header
    patterns are anchored to the start and end of the band.
    """
    body = statement_lines(statement) if lines is None else lines
    words: list[tuple[float, float, float, str]] = []
    y = HEADER_TOP

    def header(string: str, font_size: float = 9.0) -> None:
        nonlocal y
        words.append((40.0, y, font_size, string))
        y -= 13.0

    header("SICOOB - SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL", 10.0)
    header("PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR")
    header(f"EMITIDO EM {statement.issued_at:%d/%m/%Y %H:%M}")
    header("EXTRATO CONTA CORRENTE")
    header(f"COOP.: {statement.cooperative}")
    header(f"CONTA: {statement.account} / {statement.account_holder}")
    header(f"CNPJ: {statement.cnpj}")
    header(f"PERÍODO: {statement.begin:%d/%m/%Y} a {statement.end:%d/%m/%Y}")
    y -= 6.0
    header("HISTÓRICO DE MOVIMENTAÇÃO")
    words += [
        (X_DATE, y, FONT_SIZE, "DATA"),
        (X_DESCRIPTION, y, FONT_SIZE, "HISTÓRICO"),
        (AMOUNT_RIGHT_EDGE - 26.0, y, FONT_SIZE, "VALOR"),
    ]
    y -= LINE_SPACING

    for line in body:
        words += [
            (X_DATE, y, FONT_SIZE, line.posting_date),
            (X_DESCRIPTION, y, FONT_SIZE, line.description),
        ]
        if line.separate_sign:
            numerals, sign = line.amount[:-1], line.amount[-1]
            words.append(
                (AMOUNT_RIGHT_EDGE - width(numerals), y + SIGN_Y_OFFSET, FONT_SIZE, numerals)
            )
            words.append(
                (AMOUNT_RIGHT_EDGE - width(sign), y - SIGN_Y_OFFSET, FONT_SIZE, sign)
            )
        else:
            words.append((AMOUNT_RIGHT_EDGE - width(line.amount), y, FONT_SIZE, line.amount))
        for string in line.context:
            y -= CONTEXT_LINE_SPACING
            words.append((X_DESCRIPTION, y, FONT_SIZE, string))
        y -= LINE_SPACING

    # The summary at the bottom of the page has amounts in the SAME column as the entries, which is
    # why the transaction region ends at `RESUMO`.
    words.append((X_DESCRIPTION, y, FONT_SIZE, "RESUMO"))
    for tag, amount in (
        ("SALDO ANTERIOR", printed_balance(statement.opening_balance)),
        ("CRÉDITOS", number(total_credits(statement)) + "C"),
        ("DÉBITOS", number(total_debits(statement)) + "D"),
        ("SALDO ATUAL", printed_balance(closing_balance(statement))),
    ):
        y -= CONTEXT_LINE_SPACING
        words += [
            (X_DESCRIPTION, y, FONT_SIZE, tag),
            (AMOUNT_RIGHT_EDGE - width(amount), y, FONT_SIZE, amount),
        ]

    if y < BOTTOM_MARGIN:
        # A single page, on purpose: nothing in these tests needs a page break, and text drawn
        # below the margin would fall off the page without any warning.
        raise ValueError(f"the statement does not fit on one page (it would end at y={y:.1f})")
    return _save(destination, words)


def _save(destination: Path, words: list[tuple[float, float, float, str]]) -> Path:
    """A one-page A4 PDF with each text at the given coordinate.

    The same skeleton as the other hand-made test PDFs (computed xref, standard Type1 font,
    nothing embedded), with one difference: the font declares `/WinAnsiEncoding` and the text is
    encoded as cp1252. Without it, `É` and `Ç` would have no code in Helvetica's standard encoding,
    and Sicoob descriptions are full of them.

    `(x, y)` is the BASELINE, in points, with the origin at the bottom-left corner. `pdfplumber`
    returns `top = 842 - y - 0.793 * font_size` for Helvetica, and that `top` is what `bands_by_y`
    groups.
    """
    commands = []
    for x, y, body, string in words:
        raw = string.encode("cp1252").replace(b"\\", b"\\\\")
        raw = raw.replace(b"(", b"\\(").replace(b")", b"\\)")
        commands.append(b"BT /F1 %.1f Tf %.2f %.2f Td (%s) Tj ET" % (body, x, y, raw))
    stream = b"\n".join(commands)
    pdf_objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        % (PAGE_WIDTH, PAGE_HEIGHT),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    file, byte_offsets = b"%PDF-1.4\n", []
    for obj_number, pdf_object in enumerate(pdf_objects, start=1):
        byte_offsets.append(len(file))
        file += b"%d 0 obj\n" % obj_number + pdf_object + b"\nendobj\n"
    xref_start = len(file)
    file += b"xref\n0 %d\n0000000000 65535 f \n" % (len(pdf_objects) + 1)
    for byte_offset in byte_offsets:
        file += b"%010d 00000 n \n" % byte_offset
    file += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(pdf_objects) + 1, xref_start)
    )
    destination.write_bytes(file)
    return destination
