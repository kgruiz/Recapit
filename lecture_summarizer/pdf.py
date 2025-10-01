from pathlib import Path
from typing import Iterable
import shutil

from pdf2image import convert_from_path


SLIDE_KEYWORDS = ("slide", "deck", "presentation", "keynote", "pitch")
LECTURE_KEYWORDS = ("lecture", "lesson", "class", "seminar", "notes")


def pdf_to_png(pdf_path: Path, out_dir: Path, *, prefix: str | None = None) -> list[Path]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    images = convert_from_path(pdf_path, use_cropbox=False)
    result: list[Path] = []
    for i, im in enumerate(images):
        name = f"{(prefix or pdf_path.stem)}-{i}.png"
        p = out_dir / name
        im.save(p, "png")
        result.append(p)
    return result


def total_pages(pdf_paths: Iterable[Path]) -> int:
    from PyPDF2 import PdfReader

    total = 0
    for p in pdf_paths:
        with p.open("rb") as f:
            total += len(PdfReader(f).pages)
    return total


def guess_pdf_kind(pdf_path: Path) -> str:
    """Heuristically classify a PDF as slides, lecture notes, or a document."""
    from PyPDF2 import PdfReader

    name = pdf_path.stem.lower()
    if any(word in name for word in SLIDE_KEYWORDS):
        return "slides"
    if any(word in name for word in LECTURE_KEYWORDS):
        return "lecture"

    try:
        with pdf_path.open("rb") as handle:
            reader = PdfReader(handle)
            if not reader.pages:
                return "document"
            first = reader.pages[0]
            width = float(getattr(first.mediabox, "width", 1) or 1)
            height = float(getattr(first.mediabox, "height", 1) or 1)
            ratio = width / height if height else 1.0
            page_count = len(reader.pages)
    except Exception:
        # Any parsing failure falls back to generic document handling.
        return "document"

    if ratio >= 1.3 or (page_count <= 5 and ratio >= 1.2):
        return "slides"

    if any(word in name for word in ("hw", "assignment", "worksheet", "problem")):
        return "lecture"

    return "document"
