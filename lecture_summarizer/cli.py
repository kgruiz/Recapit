from pathlib import Path
from typing import Optional

import typer

from .api import (
    LatexToMarkdown,
    LatexToJson,
    TranscribeAuto,
)
from .pipeline import PDFMode


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
convert_app = typer.Typer(
    help="Utilities for converting LaTeX outputs",
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(convert_app, name="convert")


def _run_transcribe(
    source: Path,
    output_dir: Path | None,
    kind: str,
    model: Optional[str],
    recursive: bool,
    skip_existing: bool,
    pdf_mode: PDFMode | str,
    include_images: bool,
    image_pattern: str,
    include_video: bool,
    video_pattern: str,
    video_model: Optional[str],
):
    normalized_pdf_mode = pdf_mode
    if isinstance(pdf_mode, str):
        normalized_pdf_mode = PDFMode(pdf_mode.lower())
    TranscribeAuto(
        source,
        outputDir=output_dir,
        skipExisting=skip_existing,
        recursive=recursive,
        model=model,
        pdfMode=normalized_pdf_mode,
        kind=kind,
        includeImages=include_images,
        imagePattern=image_pattern,
        includeVideo=include_video,
        videoPattern=video_pattern,
        videoModel=video_model,
    )


@app.callback(invoke_without_command=True)
def default(
    ctx: typer.Context,
    source: Optional[Path] = typer.Argument(
        None,
        help="File or directory to transcribe (PDFs, images, or folders)",
    ),
    output_dir: Path | None = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    kind: str = typer.Option("auto", "--kind", "-k", case_sensitive=False, help="auto|slides|lecture|document|image"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override the default model"),
    recursive: bool = typer.Option(
        False,
        "--recursive",
        "-r",
        help="Recurse into directories when scanning for PDFs",
    ),
    skip_existing: bool = typer.Option(
        True,
        "--skip-existing/--no-skip-existing",
        "-s/-S",
        help="Skip outputs that already exist",
    ),
    pdf_mode: str = typer.Option(
        PDFMode.AUTO.value,
        "--pdf-mode",
        "-P",
        case_sensitive=False,
        help="How to feed PDFs: images, pdf, or auto",
    ),
    include_images: bool = typer.Option(
        False,
        "--include-images",
        "-i",
        help="Also process standalone images when scanning directories",
    ),
    image_pattern: str = typer.Option(
        "*.png",
        "--image-pattern",
        "-p",
        help="Glob for supplemental images when --include-images is set",
    ),
    include_video: bool = typer.Option(
        False,
        "--include-video",
        "-V",
        help="Also process video files when scanning directories",
    ),
    video_pattern: str = typer.Option(
        "*.mp4",
        "--video-pattern",
        "-v",
        help="Glob for supplemental videos when --include-video is set",
    ),
    video_model: Optional[str] = typer.Option(
        None,
        "--video-model",
        help="Override the default model specifically for video transcription",
    ),
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
        include_video=include_video,
        video_pattern=video_pattern,
        video_model=video_model,
    )


@app.command()
def transcribe(
    source: Path,
    output_dir: Path | None = None,
    kind: str = typer.Option("auto", "--kind", "-k", case_sensitive=False, help="auto|slides|lecture|document|image"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override the default model"),
    recursive: bool = typer.Option(
        False,
        "--recursive",
        "-r",
        help="Recurse into directories when scanning for PDFs",
    ),
    skip_existing: bool = typer.Option(
        True,
        "--skip-existing/--no-skip-existing",
        "-s/-S",
        help="Skip outputs that already exist",
    ),
    pdf_mode: str = typer.Option(
        PDFMode.AUTO.value,
        "--pdf-mode",
        "-P",
        case_sensitive=False,
        help="How to feed PDFs: images, pdf, or auto",
    ),
    include_images: bool = typer.Option(
        False,
        "--include-images",
        "-i",
        help="Also process standalone images when scanning directories",
    ),
    image_pattern: str = typer.Option(
        "*.png",
        "--image-pattern",
        "-p",
        help="Glob for supplemental images when --include-images is set",
    ),
    include_video: bool = typer.Option(
        False,
        "--include-video",
        "-V",
        help="Also process video files when scanning directories",
    ),
    video_pattern: str = typer.Option(
        "*.mp4",
        "--video-pattern",
        "-v",
        help="Glob for supplemental videos when --include-video is set",
    ),
    video_model: Optional[str] = typer.Option(
        None,
        "--video-model",
        help="Override the default model specifically for video transcription",
    ),
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
        include_video=include_video,
        video_pattern=video_pattern,
        video_model=video_model,
    )


@convert_app.command("md")
def latex_md(
    source: Path,
    output_dir: Path | None = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    pattern: str = typer.Option("*.tex", "--pattern", "-p", help="Glob pattern for LaTeX sources"),
    skip_existing: bool = typer.Option(
        True,
        "--skip-existing/--no-skip-existing",
        "-s/-S",
        help="Skip outputs that already exist",
    ),
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
    output_dir: Path | None = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    pattern: str = typer.Option("*.tex", "--pattern", "-p", help="Glob pattern for LaTeX sources"),
    skip_existing: bool = typer.Option(
        True,
        "--skip-existing/--no-skip-existing",
        "-s/-S",
        help="Skip outputs that already exist",
    ),
    recursive: bool = typer.Option(
        False,
        "--recursive",
        "-r",
        help="Recurse into subdirectories when scanning for LaTeX files",
    ),
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
