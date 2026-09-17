"""Excerpt of the classification-suggestions module, showing how the suggestion key is built; the
vote counting (`suggestion_map`, `SuggestionMap`), automatic classification at confidence 1.0
and the per-entry lookup are left out.

The workbench suggests a category from what the accountant has already classified (see ADR 5).

**This is not a rules engine.** The difference matters and is deliberate:

- A rules engine (planned) has versioned rules, with an author, an effective date and a list of
  affected entries whenever a rule changes.
- This is only a memory of the company's own history: "you've classified this the same way N
  times". Nothing is applied on its own: the suggestion is pre-filled on screen and **only counts
  once the accountant confirms it**.

## The key depends on the direction

**Payment (debit):** the description alone is not enough. `PIX EMIT.OUTRA IF` is the same text for
a supplier payment, a partner's profit withdrawal and a service bought; what tells them apart is
**who received the money**. The key is the pair:

    (description signature, counterparty)

**Receipt (credit):** incoming money goes to the same account whoever paid; a customer is a
customer. Splitting `PIX RECEB.OUTRA IF` by CPF would create one decision per payer, none of them
would ever repeat, and the suggestion would be useless exactly where it saves the most work. The
key is the description alone and, when the bank glues the payer's name into the description, the
operation **kind** in its place (see `operation_kinds`).

This is the firm's rule, confirmed on 2026-07-29. If some receipt needs its own account, the
accountant changes that entry: a suggestion is only a suggestion, and a wrong one costs a click,
not a wrong entry.

When the entry has no counterparty (a bank fee, a toll, a collection credit), the key is the
description alone, in either direction.

## Suggestions learn classifications, never text

The description comes from the **statement** and belongs to the bank. If the accountant rewrites
one (`DUPLICATA 4471` instead of what the bank sent), the change applies to that entry and to no
other: it is a decision about that document, not a rule. Suggesting the edited text for the next
similar entry would propose an invoice number that isn't that entry's, wrong in a way that is
hard to notice because the field looks filled in.

What is learned is **category and ledger account**. The text only changes when the accountant
says so, one entry at a time.

## Why a signature and not the raw text

The bank's description carries a document number and a payee name that change on every entry
(`PIX RECEB 4837`). Comparing raw text would match almost nothing. The signature strips digits
and punctuation and keeps the words, which is what repeats.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata

NON_WORD = re.compile(r"[^A-Z ]+")
WHITESPACE = re.compile(r"\s+")
# Words that show up in almost every description and don't help tell entries apart.
STOPWORDS = {"DE", "DA", "DO", "DAS", "DOS", "E", "P", "PARA", "REF", "LTDA", "ME", "EIRELI"}


def context_lines(context: list[str] | str | None) -> list[str]:
    """The context as a list of lines, whatever form it arrives in.

    The database stores `context` as JSON in a TEXT column. Code that reads it through SQL gets
    the raw string (`'["04/05 07:43 PadariaBomGosto"]'`); the screen calls `json.loads` before
    building the suggestion. Without normalizing, the two produce different keys for the SAME
    entry::

        learning (raw)     ->  16␞COMPRA COM CARTAO␟PADARIABOMGOSTO"]
        screen   (parsed)  ->  16␞COMPRA COM CARTAO␟PADARIABOMGOSTO

    The `"]` left over from the JSON syntax ends up in the descriptor and never matches. Measured
    on the firm's database: 1,327 of 7,042 entries (18%), in EVERY account. For the company banking
    at Banco do Brasil that meant 209 pending entries with zero suggestions on screen, while
    automatic classification (which reads through SQL on both sides) kept working. A silent screen
    and a batch job that classifies on its own: the worst possible combination, because one side
    looked right.

    The canonical form is the list. There is nothing to migrate: no key is persisted, they are all
    recomputed on every read.
    """
    if not context:
        return []
    if isinstance(context, list):
        return context
    string = context.strip()
    if string.startswith("["):
        try:
            as_read = json.loads(string)
        except ValueError:
            return [context]
        if isinstance(as_read, list):
            return [str(line) for line in as_read]
    return [context]


def _strip_accents(string: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", string or "") if not unicodedata.combining(c)
    )


def _tokens(string: str) -> list[str]:
    cleaned = NON_WORD.sub(" ", _strip_accents(string).upper())
    return [p for p in WHITESPACE.split(cleaned) if p and p not in STOPWORDS and len(p) > 1]


def signature(description: str, context: list[str] | str | None = None) -> str:
    """`PIX RECEB.OUTRA IF 4837` and `PIX RECEB.OUTRA IF 991` → the same signature.

    ## The payee's name inside the description

    Itaú writes `PIX QR CODE RECEBIDO MARIA APARECIDA 11/05`: the payer's name goes **into the
    text itself**. Left untreated, 87 receipts become 77 signatures and the suggestion is useless.

    The fix is not to shorten the signature. Cutting it at 4 words would merge
    `PIX EMITIDO OUTRA IF` with `PIX EMITIDO OUTRA IF - MESMA TIT.`, and the second one is a
    **transfer between the company's own accounts**, the opposite of an ordinary payment.

    What happens here is surgical: the words that appear in the counterparty's name (which the
    parser keeps in `context`) are removed from the description. What's left is
    `PIX QR CODE RECEBIDO`, the same for everyone, and nothing else gets truncated.
    """
    words = _tokens(description)
    if context:
        raw = " ".join(context_lines(context))
        name_words = set(_tokens(raw))
        # Always keep the first two words: they are the operation kind (`PIX RECEB`,
        # `BOLETO PAGO`) and must never disappear, even if they happen to match the payee's name.
        words = words[:2] + [p for p in words[2:] if not _in_name(p, name_words)]
    return " ".join(words[:5])


def _in_name(word: str, name_words: set[str]) -> bool:
    """Is this word part of the counterparty's name?

    It compares by **prefix** because the bank truncates the name to fit the field width:
    `JOAO PEREI30/06` in the description against `JOAO PEREIRA SANTOS` in the context. Requiring
    equality would let `PEREI` through, and every payee would still be a decision of its own.
    """
    return any(label.startswith(word) or word.startswith(label) for label in name_words)


PAYEE = re.compile(
    r"\b(?:FAV|FAVORECIDO|BENEF|BENEFICIARIO)\.?:?\s*(?P<label>[^\d]{4,60})", re.IGNORECASE
)


# The bank's own reference, not anyone's name: `Doc.: IOF/4-5`, `Doc.: 12345678`. A bare
# document number (masked or not) matches too; it was already handled earlier as the
# counterparty.
BANK_REFERENCE = re.compile(r"^(?:Doc\.?:|[\d.\-*/ ]+$)", re.IGNORECASE)


def payee(description: str, context: list[str] | str | None) -> str | None:
    """Who RECEIVED the money, when the bank prints the name but not the document number.

    Two forms, both measured at the firm:

    **With a marker.** `FAV.: MARIANA COSTA SILVA` versus `FAV.: GRAFICA IMPRIME BEM LTDA`:
    different payments with the same description.

    **Without a marker.** On a bill payment (boleto), Sicoob prints only the name, on a line of
    its own::

        DEB.TIT.COMPE EFETIVADO   312,00    Alfaprint
        DEB.TIT.COMPE EFETIVADO 1.164,37    Betafoods

    These are different suppliers and must not share a classification. When there is NOTHING
    describing the other side (`DEB.TIT.COMPE.EFETI` alone, or only the reference
    `Doc.: 12345678`), then they do group together, which is what the accountant expects.
    """
    lines = context_lines(context)
    raw = " ".join(lines)
    matched = PAYEE.search(f"{description} {raw}")
    if matched is None:
        return None
    label = " ".join(_strip_accents(matched.group("label")).upper().split())
    # The bank's boilerplate comes after the name (`Transferencia Pix <company>`). Cutting there
    # keeps the key stable: otherwise `FAV.: FULANO Transferencia Pix ...` and
    # `FAV.: FULANO Pagamento ...` became different counterparties, and the same supplier never
    # grouped.
    for marker in (" TRANSFERENCIA", " TRANSF", " PAGAMENTO", " PIX "):
        position = label.find(marker)
        if position > 0:
            label = label[:position]
    return label.strip() or None


def context_descriptor(context: list[str] | str | None) -> str | None:
    """What's left of the context after removing the bank's own references.

    It is the last resort, for when the bank neither labels the payee nor prints a document
    number. On a Sicoob bill payment the supplier comes on a line of its own:
    `DEB.TIT.COMPE EFETIVADO / Alfaprint`. `Doc.: 12345678`, on the other hand, is a reference,
    not anyone's name.
    """
    lines = context_lines(context)
    leftover = " ".join(
        line.strip()
        for line in lines
        if line.strip() and not BANK_REFERENCE.match(line.strip())
    )
    # The NUMBERS here are operation references, not anyone's identity. Sicoob prints a
    # utility-bill agreement as `AURORA 20260512000012345678 12345`, and the number changes on
    # every collection: keeping it, the same supplier became a new case every month and the
    # suggestion never caught on. What's left is `AURORA`, which is the name.
    #
    # The cutoff is 4 digits so that numbers that are part of a name survive
    # (`POSTO 24 HORAS`, `LOJA 3`).
    words = [
        word
        for word in _strip_accents(leftover).upper().split()
        if len(re.sub(r"\D", "", word)) < 4
    ]
    return " ".join(words) or None


def useful_counterparty(
    counterparty: str | None,
    description: str,
    context: list[str] | str | None,
    company_cnpj: str | None,
) -> str | None:
    """Who is on the other side, or `None` when there is no other side.

    Order matters, and it is: **whoever the bank SAYS received the money comes before any number
    found in the text**.

    1. **`FAV.:` with a name.** Sicoob prints a Pix like this::

           FAV.: MARIA APARECIDA LIMA
           Transferencia Pix MARIANA COSTA DOCERIA LTDA 44.444.444 0001-92

       The CNPJ there belongs to WHOEVER PAID, and it is the same on every payment from that
       account. Taken as the counterparty, ALL those payments share one key and one
       classification contaminates the others: that is how `LUCROS ACUMULADOS` (retained
       earnings), correct for the partner, showed up on a payment for financial advice and on
       one for eggs.

       An earlier attempt discarded that number **when it was the company's own CNPJ**, and it
       didn't work, because it isn't: the account belongs to `MARIANA COSTA`
       (33.333.333/0001-92) and the text shows `MARIANA COSTA DOCERIA LTDA`
       (44.444.444/0001-92), a different legal entity. The payer can't be recognized from the
       company records; what can be trusted is the `FAV.:` label, which the bank itself wrote to
       say who received the money.

    2. **The document number, when there is no `FAV.:`.** `PIX EMITIDO OUTRA IF / Pagamento Pix
       ***.345.345-** nutricionista`: here the masked CPF IS the recipient's. The company's own
       CNPJ is still discarded; it costs nothing and covers the case where it does appear.

    3. **What's left of the context.** On a bill payment the name comes on a line of its own:
       `DEB.TIT.COMPE EFETIVADO / Alfaprint`. It comes last because it is the noisiest: it drags
       in free text (`nutricionista`), which varies between payments to the same supplier and
       would turn each one into a separate case.

    4. **Nothing** (`DEB.IOF`, `TARIFA`, `JUROS`): there is no other side, it's the bank's own
       charge. It groups by description, which is what the accountant expects.
    """
    from .repository import digits_only

    by_name = payee(description, context)
    if by_name:
        return by_name
    digits = digits_only(counterparty or "")
    if digits and digits != digits_only(company_cnpj or ""):
        return (counterparty or "").strip() or None
    return context_descriptor(context)


# How many different continuations a prefix may have and still be an operation kind.
#
# A kind continues in only a few ways: after `Reembolso` comes `Envio` or `Reclamacoes`. A NAME
# continues in dozens: after `Pagamento com Codigo QR Pix` come PAULO, ANTONIO, MARIA,
# CLAUDIA... The text fans out like that right where the kind ends.
#
# Counting repetitions didn't work: with a low threshold a repeated first name became a kind
# (`... QR Pix PAULO`), and with a high one legitimate subtypes collapsed together
# (`Reembolso Envio cancelado` with `Reembolso Reclamacoes`), which is exactly what the firm
# keeps apart.
MAX_BRANCHING = 4


def operation_kinds(descriptions) -> dict[str, str]:
    """Description -> operation KIND, discovered from the statement itself.

    Some banks glue the payer's name to the end of the description with no delimiter at all.
    Mercado Pago writes `Reembolso Envio cancelado a Ana Paula Ferreira Rocha`,
    `... a LUCIANA MARTINS DA SILVA`. Without splitting them, every receipt becomes a decision:
    measured, 1,475 receipts across 304 distinct descriptions.

    What separates kind from name is REPETITION: the kind repeats, the name doesn't. So the kind
    of a description is the **longest prefix it shares with some other, different description**.
    There is no list of kinds to maintain: if the bank introduces a new one, it shows up on its
    own as soon as there are two entries of it.

    The count covers the WHOLE statement, not neighbours in alphabetical order. Counting by
    neighbour, `Dinheiro recebido` and `Dinheiro retido` (money received and money withheld,
    opposite operations) collapsed into `Dinheiro`.

    `Liberacao de dinheiro`, which repeats in full, is its own kind.

    Only for RECEIPTS. On a payment the recipient's name IS the identity: merging `Pix FULANO`
    with `Pix BELTRANO` would put different expenses into the same account.

    **`descriptions` is the statement of ONE account.** See `kinds_by_account`: mixing banks in
    the same count breaks legitimate kinds.
    """
    distinct = {" ".join(h.split()) for h in descriptions if h}
    next_words: dict[str, set[str]] = {}
    for string in distinct:
        terms = string.split()
        for prefix_len in range(len(terms)):
            next_words.setdefault(" ".join(terms[:prefix_len]), set()).add(terms[prefix_len])

    mapping: dict[str, str] = {}
    for string in distinct:
        terms = string.split()
        cut = len(terms)
        # Start at the FIRST word: the empty prefix branches into every description in the
        # statement and would cut everything down to nothing.
        for prefix_len in range(1, len(terms)):
            if len(next_words.get(" ".join(terms[:prefix_len]), ())) > MAX_BRANCHING:
                cut = prefix_len
                break
        mapping[string] = " ".join(terms[:cut]) or string
    return mapping


def kinds_by_account(cx: sqlite3.Connection, company_id: int) -> dict[int, dict[str, str]]:
    """Bank account → (description → kind). **One count per account, never per company.**

    The kind comes from repetition within one statement, and each bank writes in its own style.
    Counting the whole company puts both styles into the same prefix tree, and a prefix that
    belonged to one bank starts branching through the other; the cut lands too early and takes
    with it what told the operations apart. Measured on the firm's database, counting per company
    instead of per account:

        CR COMPRAS CABAL DEBITO  ─┐
        CR COMPRAS VISA ELECTRON ─┼→ `CR COMPRAS`   (Sicoob: card brands the
        CR COMPRAS MAESTRO       ─┘                  accountant books separately)

        Pix recebido: "Cp :182…"      ─┬→ `Pix`     (Inter: a customer receipt and
        Pix enviado devolvido: "Cp :…"─┘             a returned outgoing payment)

    The second one is the serious case, and having the bank account in the key doesn't save it:
    both come from the SAME Inter statement, and they only branch together because Mercado Pago's
    style (`Pix FULANO`, `Pix recebido FULANO`) entered the count. A customer receipt and a
    returned payment under one key: the suggestion would start proposing revenue for refunds.
    Counting per account, both stay whole.

    The corpus is EVERY receipt of the account, including those **not yet classified**: a pending
    entry must land in the same kind as the ones already decided. Since the corpus grows with each
    import, a description's kind can get shorter later on; that's why learning and lookup use the
    SAME map (see `SuggestionMap`), not two maps taken at different times.
    """
    by_account: dict[int, list[str]] = {}
    for line in cx.execute(
        "SELECT bank_account_id, description FROM entry "
        "WHERE company_id = ? AND direction = 'credit'",
        (company_id,),
    ):
        by_account.setdefault(line["bank_account_id"], []).append(line["description"])
    return {account: operation_kinds(descriptions) for account, descriptions in by_account.items()}


def _by_kind(
    description: str, bank_account_id: int, kinds: dict[int, dict[str, str]] | None
) -> str:
    """The description's kind, or the description itself when there is no map or no entry for it.

    A description outside the corpus (a statement just read, a test) falls back to its own text:
    with no known kind, the behaviour is the previous one, the raw signature.
    """
    if not kinds:
        return description
    return kinds.get(bank_account_id, {}).get(" ".join((description or "").split()), description)


def lookup_key(
    description: str,
    counterparty: str | None,
    direction: str | None,
    context: list[str] | str | None,
    bank_account_id: int,
    company_cnpj: str | None = None,
    kinds: dict[int, dict[str, str]] | None = None,
) -> str:
    """The suggestion key: BANK ACCOUNT, DIRECTION, description, and the counterparty **only for
    payments**.

    The account is part of the key because the same phrase means different things at different
    banks. Measured at the firm: `PIX RECEBIDO - OUTRA IF` on the BRANCH's account was classified
    as `CAIXA FILIAL` (branch cash), and the rule leaked into the head office's two accounts,
    which started booking head-office receipts into the branch's cash account.

    ## Direction too, because money in is not money out

    The same label can appear in both directions. At Caixa, `COB COMPE` is both the collection
    received AND the fee the bank charges for it::

        05/05  COB COMPE  doc 300426   385,00 C   <- the customer paid
        05/05  COB COMPE  doc 040526     4,75 D   <- the collection fee

    Those go to different ledger accounts: revenue on one side, bank expenses on the other.
    Without the direction in the key, both fell into the same bucket and automatic classification
    applied `CAIXA GERAL` to all sixteen entries.

    The collision only shows up when the payment has no counterparty, because the counterparty is
    what separates debits from one another, and the bank's own charges (`TARIFA`, `IOF`,
    `COB COMPE`) have none. Measured on the firm's database: out of 1,291 keys, exactly ONE mixed
    both directions, and it was this one.

    The rest of the system already treated direction as part of the identity: `similar` and
    `identical_pending` filter on it in SQL. Only the key didn't.

    Each statement now gets its own rule. The only thing that crosses accounts is a transfer
    between the company's own accounts, and transfers aren't learned at all (see
    `suggestion_map` and ADR 6).

    The CPF comes masked from the bank (`***.123.123-**`). The visible digits are stable for the
    same person, which is enough to recognize a repeat. Two distinct CPFs with the same middle
    digits would collide; that is why the result is a **suggestion**, confirmed by the
    accountant, and not a classification.

    ## `kinds` only applies to receipts

    When the bank glues the payer's name to the end of the description with no delimiter
    (`Reembolso Envio cancelado a FULANO`), the receipt is grouped by operation KIND instead of
    the full text; `operation_kinds` discovers the kind from the statement itself. It doesn't
    apply to payments: there the recipient's name IS the identity, and grouping by kind would put
    different suppliers into the same account.

    Without the map (`kinds=None`) the key is the old one. That is graceful degradation, not an
    error: a caller that builds the key without the map finds no suggestion for receipts with a
    glued-on name. That's why the map travels with the suggestions it indexed, in
    `SuggestionMap`, instead of being rebuilt by every caller.
    """
    # `bank_account_id` is required on purpose: with a default value, a caller that forgot it
    # would build a key without the account, find no suggestion at all, and the screen would go
    # silent, with no suggestion and no error.
    where = f"{bank_account_id}␞{direction or '?'}"
    if direction == "credit":
        # The text is swapped for its kind only in this branch. Below, for payments, the
        # description stays raw, including for `useful_counterparty`, which looks for `FAV.:`
        # inside it.
        kind = _by_kind(description, bank_account_id, kinds)
        return f"{where}␞{signature(kind, context)}"
    base = f"{where}␞{signature(description, context)}"
    target = useful_counterparty(counterparty, description, context, company_cnpj)
    if not target:
        # No document number AND no payee: there is no other side. It's a bank charge
        # (`DEB.IOF`, `TARIFA`, `JUROS`), and grouping by description is the right call.
        return base
    return f"{base}␟{target}"
