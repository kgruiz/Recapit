from pathlib import Path
from typing import Optional

import typer

from .api import LatexToMarkdown, LatexToJson
from .constants import OUTPUT_DIR
from .video import VideoEncoderPreference, select_encoder_chain
from .core.types import Job as CoreJob, Kind as CoreKind, PdfMode as CorePdfMode
from .engine.planner import Planner
from .engine import Engine
from .ingest import CompositeIngestor, CompositeNormalizer
from .providers import GeminiProvider
from .prompts import build_prompt_strategies
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


def _merge_presets(cfg: AppConfig) -> dict[str, dict[str, object]]:
    merged = {name: dict(values) for name, values in PRESETS.items()}
    for name, values in cfg.presets.items():
        merged[str(name).lower()] = dict(values)
    return merged


def _resolve_media_resolution(value: Optional[str]) -> tuple[str, str | None]:
    if value is None:
        normalized = "default"
    else:
        normalized = str(value).strip().lower()
    aliases = {
        "default": "default",
        "media_resolution_default": "default",
        "low": "low",
        "media_resolution_low": "low",
        "medium": "medium",
        "media_resolution_medium": "medium",
        "high": "high",
        "media_resolution_high": "high",
        "unspecified": "unspecified",
        "media_resolution_unspecified": "unspecified",
    }
    normalized = aliases.get(normalized, normalized)
    enum_map = {
        "default": None,
        "low": "MEDIA_RESOLUTION_LOW",
        "medium": "MEDIA_RESOLUTION_MEDIUM",
        "high": "MEDIA_RESOLUTION_HIGH",
        "unspecified": "MEDIA_RESOLUTION_UNSPECIFIED",
    }
    if normalized not in enum_map:
        raise ValueError(f"invalid media resolution '{value}'")
    return normalized, enum_map[normalized]


def _execute_summarize(
    *,
    source: Path,
    output_dir: Path | None,
    kind: str,
    model_override: Optional[str],
    recursive: bool,
    skip_existing: bool,
    pdf_mode: str,
    preset: str,
    exports: list[str],
    config_path: Path | None,
    media_resolution: Optional[str],
) -> None:
    try:
        cfg = AppConfig.from_sources(config_path)
    except ValueError as exc:  # noqa: BLE001
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    loader = TemplateLoader(cfg.templates_dir)
    prompts = build_prompt_strategies(loader)

    preset_map = _merge_presets(cfg)
    preset_key = preset.lower() if preset else "basic"
    if preset_key not in preset_map:
        raise typer.BadParameter(
            f"Unknown preset '{preset}'. Available presets: {', '.join(sorted(preset_map))}",
            param_hint="--preset",
        )

    if kind.lower() == "auto":
        normalized_kind: CoreKind | None = None
    else:
        try:
            normalized_kind = CoreKind(kind.lower())
        except ValueError as exc:  # noqa: BLE001
            raise typer.BadParameter(
                "Kind must be one of auto|slides|lecture|document|image|video",
                param_hint="--kind",
            ) from exc

    try:
        normalized_pdf_mode = CorePdfMode(pdf_mode.lower())
    except ValueError as exc:  # noqa: BLE001
        raise typer.BadParameter("PDF mode must be auto|pdf|images", param_hint="--pdf-mode") from exc

    active_model = model_override or cfg.default_model

    preset_media = preset_map[preset_key].get("media_resolution")
    media_candidate = media_resolution or preset_media or cfg.media_resolution
    try:
        media_label, media_enum = _resolve_media_resolution(media_candidate)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--media-resolution") from exc

    tokens_per_second = cfg.video_tokens_per_second
    if media_label == "low" and tokens_per_second > 100:
        tokens_per_second = 100.0

    encoder_chain, _encoder_diag = select_encoder_chain(cfg.video_encoder_preference)

    monitor = RunMonitor()
    provider = GeminiProvider(api_key=cfg.api_key, model=active_model, monitor=monitor)
    normalizer = CompositeNormalizer(
        capability_checker=provider.supports,
        encoder_chain=encoder_chain,
        max_chunk_seconds=cfg.video_max_chunk_seconds,
        max_chunk_bytes=cfg.video_max_chunk_bytes,
        token_limit=cfg.video_token_limit,
        tokens_per_second=tokens_per_second,
    )
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

    effective_output_dir = output_dir or cfg.output_dir or OUTPUT_DIR
    selected_exports = [item for item in exports if item] or list(cfg.exports)

    job = CoreJob(
        source=str(source),
        recursive=recursive,
        kind=normalized_kind,
        pdf_mode=normalized_pdf_mode,
        output_dir=effective_output_dir,
        model=active_model,
        preset=preset_key,
        export=selected_exports or None,
        skip_existing=skip_existing,
        media_resolution=media_enum,
    )

    preset_config = preset_map[preset_key]
    if "pdf_mode" in preset_config and job.pdf_mode == CorePdfMode.AUTO:
        try:
            preset_pdf_mode = CorePdfMode(str(preset_config["pdf_mode"]).lower())
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
                media_resolution=job.media_resolution,
            )
        except ValueError:
            pass

    result = engine.run(job)
    if result is None:
        typer.echo("No output generated.")
    else:
        typer.echo(f"Wrote {result}")


@app.callback(invoke_without_command=True)
def default(  # noqa: D401
    ctx: typer.Context,
    source: Optional[Path] = typer.Argument(None, help="File, directory, or URL to summarize."),
    output_dir: Path | None = typer.Option(None, "--output-dir", "-o", help="Write outputs under this directory"),
    kind: str = typer.Option("auto", "--kind", "-k", case_sensitive=False, help="auto|slides|lecture|document|image|video"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override the default model"),
    pdf_mode: str = typer.Option("auto", "--pdf-mode", "-P", case_sensitive=False, help="auto|pdf|images"),
    recursive: bool = typer.Option(False, "--recursive/--no-recursive", help="Recurse into directories"),
    skip_existing: bool = typer.Option(True, "--skip-existing/--no-skip-existing", help="Skip outputs that already exist"),
    export: list[str] = typer.Option([], "--export", "-e", help="Write additional exports such as srt or vtt"),
    preset: str = typer.Option("basic", "--preset", help="Preset profile", case_sensitive=False),
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to YAML configuration file"),
    media_resolution: Optional[str] = typer.Option(None, "--media-resolution", help="default|low|medium|high"),
) -> None:
    if ctx.invoked_subcommand:
        return
    if source is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()
    _execute_summarize(
        source=source,
        output_dir=output_dir,
        kind=kind,
        model_override=model,
        recursive=recursive,
        skip_existing=skip_existing,
        pdf_mode=pdf_mode,
        preset=preset,
        exports=export,
        config_path=config,
        media_resolution=media_resolution,
    )


@app.command(help="Summarize a source using the unified engine.")
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
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to YAML configuration file"),
    media_resolution: Optional[str] = typer.Option(None, "--media-resolution", help="default|low|medium|high"),
) -> None:
    _execute_summarize(
        source=source,
        output_dir=output_dir,
        kind=kind,
        model_override=model,
        recursive=recursive,
        skip_existing=skip_existing,
        pdf_mode=pdf_mode,
        preset=preset,
        exports=export,
        config_path=config,
        media_resolution=media_resolution,
    )


@app.command(help="Alias for summarize.")
def transcribe(
    ctx: typer.Context,
    source: Path = typer.Argument(..., help="File, directory, or URL to summarize."),
    output_dir: Path | None = typer.Option(None, "--output-dir", "-o", help="Write outputs under this directory"),
    kind: str = typer.Option("auto", "--kind", "-k", case_sensitive=False, help="auto|slides|lecture|document|image|video"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override the default model"),
    pdf_mode: str = typer.Option("auto", "--pdf-mode", "-P", case_sensitive=False, help="auto|pdf|images"),
    recursive: bool = typer.Option(False, "--recursive/--no-recursive", help="Recurse into directories"),
    skip_existing: bool = typer.Option(True, "--skip-existing/--no-skip-existing", help="Skip outputs that already exist"),
    export: list[str] = typer.Option([], "--export", "-e", help="Write additional exports such as srt or vtt"),
    preset: str = typer.Option("basic", "--preset", help="Preset profile", case_sensitive=False),
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to YAML configuration file"),
    media_resolution: Optional[str] = typer.Option(None, "--media-resolution", help="default|low|medium|high"),
) -> None:
    ctx.invoke(
        summarize,
        source=source,
        output_dir=output_dir,
        kind=kind,
        model=model,
        pdf_mode=pdf_mode,
        recursive=recursive,
        skip_existing=skip_existing,
        export=export,
        preset=preset,
        config=config,
        media_resolution=media_resolution,
    )


@app.command(help="Preview ingestion and chunk planning without running transcription.")
def plan(  # noqa: D401 - short CLI help already provided
    source: Path = typer.Argument(..., help="File, directory, or URL to inspect."),
    recursive: bool = typer.Option(False, "--recursive/--no-recursive", help="Recurse into directories."),
    kind: str = typer.Option("auto", "--kind", "-k", case_sensitive=False, help="auto|slides|lecture|document|image|video"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model to preview."),
    pdf_mode: str = typer.Option("auto", "--pdf-mode", "-P", case_sensitive=False, help="auto|pdf|images"),
    json_output: bool = typer.Option(False, "--json/--no-json", help="Emit JSON instead of human-readable text."),
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to YAML configuration file"),
):
    try:
        cfg = AppConfig.from_sources(config)
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
    job = CoreJob(
        source=str(source),
        recursive=recursive,
        kind=normalized_kind,
        pdf_mode=normalized_pdf_mode,
        output_dir=None,
        model=active_model,
    )

    planner = Planner(
        ingestor=CompositeIngestor(),
        normalizer=CompositeNormalizer(
            capability_checker=lambda cap: cap in {"pdf", "image", "video", "audio"},
            max_chunk_seconds=cfg.video_max_chunk_seconds,
            max_chunk_bytes=cfg.video_max_chunk_bytes,
            token_limit=cfg.video_token_limit,
            tokens_per_second=cfg.video_tokens_per_second,
        ),
    )
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


@app.command(help="Create a starter configuration file in the current directory.")
def init(
    path: Path = typer.Option(Path("recapit.yaml"), "--path", help="Where to write the config file"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing file"),
):
    target = path.expanduser()
    if target.exists() and not force:
        raise typer.BadParameter(f"{target} already exists; use --force to overwrite", param_hint="--force")

    content = """# Recapit configuration\n# Adjust defaults for the summarize command.\n# Available presets live under presets.<name>.\n\ndefaults:\n  model: \"gemini-2.0-flash\"\n  output_dir: \"output\"\n  exports: [\"srt\"]\n\nsave:\n  full_response: false\n  intermediates: true\n\nvideo:\n  token_limit: 300000\n  tokens_per_second: 300\n  max_chunk_seconds: 7200\n  max_chunk_bytes: 524288000\n  encoder: \"auto\"\n  media_resolution: \"default\"\n\npresets:\n  speed:\n    pdf_mode: \"images\"\n  quality:\n    pdf_mode: \"pdf\"\n"""
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
