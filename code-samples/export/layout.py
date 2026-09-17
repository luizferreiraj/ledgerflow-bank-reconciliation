"""Positional writing driven by a table (see ADR 8).

The layout was reverse-engineered from a single file. Keeping the offsets in YAML is what lets us
fix it when a second batch turns up, without hunting for f-strings scattered through the code, and
it is what makes every field auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

import yaml

TABLE = Path(__file__).with_name("layout.yaml")


class InvalidLayout(Exception):
    """A programming or spec defect, never a data problem."""


@dataclass(frozen=True, slots=True)
class Field:
    label: str
    offset: int
    size: int
    kind: str
    value: str | None = None

    @property
    def end(self) -> int:
        return self.offset + self.size


@dataclass(frozen=True, slots=True)
class Record:
    label: str
    width: int
    fields: tuple[Field, ...]


@dataclass(frozen=True, slots=True)
class Layout:
    encoding: str
    newline: str
    filename_template: str
    records: dict[str, Record]

    def filename_for(self, cnpj: str) -> str:
        return self.filename_template.format(cnpj=cnpj)


@lru_cache(maxsize=1)
def load(pathname: str | None = None) -> Layout:
    data = yaml.safe_load(Path(pathname or TABLE).read_text(encoding="utf-8"))
    records = {}
    for label, raw in data["records"].items():
        fields = tuple(
            Field(
                label=c["label"],
                offset=int(c["offset"]),
                size=int(c["size"]),
                kind=c["kind"],
                value=c.get("value"),
            )
            for c in raw["fields"]
        )
        record = Record(label=label, width=int(raw["width"]), fields=fields)
        _check(record)
        records[label] = record
    file = data["file"]
    return Layout(
        encoding=file["encoding"],
        newline=file["newline"],
        filename_template=file["label"],
        records=records,
    )


def _check(record: Record) -> None:
    """A field that overlaps or runs past the record width is a spec error, not a data error."""
    previous_end = 0
    for field in sorted(record.fields, key=lambda c: c.offset):
        if field.offset < previous_end:
            raise InvalidLayout(
                f"{record.label}.{field.label}: offset {field.offset} overlaps "
                f"the previous field, which ends at {previous_end}"
            )
        if field.end > record.width:
            raise InvalidLayout(
                f"{record.label}.{field.label}: ends at {field.end}, "
                f"past the record width {record.width}"
            )
        if field.kind == "constant":
            if field.value is None or len(field.value) != field.size:
                raise InvalidLayout(
                    f"{record.label}.{field.label}: constant has the wrong size"
                )
        previous_end = field.end


def format_field(field: Field, value) -> str:
    if field.kind == "constant":
        return field.value or ""
    if value is None:
        return " " * field.size
    if field.kind == "numeric":
        string = str(value).strip()
        if not string.isdigit():
            raise InvalidLayout(f"{field.label}: {value!r} is not numeric")
        if len(string) > field.size:
            raise InvalidLayout(
                f"{field.label}: {string!r} does not fit in {field.size} positions"
            )
        return string.rjust(field.size, "0")
    if field.kind == "amount_cents":
        cents = int(
            (Decimal(value).copy_abs() * 100).to_integral_value(rounding=ROUND_HALF_UP)
        )
        string = str(cents)
        if len(string) > field.size:
            raise InvalidLayout(f"{field.label}: value {value} does not fit in the field")
        return string.rjust(field.size, "0")
    if field.kind == "date_ddmmyyyy":
        if not isinstance(value, date):
            raise InvalidLayout(f"{field.label}: expected a date, got {type(value).__name__}")
        return value.strftime("%d%m%Y")
    string = str(value)
    if len(string) > field.size:
        raise InvalidLayout(
            f"{field.label}: {string!r} is {len(string)} characters long, "
            f"the field holds {field.size}"
        )
    return string.ljust(field.size)


def assemble(record: Record, values: dict) -> str:
    """Fills the line by position; anything not declared stays a space."""
    line = [" "] * record.width
    for field in record.fields:
        string = format_field(field, values.get(field.label))
        if len(string) != field.size:
            raise InvalidLayout(
                f"{field.label}: formatted to {len(string)} characters, expected {field.size}"
            )
        line[field.offset : field.end] = list(string)
    return "".join(line)
