from pathlib import Path
import shutil
from pdf2image import convert_from_path
from PyPDF2 import PdfReader
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    MofNCompleteColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from ..config import OUTPUT_DIR


def get_total_page_count(pdf_files: list[Path]) -> int:
    if isinstance(pdf_files, Path):
        pdf_files = [pdf_files]
    running_total = 0
    for pdf_file in pdf_files:
        with pdf_file.open("rb") as pdf:
            reader = PdfReader(pdf)
            running_total += len(reader.pages)
    return running_total


def pdf_to_png(pdf_path: Path, pages_dir: Path | None = None, progress=None, output_name: str | None = None) -> None:
    if pages_dir is None and output_name is not None:
        pages_dir = Path(OUTPUT_DIR, f"{output_name}-pages")
    elif pages_dir is None:
        pages_dir = Path(OUTPUT_DIR, f"{pdf_path.stem}-pages")

    images = convert_from_path(pdf_path, use_cropbox=False)

    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    pages_dir.mkdir(parents=True, exist_ok=True)

    if progress is None:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}", justify="left"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            expand=True,
        )
    elif not isinstance(progress, Progress):
        raise ValueError("progress must be a rich.progress.Progress instance")

    with progress:
        if output_name is not None:
            task = progress.add_task(
                f"Converting {pdf_path.name} to png", total=len(images)
            )
        else:
            task = progress.add_task(
                f"Converting {pdf_path.name} to png", total=len(images)
            )
        for i, image in enumerate(images):
            if output_name is not None:
                image.save(Path(pages_dir, f"{output_name}-{i}.png"), "png")
            else:
                image.save(Path(pages_dir, f"{pdf_path.stem}-{i}.png"), "png")
            progress.update(task, advance=1)
        progress.remove_task(task)
