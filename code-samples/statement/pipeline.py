"""Excerpt of the pipeline module: `process_file` and the reread loop proven by arithmetic.

Left out: counterparty extraction, entry conversion and the per-day verdicts. The reading pass
(`_read`) and the 400 dpi second reading (`_second_reading`) appear only as stubs.

`process_file` always returns an `IngestionResult`: a refusal is a return value, not an
exception. A refused file comes out with `accepted=False` and `entries=[]`. Failing closed is
not a convention here; it is the shape of the data (see ADR 1).

The reread loop (see ADR 3): the arithmetic says WHERE to look, a targeted reread of one cell
says WHAT is there, and the arithmetic CHECKS what was read. None of the three decides alone.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .pdf_text import UnreadableDocument, extract_pages, content_hash
from .models import (
    Direction,
    Diagnostic,
    AppliedReread,
    IngestionResult,
)

log = logging.getLogger("statement")


def _was_ocr(pathname: Path) -> bool:
    """Was this content read via OCR?

    Cached OCR exists for it exactly when there was no usable text layer: OCR only runs after
    that verdict. The key is the content hash, and asking costs one read of the file, without
    rasterizing anything.
    """
    from . import ocr

    try:
        return ocr.available() and ocr.already_recognized(pathname)
    except Exception:  # pragma: no cover - asking never breaks the reading
        return False


def process_file(
    pathname: str | Path, opening_balance=None, corrections: dict[str, dict] | None = None
) -> IngestionResult:
    """`opening_balance` is only used by layouts that don't print that anchor.

    Today that is Safra, and only Safra. The supplied number doesn't replace the proof: it goes
    into the same closing arithmetic and, if it doesn't match the balance declared on the
    statement, the file is refused with the exact difference.

    ## `corrections`: the operator reads what OCR couldn't

    `{reading_id: {"amount": "-745,00", "ocr_text": "R$ 745 ,/00D"}}`.

    On a scanned statement, the OCR engine sometimes loses an amount outright: not part of it,
    not a swapped digit, the whole amount. The file is refused with the list of where that
    happened (`unreadable_readings`), the UI asks, and the supplied number comes in here.

    This does NOT loosen the closing check, and it is the opposite of deducing the amount from
    the arithmetic:

    - **deducing** makes the file close BY CONSTRUCTION. The 0.00 stops being a proof and becomes
      a tautology. Measured: for page 37 of the scanned May statement, deduction gave R$ 528,74
      and the paper says R$ 528,34. It would have stored 40 cents of error with a "checked"
      stamp on it.
    - **asking** keeps the reader (the person, with the statement in hand) separate from the
      judge (the arithmetic). A mistyped amount does NOT close, and the file stays refused. It
      is still a test.

    It is the same mechanism Safra already uses for the opening balance, generalized.
    """
    pathname = Path(pathname)
    label = pathname.name
    _REREAD_CACHE.clear()
    try:
        digest = content_hash(pathname)
    except OSError as err:
        return IngestionResult(
            file=label,
            content_hash="",
            accepted=False,
            diagnostics=[
                Diagnostic(code="UNREADABLE_FILE", message=f"could not read the file: {err}")
            ],
        )

    try:
        pages = extract_pages(pathname)
    except UnreadableDocument as err:
        return IngestionResult.refused(label, digest, err.diagnostic)

    # Scanned statements are where reading goes wrong. The UI needs to know, so it can warn
    # right away and suggest the bank's original PDF instead of leaving the person fighting
    # with corrections.
    via_ocr = _was_ocr(pathname)

    def with_ocr_flag(final: IngestionResult) -> IngestionResult:
        return final.model_copy(update={"read_via_ocr": via_ocr})

    result = _read(pathname, label, digest, pages, opening_balance, corrections)
    if result.accepted:
        return with_ocr_flag(result)
    if not corrections:
        # With typed corrections there is no second reading: it would read the file again at
        # another resolution, the bands would change index and the corrections would lose their
        # addresses. Whoever supplied a value wants an answer about WHAT they supplied.
        second = _second_reading(pathname, label, digest, opening_balance)
        if second is not None:
            return with_ocr_flag(second)
    return with_ocr_flag(
        _proven_by_reread(
            pathname,
            label,
            digest,
            pages,
            opening_balance,
            corrections,
            _with_reread(pathname, result),
        )
    )


# How many times the system tries on its own before handing the question back.
#
# One round is not enough, and that was measured: in `sicoob-ib-2026-01-escaneado.pdf` the first
# stop is a SALDO DO DIA (daily balance) without a sign; once that is resolved, two unreadable
# cells appear; once those are resolved, day 30/01 appears, and only then can it be checked. Each
# answer uncovers the next question. That is three steps, and the operator used to climb them one
# at a time, waiting for the whole file to be read again in between.
#
# The limit exists because a bad file must not cost endless rereads. If four rounds don't make
# it close, the question goes back to the person, which is where it already was.
REREAD_ROUNDS = 4


def _reread_proposals(result: IngestionResult):
    """What the reread read, in correction format, with an `AppliedReread` card for each."""
    proposals: dict[str, dict] = {}
    cards: list[AppliedReread] = []

    def register(address, page, as_read, reread, posting_date, description):
        proposals[address] = {"ocr_text": as_read, "amount": reread}
        cards.append(
            AppliedReread(
                id=address,
                page=page,
                read_on_page=as_read,
                reread=reread,
                posting_date=posting_date,
                description=description,
            )
        )

    for reading in result.unreadable_readings:
        if reading.reread:
            register(
                reading.id, reading.page, reading.ocr_text, reading.reread,
                reading.posting_date, reading.description,
            )
    for day in result.day_checks:
        for suspect in day.suspects:
            if not suspect.reread or not suspect.source:
                continue
            sign = "D" if suspect.direction is Direction.DEBIT else "C"
            register(
                suspect.source,
                suspect.page,
                f"{suspect.amount:.2f}".replace(".", ",") + sign,
                suspect.reread,
                suspect.posting_date.isoformat(),
                suspect.description,
            )
    return proposals, cards


def _proven_by_reread(
    pathname: Path, label, digest, pages, opening_balance, corrections, result
) -> IngestionResult:
    """Resubmit the file with what the REREAD read, and keep it only if it CLOSES.

    ## The problem this solves

    The system already knew how to reread a cell in the PDF and how to check that reading
    against the day's arithmetic. But it kept the answer and handed back the QUESTION: the UI
    showed `reread 327,40C` in a field and waited for someone to click. At an accounting firm's
    pace that is a stalled file, and when the question had three steps, it meant three waits
    for a 42-page file to be read again in full.

    Worse: sometimes there was no step to climb at all. On `sicoob-ib-2026-02-escaneado.pdf` the
    UI asked for five amounts that were ALL correct (the defect was a `SALDO DO DIA` line that
    had been read as a transaction), and no possible answer would make the file close. The
    operator resubmitted, got the same error, and the UI offered no way out.

    ## Why this does NOT loosen the closing check

    What goes in comes from the IMAGE: the cell cropped, enlarged to 600 dpi and read with a
    restricted alphabet. It is a new reading of the same sheet, not arithmetic. And what
    decides whether it stays is the SAME gate as always: the statement must close at 0.00. A
    candidate that doesn't close is not applied, and the file comes back refused exactly as
    before, with the list intact for the person to answer.

    Deducing would mean computing the number from the difference itself, which closes by
    construction, every time, even when wrong. Here the arithmetic only CHECKS a reading it
    didn't produce. That is the difference between a test and a tautology.

    ## What the person still controls

    A typed correction takes precedence: `fresh_proposals` only includes addresses the operator
    did NOT answer. If they read the paper and supplied a value, their number stands; the
    machine doesn't argue with the person holding the statement.

    ## When it doesn't close

    Returns the ORIGINAL result, with none of the proposals applied. Returning the last attempt
    would hide from the UI the cells the machine filled in on its own and couldn't prove: the
    operator would see fewer questions than there really are, with machine values mixed in,
    and no way to disagree.
    """
    supplied = dict(corrections or {})
    # What the person answered outranks the machine, EXCEPT where their answer was refused.
    # There they were left with no value at all, and letting the reread try is better than
    # returning the same refusal; the reason for their refusal stays on screen anyway.
    person_answered = set(supplied) - set(result.refused_corrections)
    applied: list[AppliedReread] = []
    current = result
    for iteration in range(REREAD_ROUNDS):
        proposals, cards = _reread_proposals(current)
        fresh_proposals = {
            address: data
            for address, data in proposals.items()
            if address not in person_answered
        }
        fresh_proposals = {e: d for e, d in fresh_proposals.items() if supplied.get(e) != d}
        if not fresh_proposals:
            return result
        supplied.update(fresh_proposals)
        applied.extend(card for card in cards if card.id in fresh_proposals)
        log.info(
            "file=%s round=%d applying %d reread(s): %s",
            label, iteration + 1, len(fresh_proposals), ", ".join(sorted(fresh_proposals)),
        )
        attempt = _read(pathname, label, digest, pages, opening_balance, supplied)
        if attempt.accepted:
            log.info(
                "file=%s closed with %d cell(s) reread by the system",
                label, len(applied),
            )
            return attempt.model_copy(
                update={
                    "applied_rereads": applied,
                    # The FIRST reading's refusals go along too: if the person's answer was
                    # discarded, they need to know, even if the reread solved it afterwards.
                    "refused_corrections": {
                        **result.refused_corrections,
                        **attempt.refused_corrections,
                    },
                }
            )
        current = _with_reread(pathname, attempt)
    return result


def _second_reading(
    pathname: Path, label: str, digest: str, opening_balance
) -> IngestionResult | None:
    """Read a scanned file again at 400 dpi after a refusal; the result counts only if it closes."""
    ...  # … omitted: OCR at the higher resolution, then the same `_read` and the same gate


def _read(
    pathname: Path, label: str, digest: str, pages, opening_balance, corrections=None
) -> IngestionResult:
    """From the set of words to the result, without knowing where the words came from."""
    ...  # … omitted: layout routing, parsing, entry conversion, closing gate, per-day verdicts


# How many cells of one day the reread looks at, in order of suspicion. A statement day rarely
# has more; the limit exists so a bad file can't cost endless rereads. With the page rendered
# once, it takes seconds.
CELLS_REREAD_PER_DAY = 80
# Up to how many cells in one combination. Two errors on the same day have been measured; three
# gives some headroom. Beyond that, a combination that closes may close by luck, and what closes
# by luck proves nothing.
MAX_COMBINATION_SIZE = 3
# With too many divergences on one day, the reread is reading almost everything differently, and
# no combination deserves trust.
MAX_DIVERGENCES = 20

# The enlarged readings already done for THIS file. The loop runs up to four rounds; without
# this, each round would reread the same cells, and now that a day without a single culprit is
# reread in full, that would mean minutes per round. Cleared on every `process_file`.
_REREAD_CACHE: dict[tuple, tuple[str, ...]] = {}


def _cell_readings(pathname: Path, suspect) -> tuple[str, ...]:
    """What the enlarged reread reads in that cell, with noise stripped and no duplicates."""
    from . import cell_reread

    x0, top_edge, x1, base = suspect.box
    lookup_key = (str(pathname), suspect.page, x0, top_edge, x1, base)
    if lookup_key not in _REREAD_CACHE:
        seen: list[str] = []
        for as_read in cell_reread.candidates(
            pathname, cell_reread.Box(suspect.page, x0, top_edge, x1, base)
        ):
            cleaned = _strip_reread_noise(as_read)
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        _REREAD_CACHE[lookup_key] = tuple(seen)
    return _REREAD_CACHE[lookup_key]


def _delta(suspect, as_read: str):
    """How much the new reading changes the day's sum. None if nothing changes or it isn't a number.

    If the reading has no sign, the line's existing sign holds: what the reread corrects are the
    DIGITS. Treating `738,45` without a `D` as a credit would flip a debit's direction, and in
    the combination search that would fabricate sums that don't exist on paper.
    """
    from .money import to_decimal
    from .layouts.sicoob_sisbr import parse_typed

    raw, err = parse_typed(as_read)
    if err is not None or raw is None:
        return None
    number = to_decimal(raw.rstrip("CD"))
    has_sign = as_read.strip()[-1:].upper() in ("C", "D") or as_read.strip().startswith("-")
    is_debit = raw.endswith("D") if has_sign else suspect.direction is Direction.DEBIT
    new_value = -number if is_debit else number
    current = -suspect.amount if suspect.direction is Direction.DEBIT else suspect.amount
    return (new_value - current) or None


def _combination_that_closes(day, divergences) -> dict[str, str]:
    """The rereads that, TOGETHER, close a day that none of them closes alone.

    Measured on the COOPCENTRAL April statement with tesseract 5, day 14/04, R$ 1.400,00 above
    the SALDO DO DIA::

        DEB.TIT.COMPE EFETIVADO    read  38,45    reread  738,45D    -700,00
        DEB.TIT.COMPE EFETIVADO    read  29,17    reread  729,17D    -700,00

    The same lost `7`, twice on the same day. Fixing just one still leaves 700 missing, so the
    old test ("does this reading close the day?") rejected both, and the day came back as a
    question with five correct credits ranked ahead of them.

    The numbers come from the IMAGE, reread enlarged; the arithmetic only checks. And a
    combination only counts if it is the ONLY one of its size: if two close the day, choosing
    between them would be guessing, and the question goes back to the person. The smallest
    combination that closes is the one that counts: two errors explain the day before three do.
    """
    import itertools

    if not divergences or len(divergences) > MAX_DIVERGENCES:
        return {}
    shortfall = day.declared - day.running
    for size in range(2, MAX_COMBINATION_SIZE + 1):
        solutions = []
        for subset in itertools.combinations(divergences, size):
            for choice in itertools.product(*(readings for _, readings in subset)):
                if sum(delta for _, delta in choice) == shortfall:
                    solutions.append(
                        {s.source: as_read for (s, _), (as_read, _) in zip(subset, choice)}
                    )
        if len(solutions) == 1:
            return solutions[0]
        if solutions:
            return {}  # more than one closes: choosing would be guessing
    return {}


def _reread_flagged_cells(pathname: Path, day_checks) -> dict[str, str]:
    """Reread, enlarged, only the cells of the days that don't close.

    This is the step that closes the loop: the arithmetic says WHICH line to suspect, the reread
    says what is written in that rectangle, and the arithmetic checks what it read.

    ## A candidate only counts if it CLOSES the day

    Each cell comes back with up to three readings, one per tesseract mode, and they disagree
    with each other. Measured: `psm 7` gets page 37 right and page 14 wrong, and `psm 8` does
    the opposite. Picking "the most likely" would be a guess. The one that makes the day close
    gets in, and if none does, none gets in.

    This is NOT deducing the amount. The number comes from the IMAGE, read again at a higher
    resolution with a restricted alphabet; the arithmetic only says whether that reading
    explains the day. Deducing would mean computing the number from the difference itself, and
    that would close by construction, every time, even when wrong.
    """
    from . import cell_reread

    broken_days = [d for d in day_checks if not d.closes]
    if not broken_days:
        return {}
    findings: dict[str, str] = {}
    try:
        for day in broken_days:
            culprit = None
            divergences: list[tuple] = []
            queue = [s for s in (*day.suspects, *day.rest) if s.box and s.source]
            for suspect in queue[:CELLS_REREAD_PER_DAY]:
                differing = []
                for cleaned in _cell_readings(pathname, suspect):
                    if _closes_the_day(day, suspect, cleaned):
                        culprit = (suspect.source, cleaned)
                        break
                    delta = _delta(suspect, cleaned)
                    if delta:
                        differing.append((cleaned, delta))
                if culprit:
                    break  # one culprit per day explains the whole day
                if differing:
                    divergences.append((suspect, differing))
            if culprit:
                findings[culprit[0]] = culprit[1]
            else:
                findings.update(_combination_that_closes(day, divergences))
    finally:
        cell_reread.clear_cache()
    return findings


# Junk at the edges of what the reread returns: `.238,34D`, `238,34D,`, `$ 372,46C`. It is
# noise from the edge of the crop, not part of the number. Trimming the edges can't invent
# anything, because what is left still has to match the whole pattern AND make the day close.
REREAD_LEADING_NOISE = re.compile(r"^[^\d]+")
REREAD_TRAILING_NOISE = re.compile(r"[^\dCDcd]+$")


def _strip_reread_noise(as_read: str) -> str:
    return REREAD_TRAILING_NOISE.sub("", REREAD_LEADING_NOISE.sub("", as_read))


def _closes_the_day(day, suspect, as_read: str) -> bool:
    """Does the new reading, in place of the old one, make the day's sum match?"""
    from .layouts.sicoob_sisbr import parse_typed

    raw, err = parse_typed(as_read)
    if err is not None or raw is None:
        return False
    from .money import to_decimal

    new_value = to_decimal(raw.rstrip("CD"))
    if raw.endswith("D"):
        new_value = -new_value
    current = -suspect.amount if suspect.direction is Direction.DEBIT else suspect.amount
    return day.running - current + new_value == day.declared


def _reread_unreadable(pathname: Path, readings: list):
    """Reread, enlarged, each cell that didn't become a number.

    Here there is no day to check against: the file stopped before a running balance existed.
    So the reread goes in as a PROPOSAL (the UI can show what it read, for the person to check
    on paper), and the proof comes on resubmission, when the closing and the daily balance check
    judge the whole set.

    Measured on `sicoob-ib-2026-05-escaneado.pdf`: `--psm 7` read all four cells correctly
    (`236,50C`, `745,00D`, `528,34C`, `318,40D`) where the whole-page pass had produced no number
    at all.
    """
    from . import cell_reread
    from .layouts.sicoob_sisbr import parse_typed

    if not readings:
        return readings
    outgoing = []
    try:
        for reading in readings:
            proposal = None
            if reading.box:
                x0, top_edge, x1, base = reading.box
                for as_read in cell_reread.candidates(
                    pathname, cell_reread.Box(reading.page, x0, top_edge, x1, base)
                ):
                    cleaned = _strip_reread_noise(as_read)
                    if parse_typed(cleaned)[0] is not None:
                        proposal = cleaned
                        break
            outgoing.append(reading.model_copy(update={"reread": proposal}))
    finally:
        cell_reread.clear_cache()
    return outgoing


def _with_rereads(day, rereads: dict[str, str]):
    """Mark what the reread found, and move whoever was at the back to the front.

    A culprit found among the `rest` has to move up to the `suspects`: that is where the
    proposals come from (`_reread_proposals`), and it is what the UI shows. Leaving it at the
    back of the queue would mean finding the error and not using it.
    """

    def mark_reread(suspect):
        return suspect.model_copy(update={"reread": rereads.get(suspect.source)})

    promoted = [mark_reread(s) for s in day.rest if s.source in rereads]
    return day.model_copy(
        update={
            "suspects": [mark_reread(s) for s in day.suspects] + promoted,
            "rest": [s for s in day.rest if s.source not in rereads],
        }
    )


def _with_reread(pathname: Path, result: IngestionResult) -> IngestionResult:
    """Reread the cells the arithmetic pointed at, ONCE, on the final result.

    This used to live inside `_read`, and `_read` runs twice on a refused file (the second time
    at 400 dpi, trying again). The reread happened in both runs, with 42 tesseract calls where 21
    were enough, and half of them were thrown away along with the reading that didn't make it.

    Here it runs on what the person will see, and only on that.
    """
    if result.accepted:
        return result
    day_checks = list(result.day_checks)
    rereads = _reread_flagged_cells(pathname, day_checks)
    if rereads:
        day_checks = [_with_rereads(d, rereads) for d in day_checks]
    return result.model_copy(
        update={
            "unreadable_readings": _reread_unreadable(pathname, result.unreadable_readings),
            "day_checks": day_checks,
        }
    )
