from pathlib import Path
from typing import Iterable
import shutil

from pdf2image import convert_from_path


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
