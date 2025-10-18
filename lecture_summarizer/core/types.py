from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal


class Kind(str, Enum):
    SLIDES = "slides"
    LECTURE = "lecture"
    DOCUMENT = "document"
    IMAGE = "image"
    VIDEO = "video"


class PdfMode(str, Enum):
    AUTO = "auto"
    IMAGES = "images"
    PDF = "pdf"


class SourceKind(str, Enum):
    LOCAL = "local"
    URL = "url"
    YOUTUBE = "youtube"
    DRIVE = "drive"


@dataclass(frozen=True)
class Asset:
    path: Path
    media: Literal["pdf", "image", "video", "audio"]
    page_index: int | None = None
    source_kind: SourceKind = SourceKind.LOCAL
    mime: str | None = None
    meta: dict | None = None


@dataclass(frozen=True)
class Job:
    source: str
    recursive: bool
    kind: Kind | None
    pdf_mode: PdfMode
    output_dir: Path | None
    model: str
    preset: str | None = None
    export: list[str] | None = None
    skip_existing: bool = True
