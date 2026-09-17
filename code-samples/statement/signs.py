"""Table-driven sign normalizer (see ADR 2).

It always returns a signed amount. It never assumes credit: text that no convention recognizes
becomes `UndeterminedSign`, which the pipeline turns into a refusal. Assuming a direction is how
money silently changes sides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml

from .money import to_decimal
from .models import Direction, LineKind

TABLE = Path(__file__).with_name("signs.yaml")


class UndeterminedSign(ValueError):
    """No convention recognized the amount's text."""


@dataclass(frozen=True, slots=True)
class Convention:
    id: str
    caption: str
    regex: re.Pattern | None
    mapping: dict[str, str]
    mapping_by_sign: dict[str, str]
    fixed_direction: str | None
    sign_in_separate_token: bool
    in_use: bool
    real_examples: tuple


@dataclass(frozen=True, slots=True)
class NormalizedAmount:
    amount: Decimal
    kind: LineKind
    direction: Direction | None
    convention: str

    @property
    def signed(self) -> Decimal:
        if self.direction is Direction.DEBIT:
            return -self.amount
        return self.amount


@lru_cache(maxsize=1)
def load_conventions(pathname: str | None = None) -> tuple[Convention, ...]:
    data = yaml.safe_load(Path(pathname or TABLE).read_text(encoding="utf-8"))
    conventions = []
    for raw in data["conventions"]:
        conventions.append(
            Convention(
                id=raw["id"],
                caption=raw.get("caption", ""),
                regex=re.compile(raw["regex"]) if raw.get("regex") else None,
                mapping=raw.get("mapping", {}),
                mapping_by_sign=raw.get("mapping_by_sign", {}),
                fixed_direction=raw.get("fixed_direction"),
                sign_in_separate_token=bool(raw.get("sign_in_separate_token")),
                in_use=bool(raw.get("in_use")),
                real_examples=tuple(raw.get("real_examples", [])),
            )
        )
    return tuple(conventions)


def _to_kind_and_direction(tag: str) -> tuple[LineKind, Direction | None]:
    if tag == "non_posting":
        return LineKind.NON_POSTING, None
    if tag == "credit":
        return LineKind.ENTRY, Direction.CREDIT
    if tag == "debit":
        return LineKind.ENTRY, Direction.DEBIT
    raise UndeterminedSign(f"unknown direction tag in the table: {tag!r}")


def normalize_amount(
    string: str,
    separate_sign: str | None = None,
    allowed: frozenset[str] | None = None,
) -> NormalizedAmount:
    """Apply the table to the amount's text.

    `separate_sign` covers the convention where the marker is a separate token, resolved by
    geometry before reaching this function.

    `allowed` restricts the conventions to the ones the layout declares. Without that
    restriction, an amount without a suffix from a Sicoob statement would fall into
    `plain_negative` and be read as a CREDIT: the silent failure this module exists to prevent.
    A layout that declares nothing accepts the whole table.
    """
    cleaned = string.strip()
    for convention in load_conventions():
        if convention.regex is None:
            continue
        if allowed is not None and convention.id not in allowed:
            continue
        matched = convention.regex.match(cleaned)
        if matched is None:
            continue
        groups = matched.groupdict()
        amount = to_decimal(groups["amount"])

        if convention.sign_in_separate_token:
            if separate_sign is None:
                continue
            tag = convention.mapping.get(separate_sign.strip())
            if tag is None:
                raise UndeterminedSign(
                    f"unknown marker {separate_sign!r} for amount {string!r}"
                )
        elif convention.fixed_direction:
            tag = convention.fixed_direction
        elif "sign" in groups and convention.mapping:
            tag = convention.mapping.get(groups["sign"])
            if tag is None:
                raise UndeterminedSign(
                    f"unknown marker {groups['sign']!r} in {string!r}"
                )
        elif "sign" in groups and convention.mapping_by_sign:
            tag = convention.mapping_by_sign.get(groups["sign"])
            if tag is None:
                raise UndeterminedSign(f"unmapped sign in {string!r}")
        else:
            continue

        kind, direction = _to_kind_and_direction(tag)
        return NormalizedAmount(
            amount=amount, kind=kind, direction=direction, convention=convention.id
        )

    raise UndeterminedSign(
        f"no sign convention recognizes {string!r}"
        + (f" with marker {separate_sign!r}" if separate_sign else "")
    )
