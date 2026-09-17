"""Transfers between accounts of the same company (see ADR 6).

## The problem

Moving R$ 7,300 from Sicoob to Itaú produces **two** statement entries: money out of one account
and money in to the other. Classified as an expense and a revenue, they inflate both sides of the
income statement, although the money never left the company.

## The accounting treatment

The correct booking is a single one: `D destination bank / C source bank`. But the export batch
carries one record per entry, with **that entry's bank on one side** (a rule measured against the
real batch format). If each leg pointed at the other bank, the transfer would be booked twice.

The fix is a **transit account**, which is how the firm already works:

    out of Sicoob  ->  D transit / C Sicoob
    into Itaú      ->  D Itaú    / C transit
                       -------------------------
    net effect:        D Itaú    / C Sicoob   ✓  and the transit account nets to zero

Netting to zero is the **proof**: if the transit account doesn't close at `0.00` for the period,
a leg has no pair. Same spirit as the statement's arithmetic closing gate (ADR 1).

## Why nothing is paired automatically

Measured on the 7 real files: between **different companies** (Mariana Costa and a brake
distributor, with no relation at all), amount + date matched **20 times** by pure chance. Two
statements with hundreds of entries collide a lot. So:

1. A pair is only searched for **within the same company**, between different accounts.
2. Each candidate carries corroborating signals, and the screen shows its confidence.
3. **The accountant confirms.** Nothing is classified without a click.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import date
from decimal import Decimal

# Wording the banks themselves use for a transfer between accounts of the same owner.
TRANSFER_HINTS = ("TRANSF", "MESMA TIT", "MESMO TITULAR", "TED", "DOC ")
WINDOW_DAYS = 2


def _digits(string: str | None) -> str:
    return "".join(c for c in (string or "") if c.isdigit())


def _strip_accents(string: str | None) -> str:
    raw = unicodedata.normalize("NFKD", string or "")
    return "".join(c for c in raw if not unicodedata.combining(c)).upper()


def _tokens(label: str) -> set[str]:
    """Significant words of the company's legal name, to match against the context lines."""
    words = re.split(r"[^A-Z0-9]+", _strip_accents(label))
    return {p for p in words if len(p) > 3 and p not in {"LTDA", "EIRELI", "MEI"}}


def signs(line: dict, company_cnpj: str, company_name: str) -> list[str]:
    """Evidence that the entry is a transfer between the company's own accounts."""
    string = _strip_accents(f"{line['description']} {line.get('context') or ''}")
    findings = []
    if any(hint in string for hint in TRANSFER_HINTS):
        findings.append("transfer wording")
    # The company's own CNPJ showing up as the counterparty is the strongest signal: it only
    # happens when the company transfers money to itself.
    target = _digits(company_cnpj)
    if target and target in _digits(f"{line.get('counterparty')} {line.get('context')}"):
        findings.append("company's own CNPJ")
    own_tokens = _tokens(company_name)
    if own_tokens and len(own_tokens & _tokens(string)) >= 2:
        findings.append("company's legal name")
    return findings


def candidates(
    cx: sqlite3.Connection, company_id: int, period: str | None = None
) -> list[dict]:
    """Plausible (outgoing, incoming) pairs, from most to least confident.

    Greedy 1:1 pairing: an entry belongs to at most one pair. Pairs are sorted by confidence
    before matching, so that the best candidate takes a leg when two of them compete for it.
    """
    data = cx.execute(
        "SELECT name, head_office_cnpj FROM company WHERE id = ?", (company_id,)
    ).fetchone()
    if data is None:
        return []

    sql = (
        "SELECT l.*, ba.nickname AS account_nickname FROM entry l "
        "JOIN bank_account ba ON ba.id = l.bank_account_id "
        "WHERE l.company_id = ? AND l.category_id IS NULL"
    )
    query_params: list = [company_id]
    if period:
        sql += " AND l.period = ?"
        query_params.append(period)
    lines = [dict(l) for l in cx.execute(sql, query_params)]
    dismissed = {
        (l["outgoing_id"], l["incoming_id"])
        for l in cx.execute(
            "SELECT outgoing_id, incoming_id FROM dismissed_transfer WHERE company_id = ?",
            (company_id,),
        )
    }

    if len({l["bank_account_id"] for l in lines}) < 2:
        return []  # a single account: no internal transfer is possible

    outgoing_entries = [l for l in lines if l["direction"] == "debit"]
    incoming_entries = [l for l in lines if l["direction"] == "credit"]

    possible_pairs = []
    for outgoing in outgoing_entries:
        for incoming in incoming_entries:
            if outgoing["bank_account_id"] == incoming["bank_account_id"]:
                continue
            if Decimal(outgoing["amount"]) != Decimal(incoming["amount"]):
                continue
            days_apart = abs(
                (
                    date.fromisoformat(outgoing["posting_date"])
                    - date.fromisoformat(incoming["posting_date"])
                ).days
            )
            if days_apart > WINDOW_DAYS:
                continue
            evidence = sorted(
                set(signs(outgoing, data["head_office_cnpj"], data["name"]))
                | set(signs(incoming, data["head_office_cnpj"], data["name"]))
            )
            if (outgoing["id"], incoming["id"]) in dismissed:
                # The accountant already looked at this pair and said it isn't a transfer. It
                # leaves the panel and both legs go back to the normal queue, but each one is
                # still free to pair with ANOTHER leg, which may be the real match.
                continue
            if not evidence:
                # Equal amount and date, on their own, are NOT a pair. In a company with several
                # accounts that matches anything with the same amount in the month: a
                # `DEB.TITULO COBRANCA` of R$ 65.00 with a `PIX RECEBIDO` of R$ 65.00, or a
                # `TARIFA RENOVACAO LIMITE` fee with some other Pix.
                #
                # These pairs used to be shown flagged "low confidence, please check". That
                # didn't help: they cluttered the screen, couldn't be dismissed, and could be
                # reconciled by mistake together with the real ones, which is what happened in
                # the real-world test. A transfer between accounts of the SAME company always
                # leaves a trace (the CNPJ, the legal name or transfer wording); with no trace
                # at all, it is a coincidence of amounts.
                continue
            possible_pairs.append(
                {
                    "outgoing": outgoing,
                    "incoming": incoming,
                    "days": days_apart,
                    "signs": evidence,
                    "confidence": _confidence(evidence, days_apart),
                }
            )

    possible_pairs.sort(key=lambda p: (-p["confidence"], p["days"]))
    pairs, used = [], set()
    for pair in possible_pairs:
        keys = (pair["outgoing"]["id"], pair["incoming"]["id"])
        if keys[0] in used or keys[1] in used:
            continue
        used.update(keys)
        pairs.append(pair)
    return pairs


def dismiss(cx: sqlite3.Connection, company_id: int, outgoing_id: int, incoming_id: int) -> None:
    """Marks a pair as "not a transfer". Idempotent."""
    cx.execute(
        "INSERT OR IGNORE INTO dismissed_transfer (company_id, outgoing_id, incoming_id) "
        "VALUES (?, ?, ?)",
        (company_id, outgoing_id, incoming_id),
    )
    cx.commit()


def _confidence(evidence: list[str], day_checks: int) -> float:
    """0.0 to 1.0: what the screen uses to sort pairs and to flag doubtful ones.

    `day_checks` is the gap in days between the two legs. Matching amount and date, on their
    own, are worth little: that is how 20 pairs showed up between unrelated companies.
    """
    mark = 0.25 if day_checks == 0 else 0.10
    if "company's own CNPJ" in evidence:
        mark += 0.45
    if "transfer wording" in evidence:
        mark += 0.20
    if "company's legal name" in evidence:
        mark += 0.15
    return round(min(mark, 1.0), 2)


def reconcile(
    cx: sqlite3.Connection, ids: list[int], category_id: int, account: str
) -> int:
    """Classifies both legs with the SAME transit account.

    That is what makes the transit account net to zero: one leg debits it, the other credits it.
    """
    from . import repository as repo

    for identifier in ids:
        repo.classify(cx, identifier, category_id, account, None)
    return len(ids)


TRANSFER_CATEGORY = "TRANSFER BETWEEN ACCOUNTS"


def check_transit_account(
    cx: sqlite3.Connection, company_id: int, account: str, period: str | None = None
) -> dict:
    """The proof: whatever was reconciled as a transfer must close at `0.00`.

    **It filters by category, not by ledger account code.** If the accountant picks a transit
    account that another category already uses, summing by code would mix revenue with
    transfers: the proof would report a difference with no orphan leg or, worse, a revenue entry
    would offset a missing leg and the zero would appear by chance. Measured: with `1-9` serving
    both purposes, the proof reported R$ 42.00 that had nothing to do with any transfer.

    The `shared_account` field lists the other categories on the same code, because mixing
    transfers with income-statement items in one ledger account is questionable even when the
    proof is right.
    """
    sql = (
        "SELECT l.direction, l.amount FROM entry l "
        "JOIN category c ON c.id = l.category_id "
        "WHERE l.company_id = ? AND c.name = ?"
    )
    query_params: list = [company_id, TRANSFER_CATEGORY]
    if period:
        sql += " AND l.period = ?"
        query_params.append(period)

    total_debits = total_credits = Decimal("0.00")
    entry_count = 0
    for line in cx.execute(sql, query_params):
        entry_count += 1
        if line["direction"] == "debit":
            total_debits += Decimal(line["amount"])
        else:
            total_credits += Decimal(line["amount"])

    other_categories = [
        line["name"]
        for line in cx.execute(
            "SELECT name FROM category WHERE company_id = ? AND short_code = ? "
            "AND name <> ?",
            (company_id, account, TRANSFER_CATEGORY),
        )
    ]

    difference = total_debits - total_credits
    return {
        "account": account,
        "entries": entry_count,
        "total_debits": str(total_debits),
        "total_credits": str(total_credits),
        "difference": str(difference),
        "closes": difference == Decimal("0.00"),
        "shared_account": other_categories,
    }
