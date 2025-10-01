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
    TranscribeAuto,
)
from .pipeline import PDFMode


app = typer.Typer(add_completion=False, no_args_is_help=True)
convert_app = typer.Typer(help="Utilities for converting LaTeX outputs")
app.add_typer(convert_app, name="convert")


def _run_transcribe(
    source: Path,
    output_dir: Path | None,
    kind: str,
    model: Optional[str],
    recursive: bool,
    skip_existing: bool,
    pdf_mode: PDFMode,
    include_images: bool,
    image_pattern: str,
):
    TranscribeAuto(
        source,
        outputDir=output_dir,
        skipExisting=skip_existing,
        recursive=recursive,
        model=model,
        pdfMode=pdf_mode,
        kind=kind,
        includeImages=include_images,
        imagePattern=image_pattern,
    )


@app.callback(invoke_without_command=True)
def default(
    ctx: typer.Context,
    source: Optional[Path] = typer.Argument(
        None,
        help="File or directory to transcribe (PDFs, images, or folders)",
    ),
    output_dir: Path | None = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    kind: str = typer.Option("auto", "--kind", "-k", case_sensitive=False, help="auto|slides|lecture|document"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override the default model"),
    recursive: bool = typer.Option(False, "--recursive", help="Recurse into directories when scanning for PDFs"),
    skip_existing: bool = typer.Option(True, "--skip-existing/--no-skip-existing", help="Skip outputs that already exist"),
    pdf_mode: PDFMode = typer.Option(PDFMode.AUTO, "--pdf-mode", case_sensitive=False, help="How to feed PDFs: images, pdf, or auto"),
    include_images: bool = typer.Option(False, "--include-images", help="Also process standalone images when scanning directories"),
    image_pattern: str = typer.Option("*.png", "--image-pattern", help="Glob for supplemental images when --include-images is set"),
):
    if ctx.invoked_subcommand:
        return
    if source is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()
    _run_transcribe(
        source=source,
        output_dir=output_dir,
        kind=kind,
        model=model,
        recursive=recursive,
        skip_existing=skip_existing,
        pdf_mode=pdf_mode,
        include_images=include_images,
        image_pattern=image_pattern,
    )


@app.command()
def transcribe(
    source: Path,
    output_dir: Path | None = None,
    kind: str = typer.Option("auto", "--kind", "-k", case_sensitive=False, help="auto|slides|lecture|document"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override the default model"),
    recursive: bool = False,
    skip_existing: bool = typer.Option(True, "--skip-existing/--no-skip-existing", help="Skip outputs that already exist"),
    pdf_mode: PDFMode = typer.Option(PDFMode.AUTO, "--pdf-mode", case_sensitive=False, help="How to feed PDFs: images, pdf, or auto"),
    include_images: bool = typer.Option(False, "--include-images", help="Also process standalone images when scanning directories"),
    image_pattern: str = typer.Option("*.png", "--image-pattern", help="Glob for supplemental images when --include-images is set"),
):
    _run_transcribe(
        source=source,
        output_dir=output_dir,
        kind=kind,
        model=model,
        recursive=recursive,
        skip_existing=skip_existing,
        pdf_mode=pdf_mode,
        include_images=include_images,
        image_pattern=image_pattern,
    )


@app.command(hidden=True)
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


@app.command(hidden=True)
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


@app.command(hidden=True)
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


@app.command(hidden=True)
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


@convert_app.command("md")
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


@convert_app.command("json")
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
