"""Match the file's textual signature against a registered layout (see ADR 2).

A file that matches nothing is refused with the observed signature in the diagnostic, which is
exactly the input needed to register the new layout.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .geometry import bands_by_y, band_text
from .models import Diagnostic, Word

TABLE = Path(__file__).with_name("signatures.yaml")


class UnknownLayout(Exception):
    def __init__(self, diagnostic: Diagnostic):
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


@dataclass(frozen=True, slots=True)
class Layout:
    id: str
    label: str
    required: tuple[str, ...]
    forbidden: tuple[str, ...]
    strategy: str
    conventions: frozenset[str]
    geometry: dict
    region: dict
    markers: dict
    known_files: tuple[str, ...]

    @property
    def columns(self) -> dict[str, tuple[float, float]]:
        return {
            label: (float(span[0]), float(span[1]))
            for label, span in self.geometry.get("columns", {}).items()
        }


def strip_accents(string: str) -> str:
    normalized = unicodedata.normalize("NFKD", string)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def normalize_for_signature(string: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(string)).strip()


@lru_cache(maxsize=1)
def load_layouts(pathname: str | None = None) -> tuple[Layout, ...]:
    data = yaml.safe_load(Path(pathname or TABLE).read_text(encoding="utf-8"))
    return tuple(
        Layout(
            id=raw["id"],
            label=raw["label"],
            required=tuple(raw.get("required", ())),
            forbidden=tuple(raw.get("forbidden", ())),
            strategy=raw["strategy"],
            conventions=frozenset(raw.get("conventions", ())),
            geometry=raw.get("geometry", {}),
            region=raw.get("region", {}),
            markers=raw.get("markers", {}),
            known_files=tuple(raw.get("known_files", ())),
        )
        for raw in data["layouts"]
    )


def observed_signature(pages: list[list[Word]], lines: int = 25) -> str:
    """Normalized text of the first lines of the first page."""
    if not pages or not pages[0]:
        return ""
    bands = bands_by_y(pages[0], tol=2.0)[:lines]
    return normalize_for_signature(" \n ".join(band_text(b) for b in bands))


def route(pages: list[list[Word]]) -> Layout:
    signature = observed_signature(pages)
    for layout in load_layouts():
        if any(forbidden_phrase in signature for forbidden_phrase in layout.forbidden):
            continue
        if all(required_phrase in signature for required_phrase in layout.required):
            return layout
    raise UnknownLayout(
        Diagnostic(
            code="UNKNOWN_LAYOUT",
            message=(
                "no registered signature matches this file. "
                "The observed signature is in the details; use it to register "
                "the layout."
            ),
            details={"observed_signature": signature[:600]},
        )
    )
