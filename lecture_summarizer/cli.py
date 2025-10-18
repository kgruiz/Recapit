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
from .video import VideoEncoderPreference
from .core.types import Job as CoreJob, Kind as CoreKind, PdfMode as CorePdfMode
from .engine.planner import Planner
from .engine import Engine
from .ingest import CompositeIngestor, CompositeNormalizer
from .providers import GeminiProvider
from .render.writer import LatexWriter
from .render.subtitles import SubtitleExporter
from .templates import TemplateLoader
from .telemetry import RunMonitor
from .output.cost import CostEstimator
from .config import AppConfig


_CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "allow_interspersed_args": True}

PRESETS: dict[str, dict[str, object]] = {
    "basic": {},
    "speed": {"pdf_mode": "images"},
    "quality": {"pdf_mode": "pdf"},
}

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


class _TemplatePromptStrategy:
    def __init__(self, loader: TemplateLoader, kind: CoreKind) -> None:
        self.kind = kind
        self._loader = loader

    def preamble(self) -> str:
        if self.kind == CoreKind.SLIDES:
            return self._loader.slide_preamble()
        if self.kind == CoreKind.LECTURE:
            return self._loader.lecture_preamble()
        if self.kind == CoreKind.IMAGE:
            return self._loader.image_preamble()
        if self.kind == CoreKind.VIDEO:
            return self._loader.video_preamble()
        return self._loader.document_preamble()

    def instruction(self, preamble: str) -> str:
        prompt = self._loader.prompt(self.kind.value)
        return prompt.replace("{{PREAMBLE}}", preamble)


def _prompt_strategies(loader: TemplateLoader) -> dict[CoreKind, _TemplatePromptStrategy]:
    return {kind: _TemplatePromptStrategy(loader, kind) for kind in CoreKind}


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
    video_encoder: str,
):
    try:
        encoder_pref = VideoEncoderPreference.parse(video_encoder)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--video-encoder") from exc
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
        videoEncoder=encoder_pref,
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
        PDFMode.IMAGES.value,
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
    video_encoder: str = typer.Option(
        VideoEncoderPreference.AUTO.value,
        "--video-encoder",
        help="Preferred video encoder (auto, cpu, nvenc, videotoolbox, qsv, vaapi, amf)",
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
        video_encoder=video_encoder,
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
        PDFMode.IMAGES.value,
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
    video_encoder: str = typer.Option(
        VideoEncoderPreference.AUTO.value,
        "--video-encoder",
        help="Preferred video encoder (auto, cpu, nvenc, videotoolbox, qsv, vaapi, amf)",
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
        video_encoder=video_encoder,
    )


@app.command(help="Preview ingestion and chunk planning without running transcription.")
def plan(  # noqa: D401 - short CLI help already provided
    source: Path = typer.Argument(..., help="File, directory, or URL to inspect."),
    recursive: bool = typer.Option(False, "--recursive/--no-recursive", help="Recurse into directories."),
    kind: str = typer.Option("auto", "--kind", "-k", case_sensitive=False, help="auto|slides|lecture|document|image|video"),
    model: str = typer.Option("gemini-2.0-flash", "--model", "-m", help="Model to preview."),
    pdf_mode: str = typer.Option("auto", "--pdf-mode", "-P", case_sensitive=False, help="auto|pdf|images"),
    json_output: bool = typer.Option(False, "--json/--no-json", help="Emit JSON instead of human-readable text."),
):
    normalized_kind: CoreKind | None
    if kind.lower() == "auto":
        normalized_kind = None
    else:
        try:
            normalized_kind = CoreKind(kind.lower())
        except ValueError as exc:  # noqa: BLE001
            raise typer.BadParameter("Kind must be one of auto|slides|lecture|document|image|video", param_hint="--kind") from exc

    try:
        normalized_pdf_mode = CorePdfMode(pdf_mode.lower())
    except ValueError as exc:  # noqa: BLE001
        raise typer.BadParameter("PDF mode must be auto|pdf|images", param_hint="--pdf-mode") from exc

    job = CoreJob(
        source=str(source),
        recursive=recursive,
        kind=normalized_kind,
        pdf_mode=normalized_pdf_mode,
        output_dir=None,
        model=model,
    )

    planner = Planner(ingestor=CompositeIngestor(), normalizer=CompositeNormalizer())
    report = planner.plan(job)

    if json_output:
        typer.echo(report.to_json())
        return

    typer.echo(f"Source: {report.job.source}")
    typer.echo(f"Kind: {report.kind.value}")
    typer.echo(f"Modality: {report.modality or 'unknown'}")
    typer.echo(f"Assets: {len(report.assets)}")
    for asset in report.assets[:10]:
        typer.echo(f"  - {asset.media}: {asset.path}")
    if len(report.assets) > 10:
        typer.echo(f"  ... {len(report.assets) - 10} more")
    typer.echo(f"Chunks planned: {len(report.chunks)}")


@app.command(help="Summarize a source using the new engine pipeline.")
def summarize(  # noqa: D401
    source: Path = typer.Argument(..., help="File, directory, or URL to summarize."),
    output_dir: Path | None = typer.Option(None, "--output-dir", "-o", help="Write outputs under this directory"),
    kind: str = typer.Option("auto", "--kind", "-k", case_sensitive=False, help="auto|slides|lecture|document|image|video"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override the default model"),
    pdf_mode: str = typer.Option("auto", "--pdf-mode", "-P", case_sensitive=False, help="auto|pdf|images"),
    recursive: bool = typer.Option(False, "--recursive/--no-recursive", help="Recurse into directories"),
    skip_existing: bool = typer.Option(True, "--skip-existing/--no-skip-existing", help="Skip outputs that already exist"),
    export: list[str] = typer.Option([], "--export", "-e", help="Write additional exports such as srt or vtt"),
    preset: str = typer.Option("basic", "--preset", help="Preset profile", case_sensitive=False),
):
    try:
        cfg = AppConfig.from_env()
    except ValueError as exc:  # noqa: BLE001
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    normalized_kind: CoreKind | None
    if kind.lower() == "auto":
        normalized_kind = None
    else:
        try:
            normalized_kind = CoreKind(kind.lower())
        except ValueError as exc:  # noqa: BLE001
            raise typer.BadParameter("Kind must be one of auto|slides|lecture|document|image|video", param_hint="--kind") from exc

    try:
        normalized_pdf_mode = CorePdfMode(pdf_mode.lower())
    except ValueError as exc:  # noqa: BLE001
        raise typer.BadParameter("PDF mode must be auto|pdf|images", param_hint="--pdf-mode") from exc

    active_model = model or cfg.default_model
    preset_key = preset.lower() if preset else "basic"
    if preset_key not in PRESETS:
        raise typer.BadParameter(
            f"Unknown preset '{preset}'. Available presets: {', '.join(sorted(PRESETS))}",
            param_hint="--preset",
        )
    loader = TemplateLoader(cfg.templates_dir)
    prompts = _prompt_strategies(loader)

    monitor = RunMonitor()
    normalizer = CompositeNormalizer()
    provider = GeminiProvider(api_key=cfg.api_key, model=active_model, monitor=monitor)
    engine = Engine(
        ingestor=CompositeIngestor(),
        normalizer=normalizer,
        prompts=prompts,
        provider=provider,
        writer=LatexWriter(),
        monitor=monitor,
        cost=CostEstimator(),
        subtitles=SubtitleExporter(),
    )

    job = CoreJob(
        source=str(source),
        recursive=recursive,
        kind=normalized_kind,
        pdf_mode=normalized_pdf_mode,
        output_dir=output_dir or cfg.output_dir,
        model=active_model,
        preset=preset_key,
        export=list(export) if export else None,
        skip_existing=skip_existing,
    )

    preset_config = PRESETS[preset_key]
    if "pdf_mode" in preset_config and job.pdf_mode == CorePdfMode.AUTO:
        try:
            preset_pdf_mode = CorePdfMode(str(preset_config["pdf_mode"]).lower())
        except ValueError:
            preset_pdf_mode = job.pdf_mode
        job = CoreJob(
            source=job.source,
            recursive=job.recursive,
            kind=job.kind,
            pdf_mode=preset_pdf_mode,
            output_dir=job.output_dir,
            model=job.model,
            preset=job.preset,
            export=job.export,
            skip_existing=job.skip_existing,
        )

    result = engine.run(job)
    if result is None:
        typer.echo("No output generated.")
    else:
        typer.echo(f"Wrote {result}")


@app.command(help="Create a starter configuration file in the current directory.")
def init(
    path: Path = typer.Option(Path("lecture-summarizer.toml"), "--path", help="Where to write the config file"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing file"),
):
    target = path.expanduser()
    if target.exists() and not force:
        raise typer.BadParameter(f"{target} already exists; use --force to overwrite", param_hint="--force")

    content = """# Lecture Summarizer configuration\n# Adjust defaults for the summarize command.\n# Available presets live under [presets.<name>].\n\ndefault_model = \"gemini-2.5-flash-lite\"\noutput_dir = \"output\"\nsave_full_response = true\nexports = [\"srt\"]\n\n[presets.speed]\npdf_mode = \"images\"\n\n[presets.quality]\npdf_mode = \"pdf\"\n"""
    target.write_text(content)
    typer.echo(f"Wrote {target}")


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
