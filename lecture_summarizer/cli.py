import json
from pathlib import Path
from typing import Optional

import typer

from .api import (
    LatexToMarkdown,
    LatexToJson,
    TranscribeAuto,
    RunReport,
)
from .pipeline import PDFMode
from .constants import OUTPUT_DIR


_CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "allow_interspersed_args": True}

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    context_settings=_CONTEXT_SETTINGS,
)
convert_app = typer.Typer(
    help="Utilities for converting LaTeX outputs",
    context_settings=_CONTEXT_SETTINGS,
)
app.add_typer(convert_app, name="convert")


def _default_summary_path(output_dir: Path | None) -> Path:
    base = (output_dir or OUTPUT_DIR).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    return base / "run-summary.json"


def _write_summary(report: RunReport, path: Path) -> None:
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True))


def _print_summary(report: RunReport, *, detailed: bool) -> None:
    summary = report.summary
    costs = report.costs
    typer.echo("Run Summary:")
    typer.echo(f"  Requests: {summary.total_requests}")
    typer.echo(f"  Input tokens: {summary.total_input_tokens:,}")
    typer.echo(f"  Output tokens: {summary.total_output_tokens:,}")
    typer.echo(
        f"  Cost: ${costs.total_cost:.2f} (input ${costs.total_input_cost:.2f}, output ${costs.total_output_cost:.2f})"
    )
    if costs.estimated:
        typer.echo("  Note: costs include estimated values for some requests.")
    if detailed and costs.per_model:
        typer.echo("  Per-model breakdown:")
        for model, data in sorted(costs.per_model.items()):
            typer.echo(
                f"    {model}: ${data['total_cost']:.2f} (input ${data['input_cost']:.2f}, "
                f"output ${data['output_cost']:.2f}) tokens in {data['input_tokens']:,}, out {data['output_tokens']:,}"
            )


def _handle_report(
    report: RunReport | None,
    *,
    show_summary: bool,
    detailed_costs: bool,
    summary_path: Path | None,
    fallback_output_dir: Path | None,
) -> None:
    if report is None:
        return
    if show_summary:
        _print_summary(report, detailed=detailed_costs)
    path = summary_path or _default_summary_path(fallback_output_dir)
    try:
        _write_summary(report, path)
    except OSError as exc:  # noqa: BLE001
        typer.echo(f"Warning: failed to write summary to {path}: {exc}")


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
    video_token_limit: Optional[int],
    save_intermediates: bool,
    show_summary: bool,
    detailed_costs: bool,
    summary_path: Path | None,
):
    normalized_pdf_mode = pdf_mode
    if isinstance(pdf_mode, str):
        normalized_pdf_mode = PDFMode(pdf_mode.lower())
    report = TranscribeAuto(
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
        videoTokenLimit=video_token_limit,
        saveIntermediates=save_intermediates,
    )
    _handle_report(
        report,
        show_summary=show_summary,
        detailed_costs=detailed_costs,
        summary_path=summary_path,
        fallback_output_dir=output_dir,
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
        True,
        "--include-video/--no-include-video",
        help="Toggle processing of video files when scanning directories",
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
    video_token_limit: Optional[int] = typer.Option(
        None,
        "--video-token-limit",
        help="Maximum tokens allowed per video chunk before splitting (default 300000)",
    ),
    save_intermediates: bool = typer.Option(
        False,
        "--save-intermediates/--no-save-intermediates",
        help="Persist normalized videos and chunk files for reuse/debugging",
    ),
    show_summary: bool = typer.Option(
        True,
        "--show-summary/--hide-summary",
        help="Display token and cost summary after processing",
    ),
    detailed_costs: bool = typer.Option(
        False,
        "--detailed-costs",
        help="Include per-model cost breakdown in the summary output",
    ),
    summary_path: Optional[Path] = typer.Option(
        None,
        "--summary-path",
        help="Write the run summary JSON to this path",
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
        video_token_limit=video_token_limit,
        save_intermediates=save_intermediates,
        show_summary=show_summary,
        detailed_costs=detailed_costs,
        summary_path=summary_path,
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
        True,
        "--include-video/--no-include-video",
        help="Toggle processing of video files when scanning directories",
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
    video_token_limit: Optional[int] = typer.Option(
        None,
        "--video-token-limit",
        help="Maximum tokens allowed per video chunk before splitting (default 300000)",
    ),
    save_intermediates: bool = typer.Option(
        False,
        "--save-intermediates/--no-save-intermediates",
        help="Persist normalized videos and chunk files for reuse/debugging",
    ),
    show_summary: bool = typer.Option(
        True,
        "--show-summary/--hide-summary",
        help="Display token and cost summary after processing",
    ),
    detailed_costs: bool = typer.Option(
        False,
        "--detailed-costs",
        help="Include per-model cost breakdown in the summary output",
    ),
    summary_path: Optional[Path] = typer.Option(
        None,
        "--summary-path",
        help="Write the run summary JSON to this path",
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
        video_token_limit=video_token_limit,
        save_intermediates=save_intermediates,
        show_summary=show_summary,
        detailed_costs=detailed_costs,
        summary_path=summary_path,
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
