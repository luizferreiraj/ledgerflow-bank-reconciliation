"""Data contract of the statement extraction package.

Internal geometric types (`Word`, `LogicalLine`) are dataclasses with slots: they are created by
the thousands per file and never leave the package. The types that make up the output envelope
are pydantic v2 models, because that envelope is the contract the rest of the system consumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

from .money import to_text

Money = Annotated[Decimal, PlainSerializer(to_text, return_type=str)]


class Direction(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class LineKind(str, Enum):
    ENTRY = "entry"
    BALANCE = "balance"
    NON_POSTING = "non_posting"


@dataclass(slots=True)
class Word:
    string: str
    x0: float
    x1: float
    top: float
    bottom: float
    page: int
    # How much the OCR engine trusts this reading (0 to 100), or `None` when the word came from
    # the PDF's text layer, where there is nothing to doubt.
    #
    # It is not used to DISCARD words (a confidence cutoff once deleted a correct amount, see
    # `ocr`). It is used to ORDER them: when a day doesn't close and the arithmetic can't point
    # at the line, the lowest-confidence reading is the best place for the eye to start.
    # Measured: the `,74` of the misread amount on page 37 came out at 24, against a page
    # average of 70.3.
    confidence: float | None = None

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(slots=True)
class LogicalLine:
    """A visual block rebuilt from geometry, not yet interpreted."""

    words: list[Word]
    page: int

    @property
    def string(self) -> str:
        return " ".join(p.string for p in sorted(self.words, key=lambda p: (p.top, p.x0)))

    @property
    def top_edge(self) -> float:
        return min(p.top for p in self.words)


@dataclass(slots=True)
class RawLine:
    """The source boundary: nothing below this point knows what a PDF is.

    A future OFX/CSV adapter produces `RawLine`s and reuses everything else.

    ## And it is no longer hypothetical

    "Is PDF mandatory, or is there OFX/CSV?" stayed open from the start of the project, noted as
    the one answer that could remove the largest block of effort here. On 2026-09-09 it was
    answered: **some of the firm's clients send the bank's OFX without any trouble**. The
    decision was to postpone, so the team could start using what is already built.

    It is written down here because this is where the next person will look, and because the
    gain is large: OFX is exact data. No OCR, no cell rereads, no dependence on the tesseract
    version. The whole class of defect that `sicoob_sisbr` documents simply doesn't exist.

    Two things the adapter will run into, which make it an ADDITION to PDF and not a
    replacement:

    - many banks' `<MEMO>` is short and has no counterparty, and classification comes from the
      description: the Sicoob PDF has `PIX RECEB.OUTRA IF` plus the sender, the OFX may have
      just `PIX`;
    - not every file carries an opening balance, and without it the closing check has nothing
      to start from (the same opening-balance-required refusal that two Safra statements
      already get today).

    The closing check judges both sources the same way, so it is possible to start with ONE
    bank and measure, without betting the project.

    ## The requirement, in the words of the people who use the system

    "The system keeps working just like today, and ALSO works with OFX." It is not a
    migration: no PDF statement may be read differently because of the new adapter.

    And that can be checked, not just promised. The 2026-09-09 baseline stores, for every file
    in the corpus, whether it is accepted and how many entries it yields, with a fingerprint of
    `posting_date|description|amount|direction` for each one. Capture before the adapter goes
    in; compare file by file afterwards. Any difference on a PDF is a defect, even if OFX works.

    That comparison caught two regressions on the day line matching switched to bands, and
    both of them passed all 790 tests.
    """

    raw_date: str
    description: str
    raw_amount: str
    document: str | None = None
    context: list[str] = field(default_factory=list)
    page: int = 1
    order: int = 0
    marker: str | None = None
    # The lowest confidence among the tokens that formed this line's AMOUNT.
    confidence: float | None = None
    # The amount cell's rectangle, in PDF points, so it can be reread at a larger scale when
    # the arithmetic points at this line. See `cell_reread`.
    box: tuple[float, float, float, float] | None = None
    # Stable address of the line in the file (`p37-b3036`), so the UI can point at ONE entry and
    # the operator can supply the right amount. Same shape as the `id` of `UnreadableReading`,
    # because both are the same question: "what is written here?"
    source: str | None = None


class Diagnostic(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    details: dict[str, str] = {}


class Balance(BaseModel):
    """Always signed, never a magnitude: C -> positive, D -> negative."""

    amount: Money
    source: str


class StatementHeader(BaseModel):
    institution: str
    account_holder: str | None = None
    account: str | None = None
    branch: str | None = None
    cooperative: str | None = None
    period_start: date | None = None
    period_end: date | None = None


class Entry(BaseModel):
    posting_date: date
    period: str
    description: str
    amount: Money
    direction: Direction
    account: str | None = None
    document: str | None = None
    context: list[str] = []
    raw_counterparty: str | None = None
    page: int = 1
    file_order: int = 0
    source: str | None = None
    confidence: float | None = None
    box: tuple[float, float, float, float] | None = None


class Closing(BaseModel):
    opening_balance: Money
    total_credits: Money
    total_debits: Money
    computed_balance: Money
    declared_balance: Money
    difference: Money
    matches: bool


class Counts(BaseModel):
    transaction_lines: int = 0
    entries: int = 0
    balance_lines: int = 0
    non_posting: int = 0


class UnreadableReading(BaseModel):
    """A place where there should be an amount and OCR didn't produce one.

    It exists so the UI can ask. A 41-page scanned statement lost 8 amounts in more than a
    thousand lines; without this list the operator only knew that "5 amounts are missing" and
    would have had to scan all 41 pages to find which.

    ## Why the `id` carries the position AND the text

    A typed correction is matched to this reading by `id`. If the file is read again with OCR at
    another resolution, the bands change index and yesterday's `id` may point at a different
    line, and the correction would land in the wrong place, silently. So whoever applies it also
    checks `ocr_text`: if it differs, the correction is ignored and the amount is missing again.
    Fail closed.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    page: int
    ocr_text: str
    posting_date: str | None = None
    description: str | None = None
    # What the person typed, if anything, so the UI can show it filled in again.
    supplied_amount: str | None = None
    # This cell's rectangle, for the enlarged reread.
    box: tuple[float, float, float, float] | None = None
    # What the enlarged reread read in this rectangle. It comes from the IMAGE, never from the
    # arithmetic, which is why it can be offered; the computed `target` could not.
    reread: str | None = None
    # Why what the person typed was not accepted. When set, the line goes back to the list WITH
    # the explanation and never disappears. Treating "couldn't parse it" as "leave it blank"
    # once turned four supplied amounts into two, with no warning at all.
    err: str | None = None


class AppliedReread(BaseModel):
    """A cell the SYSTEM reread in the PDF and the arithmetic confirmed.

    It is not the same as a typed amount, and must not look like one. The number came from the
    image (the cell cropped, enlarged to 600 dpi and read with a restricted alphabet) and was
    accepted because, with it in place, the day closes and the statement closes at 0.00. Without
    that proof, no candidate gets in.

    It exists so the audit trail can answer "where did this number come from?" six months later
    with three distinct answers: the bank, a person, or the reread (see ADR 3).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    page: int
    # What the whole-page pass had read in that cell.
    read_on_page: str
    # What the enlarged reread read, and the arithmetic confirmed.
    reread: str
    posting_date: str | None = None
    description: str | None = None


class CompletedDate(BaseModel):
    """A line whose date OCR read only partly, and the neighbouring lines completed.

    It is neither a reread nor a typed amount: it is a third kind of source, and has to show up
    as such. The AMOUNT came from the image as usual. What the OCR engine damaged was the date
    (`27/04` read as `2//04`, the `7` turned into a slash), and what completed it were the lines
    above and below, which carry the same day.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    page: int
    # What OCR read in the date cell, exactly as read.
    as_read: str
    # The completed date, `DD/MM`.
    posting_date: str
    description: str | None = None


class IngestionResult(BaseModel):
    file: str
    content_hash: str
    accepted: bool
    layout: str | None = None
    header: StatementHeader | None = None
    closing: Closing | None = None
    counts: Counts = Counts()
    periods: list[str] = []
    entries: list[Entry] = []
    diagnostics: list[Diagnostic] = []
    # Filled when the file is refused because a reading was lost: it is what the UI shows the
    # operator to check against the paper and supply.
    unreadable_readings: list[UnreadableReading] = []
    # One item per day with a `SALDO DO DIA` (daily balance), so the UI can say right away
    # whether a supplied amount made that day close (see `daily_balance_check`).
    day_checks: list["CheckedDay"] = []
    # The cells the system reread in the PDF on its own and the arithmetic confirmed. Only filled
    # on an ACCEPTED file: a candidate that didn't close is not applied, so it never shows up
    # here.
    applied_rereads: list[AppliedReread] = []
    # What was supplied and the parser did NOT accept, by address. Returning this is what stops
    # the operator from resending the same number against the same error, with nothing on
    # screen saying the typed value was discarded.
    refused_corrections: dict[str, str] = {}
    # The file had no text layer and was read via OCR. The UI uses this to warn that the reading
    # may be wrong, and to suggest asking the client for the bank's original PDF, which is read
    # directly with no recognition at all.
    read_via_ocr: bool = False
    # Lines whose date OCR read only partly and the neighbouring lines completed. Always
    # present, accepted or not: it is a decision the system made, and decisions aren't made
    # silently.
    completed_dates: list[CompletedDate] = []

    @classmethod
    def refused(
        cls, file: str, content_hash: str, diagnostic: Diagnostic, **extra
    ) -> "IngestionResult":
        """Fail closed: a refusal never carries entries (see ADR 1)."""
        return cls(
            file=file,
            content_hash=content_hash,
            accepted=False,
            entries=[],
            diagnostics=[diagnostic],
            **extra,
        )


class CheckedDay(BaseModel):
    """The verdict for one day, so the UI can give immediate feedback.

    It is what turns "R$ 2.160,40 is missing" into "days 04/05, 05/05 and 20/05 don't close":
    from more than a thousand lines down to three days.
    """

    model_config = ConfigDict(frozen=True)

    posting_date: date
    running: Money
    declared: Money
    closes: bool
    # Where to check on paper. Measured on the scanned May statement: the three days that didn't
    # close fell on five pages out of 41.
    pages: list[int] = []
    # The day's entries whose amount looks most like the difference. Ordering is not accusing;
    # it is a shortcut for the eye. The arithmetic still decides whether a correction holds.
    suspects: list["Suspect"] = []
    # The REST of the day, in the same order. The UI shows the first five; the reread needs all
    # of them. Measured on the COOPCENTRAL April statement with tesseract 5: the two debits that
    # had lost their leading `7` (`38,45` for `738,45`, `29,17` for `729,17`) ranked 12th and
    # 14th on a 54-line day, off the screen and out of the reread, which only looked at five.
    rest: list["Suspect"] = []


class Suspect(BaseModel):
    """An entry from a day that doesn't close, addressable for correction."""

    model_config = ConfigDict(frozen=True)

    source: str | None
    page: int
    posting_date: date
    amount: Money
    direction: Direction
    description: str
    # How much the OCR engine trusted this reading. Low confidence doesn't prove an error, but it
    # is the best place for the eye to start when the arithmetic doesn't point at the line.
    confidence: float | None = None
    # What the enlarged reread read in this cell, when it read something different AND that
    # something makes the day close.
    #
    # Not to be confused with "the amount that would make the arithmetic close": that one comes
    # from a subtraction and would close by construction. Offering it would push the person to
    # accept a number the system itself made up. This one comes from the IMAGE, read again at a
    # larger scale, and the arithmetic only CHECKS it. They are opposites.
    reread: str | None = None
    # This cell's rectangle, so the enlarged reread knows where to crop.
    box: tuple[float, float, float, float] | None = None


# The models refer to each other by name (`"CheckedDay"`, `"Suspect"`), so the rebuild comes at
# the end of the module: before this point the name doesn't exist yet, and pydantic fails at
# definition time, not at use.
CheckedDay.model_rebuild()
IngestionResult.model_rebuild()
