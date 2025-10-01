from pathlib import Path
from typing import Optional

import typer

from .api import (
    TranscribeSlides,
    TranscribeLectures,
    TranscribeDocuments,
    TranscribeImages,
    LatexToMarkdown,
    LatexToJson,
)
from .pipeline import PDFMode


app = typer.Typer(add_completion=False)


@app.command()
def slides(
    source: Path,
    output_dir: Path | None = None,
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override the default model"),
    exclude: str = "",
    pattern: str = r".*(\d+).*",
    skip_existing: bool = True,
    pdf_mode: PDFMode = typer.Option(PDFMode.IMAGES, "--pdf-mode", case_sensitive=False, help="How to feed PDFs: images, pdf, or auto"),
):
    ex = [int(x) for x in exclude.split(",") if x.strip()] if exclude else []
    TranscribeSlides(
        source,
        outputDir=output_dir,
        lectureNumPattern=pattern,
        excludeLectureNums=ex,
        skipExisting=skip_existing,
        model=model,
        pdfMode=pdf_mode,
    )


@app.command()
def lectures(
    source: Path,
    output_dir: Path | None = None,
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override the default model"),
    exclude: str = "",
    pattern: str = r".*(\d+).*",
    skip_existing: bool = True,
    pdf_mode: PDFMode = typer.Option(PDFMode.IMAGES, "--pdf-mode", case_sensitive=False, help="How to feed PDFs: images, pdf, or auto"),
):
    ex = [int(x) for x in exclude.split(",") if x.strip()] if exclude else []
    TranscribeLectures(
        source,
        outputDir=output_dir,
        lectureNumPattern=pattern,
        excludeLectureNums=ex,
        skipExisting=skip_existing,
        model=model,
        pdfMode=pdf_mode,
    )


@app.command()
def documents(
    source: Path,
    output_dir: Path | None = None,
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override the default model"),
    recursive: bool = False,
    skip_existing: bool = True,
    output_name: str | None = None,
    pdf_mode: PDFMode = typer.Option(PDFMode.AUTO, "--pdf-mode", case_sensitive=False, help="How to feed PDFs: images, pdf, or auto"),
):
    TranscribeDocuments(
        source,
        outputDir=output_dir,
        skipExisting=skip_existing,
        outputName=output_name,
        recursive=recursive,
        model=model,
        pdfMode=pdf_mode,
    )


@app.command()
def images(
    source: Path,
    output_dir: Path | None = None,
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override the default model"),
    pattern: str = "*.png",
    separate: bool = True,
    skip_existing: bool = True,
):
    TranscribeImages(
        source,
        outputDir=output_dir,
        filePattern=pattern,
        separateOutputs=separate,
        skipExisting=skip_existing,
        model=model,
    )


@app.command("latex-md")
def latex_md(
    source: Path,
    output_dir: Path | None = None,
    pattern: str = "*.tex",
    skip_existing: bool = True,
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model for Markdown conversion"),
):
    LatexToMarkdown(
        source,
        outputDir=output_dir,
        filePattern=pattern,
        skipExisting=skip_existing,
        model=model,
    )


@app.command("latex-json")
def latex_json(
    source: Path,
    output_dir: Path | None = None,
    pattern: str = "*.tex",
    skip_existing: bool = True,
    recursive: bool = False,
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model for JSON conversion"),
):
    LatexToJson(
        source,
        outputDir=output_dir,
        filePattern=pattern,
        skipExisting=skip_existing,
        recursive=recursive,
        model=model,
    )


def main():
    app()


if __name__ == "__main__":
    main()
