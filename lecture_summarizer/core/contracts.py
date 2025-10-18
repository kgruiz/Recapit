from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .types import Asset, Kind, PdfMode, Job


class Ingestor(Protocol):
    def discover(self, job: Job) -> list[Asset]:
        ...


class Normalizer(Protocol):
    def normalize(self, assets: list[Asset], pdf_mode: PdfMode) -> list[Asset]:
        ...


class PromptStrategy(Protocol):
    kind: Kind

    def preamble(self) -> str:
        ...

    def instruction(self, preamble: str) -> str:
        ...


class Provider(Protocol):
    def supports(self, capability: str) -> bool:
        ...

    def transcribe(self, *, instruction: str, assets: list[Asset], modality: str, meta: dict) -> str:
        ...


class Writer(Protocol):
    def write_latex(self, *, base: Path, name: str, preamble: str, body: str) -> Path:
        ...
