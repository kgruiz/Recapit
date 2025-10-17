from __future__ import annotations
from pathlib import Path
from typing import Iterable

from natsort import natsorted
from rich.console import Console

from .config import AppConfig
from .templates import TemplateLoader
from .llm import LLMClient
from .pipeline import Pipeline, Kind, PDFMode
from .constants import GEMINI_2_FLASH_THINKING_EXP
from .pdf import guess_pdf_kind
from .utils import slugify


_KIND_ALIASES: dict[str, Kind] = {
    "slides": Kind.SLIDES,
    "slide": Kind.SLIDES,
    "deck": Kind.SLIDES,
    "presentation": Kind.SLIDES,
    "lecture": Kind.LECTURE,
    "lesson": Kind.LECTURE,
    "notes": Kind.LECTURE,
    "document": Kind.DOCUMENT,
    "doc": Kind.DOCUMENT,
    "generic": Kind.DOCUMENT,
    "worksheet": Kind.DOCUMENT,
    "handout": Kind.DOCUMENT,
    "paper": Kind.DOCUMENT,
    "article": Kind.DOCUMENT,
    "form": Kind.DOCUMENT,
    "image": Kind.IMAGE,
    "img": Kind.IMAGE,
    "video": Kind.VIDEO,
    "vid": Kind.VIDEO,
}


def _resolve_kind(value: Kind | str | None) -> Kind:
    if isinstance(value, Kind):
        return value
    if value is None:
        return Kind.DOCUMENT
    key = value.lower()
    if key == "auto":
        raise ValueError("'auto' must be handled before calling _resolve_kind")
    if key in _KIND_ALIASES:
        return _KIND_ALIASES[key]
    raise ValueError(f"Unknown kind '{value}'. Expected one of: {', '.join(sorted(_KIND_ALIASES))} or 'auto'")


def _mk(ctx_output_dir: Path | None = None, save_intermediates: bool | None = None) -> Pipeline:
    cfg = AppConfig.from_env()
    if ctx_output_dir:
        cfg = AppConfig(
            api_key=cfg.api_key,
            output_dir=Path(ctx_output_dir),
            templates_dir=cfg.templates_dir,
            default_model=cfg.default_model,
            save_full_response=cfg.save_full_response,
            save_intermediates=cfg.save_intermediates if save_intermediates is None else save_intermediates,
            video_token_limit=cfg.video_token_limit,
        )
    elif save_intermediates is not None:
        cfg = AppConfig(
            api_key=cfg.api_key,
            output_dir=cfg.output_dir,
            templates_dir=cfg.templates_dir,
            default_model=cfg.default_model,
            save_full_response=cfg.save_full_response,
            save_intermediates=save_intermediates,
            video_token_limit=cfg.video_token_limit,
        )
    return Pipeline(cfg=cfg, llm=LLMClient(api_key=cfg.api_key), templates=TemplateLoader(cfg.templates_dir))


def TranscribeSlides(
    source,
    outputDir: Path | None = None,
    lectureNumPattern: str | None = r".*(\d+).*",
    excludeLectureNums: list[int] = [],
    skipExisting: bool = True,
    model: str | None = None,
    pdfMode: PDFMode = PDFMode.IMAGES,
):
    pl = _mk(outputDir)
    active_model = model or pl.cfg.default_model
    resolved_root = Path(outputDir).expanduser() if outputDir else None
    paths = _coerce_pdfs(source)
    for p in _filter_by_pattern(paths, lectureNumPattern, excludeLectureNums):
        out_base = pl.output_base_for(source=p, override_root=resolved_root)
        out_tex = out_base / f"{p.stem}-transcribed.tex"
        if skipExisting and out_tex.exists():
            continue
        pl.transcribe_pdf(
            pdf=p,
            kind=Kind.SLIDES,
            model=active_model,
            mode=pdfMode,
            output_root=resolved_root,
        )


def TranscribeLectures(
    source,
    outputDir: Path | None = None,
    lectureNumPattern: str = r".*(\d+).*",
    excludeLectureNums: list[int] = [],
    skipExisting: bool = True,
    model: str | None = None,
    pdfMode: PDFMode = PDFMode.IMAGES,
):
    pl = _mk(outputDir)
    active_model = model or pl.cfg.default_model
    resolved_root = Path(outputDir).expanduser() if outputDir else None
    paths = _coerce_pdfs(source)
    for p in _filter_by_pattern(paths, lectureNumPattern, excludeLectureNums):
        out_base = pl.output_base_for(source=p, override_root=resolved_root)
        out_tex = out_base / f"{p.stem}-transcribed.tex"
        if skipExisting and out_tex.exists():
            continue
        pl.transcribe_pdf(
            pdf=p,
            kind=Kind.LECTURE,
            model=active_model,
            mode=pdfMode,
            output_root=resolved_root,
        )


def TranscribeDocuments(
    source,
    outputDir: Path | None = None,
    skipExisting: bool = True,
    outputName: str | None = None,
    recursive: bool = False,
    model: str | None = None,
    pdfMode: PDFMode = PDFMode.AUTO,
):
    pl = _mk(outputDir)
    active_model = model or pl.cfg.default_model
    resolved_root = Path(outputDir).expanduser() if outputDir else None
    paths = _coerce_pdfs(source, recursive=recursive)
    for p in paths:
        out_name = outputName or f"{p.stem}-transcribed"
        out_base = pl.output_base_for(source=p, override_root=resolved_root)
        out_tex = out_base / f"{out_name}.tex"
        if skipExisting and out_tex.exists():
            continue
        pl.transcribe_pdf(
            pdf=p,
            kind=Kind.DOCUMENT,
            model=active_model,
            output_name=out_name,
            mode=pdfMode,
            output_root=resolved_root,
        )


def TranscribeImages(
    source,
    outputDir: Path | None = None,
    filePattern: str = "*.png",
    separateOutputs: bool = True,
    skipExisting: bool = True,
    model: str | None = None,
):
    pl = _mk(outputDir)
    active_model = model or pl.cfg.default_model
    resolved_root = Path(outputDir).expanduser() if outputDir else None
    imgs = _coerce_images(source, pattern=filePattern)
    if not separateOutputs:
        pl.transcribe_images(images=imgs, kind=Kind.IMAGE, model=active_model, output_dir=resolved_root, bulk=True)
        return
    for img in imgs:
        out_dir = pl.output_base_for(source=img, override_root=resolved_root)
        out_tex = out_dir / f"{img.stem}-transcribed.tex"
        if skipExisting and out_tex.exists():
            continue
        pl.transcribe_images(images=[img], kind=Kind.IMAGE, model=active_model, output_dir=resolved_root, bulk=False)


def TranscribeVideos(
    source,
    outputDir: Path | None = None,
    filePattern: str = "*.mp4",
    skipExisting: bool = True,
    model: str | None = None,
    tokenLimit: int | None = None,
    saveIntermediates: bool | None = None,
):
    pl = _mk(outputDir, save_intermediates=saveIntermediates)
    active_model = model or pl.cfg.default_model
    resolved_root = Path(outputDir).expanduser() if outputDir else None
    videos = _coerce_videos(source, pattern=filePattern)
    if not videos:
        return
    effective_limit = tokenLimit if tokenLimit and tokenLimit > 0 else pl.cfg.video_token_limit
    pl.transcribe_videos(
        videos=videos,
        model=active_model,
        output_dir=resolved_root,
        skip_existing=skipExisting,
        token_limit=effective_limit,
    )


def TranscribeAuto(
    source,
    outputDir: Path | None = None,
    skipExisting: bool = True,
    recursive: bool = False,
    model: str | None = None,
    pdfMode: PDFMode = PDFMode.AUTO,
    kind: Kind | str | None = "auto",
    includeImages: bool = False,
    imagePattern: str = "*.png",
    includeVideo: bool = True,
    videoPattern: str = "*.mp4",
    videoModel: str | None = None,
    videoTokenLimit: int | None = None,
    saveIntermediates: bool | None = None,
):
    """Transcribe PDFs (and optionally images) with automatic prompt selection."""

    pl = _mk(outputDir, save_intermediates=saveIntermediates)
    active_model = model or pl.cfg.default_model

    forced_kind: Kind | None
    if isinstance(kind, str) and kind.lower() == "auto":
        forced_kind = None
    elif kind is None:
        forced_kind = None
    else:
        forced_kind = _resolve_kind(kind)
    if forced_kind == Kind.VIDEO:
        includeVideo = True

    resolved_root = Path(outputDir).expanduser() if outputDir else None
    did_process = False

    def _is_video_file(path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in _VIDEO_EXTENSIONS

    video_seen: set[Path] = set()
    video_sources: list[Path] = []

    def _enqueue_video(candidate: Path) -> None:
        if not _is_video_file(candidate):
            return
        try:
            resolved = candidate.resolve()
        except FileNotFoundError:
            resolved = candidate
        if resolved in video_seen:
            return
        video_seen.add(resolved)
        video_sources.append(resolved)

    direct_video_input = False
    if isinstance(source, list):
        list_paths = [Path(item) for item in source]
        if list_paths and all(_is_video_file(p) for p in list_paths):
            direct_video_input = True
        for lp in list_paths:
            _enqueue_video(lp)
    elif isinstance(source, (str, Path)):
        src_path = Path(source)
        if _is_video_file(src_path):
            direct_video_input = True
            _enqueue_video(src_path)

    if direct_video_input:
        includeVideo = True

    active_video_model = videoModel or active_model
    if videoTokenLimit is not None and videoTokenLimit <= 0:
        raise ValueError("videoTokenLimit must be a positive integer")
    active_video_token_limit = videoTokenLimit or pl.cfg.video_token_limit
    pdf_error: Exception | None = None
    paths: list[Path] = []
    if not direct_video_input:
        try:
            paths = _coerce_pdfs(source, recursive=recursive)
        except ValueError as exc:
            pdf_error = exc
            paths = []

    if pdf_error and not includeVideo and not video_sources:
        raise pdf_error

    if includeVideo:
        def _collect_from_directory(target: Path) -> None:
            for vid in _coerce_videos(target, pattern=videoPattern, recursive=recursive):
                _enqueue_video(vid)

        if isinstance(source, list):
            for item in source:
                item_path = Path(item)
                if item_path.is_dir():
                    _collect_from_directory(item_path)
        elif isinstance(source, (str, Path)):
            src_path = Path(source)
            if src_path.is_dir():
                _collect_from_directory(src_path)

    video_sources = natsorted(video_sources)

    if paths:
        did_process = True
        progress = pl._progress()
        with progress:
            files_task = None
            if len(paths) > 1:
                files_task = progress.add_task("Files", total=len(paths))
            for p in paths:
                inferred = forced_kind or _resolve_kind(guess_pdf_kind(p))
                out_name = f"{p.stem}-transcribed"
                out_dir = pl.output_base_for(source=p, override_root=resolved_root)
                out_tex = out_dir / f"{out_name}.tex"
                if skipExisting and out_tex.exists():
                    progress.console.print(f"[yellow]Skipping {p.name} (existing outputs). Use --no-skip-existing to regenerate.[/yellow]")
                    if files_task is not None:
                        progress.update(files_task, advance=1)
                    continue
                pl.transcribe_pdf(
                    pdf=p,
                    kind=inferred,
                    model=active_model,
                    output_name=out_name,
                    mode=pdfMode,
                    progress=progress,
                    output_root=resolved_root,
                    files_task=files_task,
                )
                progress.console.print(f"[green]Saved[/green] {out_dir / (out_name + '.tex')}")

    if includeImages:
        image_sources: list[Path] = []
        if isinstance(source, (str, Path)):
            src_path = Path(source)
            if src_path.is_dir():
                image_sources = _coerce_images(src_path, pattern=imagePattern)
            elif src_path.is_file() and src_path.suffix.lower() in _IMAGE_EXTENSIONS:
                image_sources = [src_path]
        elif isinstance(source, list):
            for item in source:
                path = Path(item)
                if path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS:
                    image_sources.append(path)
                elif path.is_dir():
                    image_sources.extend(_coerce_images(path, pattern=imagePattern))

        if image_sources:
            image_sources = natsorted(list(dict.fromkeys(image_sources)))
            TranscribeImages(
                image_sources,
                outputDir=outputDir,
                filePattern=imagePattern,
                separateOutputs=True,
                skipExisting=skipExisting,
                model=model,
            )
            did_process = True

    if includeVideo and video_sources:
        TranscribeVideos(
            video_sources,
            outputDir=outputDir,
            filePattern=videoPattern,
            skipExisting=skipExisting,
            model=active_video_model,
            tokenLimit=active_video_token_limit,
        )
        did_process = True

    if not paths and not did_process:
        Console().print("[yellow]No PDF files found to transcribe.[/yellow]")


def LatexToMarkdown(
    source,
    outputDir: Path | None = None,
    filePattern: str = "*.tex",
    skipExisting: bool = True,
    model: str | None = None,
):
    pl = _mk(outputDir)
    active_model = model or GEMINI_2_FLASH_THINKING_EXP
    resolved_root = Path(outputDir).expanduser() if outputDir else None
    tex_files = _coerce_tex(source, pattern=filePattern)
    for t in tex_files:
        out_dir = pl.output_base_for(source=t, override_root=resolved_root)
        out_md = out_dir / f"{t.stem}.md"
        if skipExisting and out_md.exists():
            continue
        pl.latex_to_markdown(tex_file=t, model=active_model, output_dir=resolved_root, output_name=t.stem)


def LatexToJson(
    source,
    outputDir: Path | None = None,
    filePattern: str = "*.tex",
    skipExisting: bool = True,
    model: str | None = None,
    recursive: bool = False,
):
    pl = _mk(outputDir)
    active_model = model or GEMINI_2_FLASH_THINKING_EXP
    resolved_root = Path(outputDir).expanduser() if outputDir else None
    tex_files = _coerce_tex(source, pattern=filePattern, recursive=recursive)
    for t in tex_files:
        out_dir = pl.output_base_for(source=t, override_root=resolved_root)
        out_json = out_dir / f"{t.stem}.json"
        if skipExisting and out_json.exists():
            continue
        pl.latex_to_json(tex_file=t, model=active_model, output_dir=resolved_root, output_name=t.stem)


# ---- helpers ----
from pathlib import Path
import re


_PDF_EXTENSION = ".pdf"
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".mkv",
    ".webm",
    ".avi",
    ".mpg",
    ".mpeg",
    ".wmv",
    ".flv",
}


def _coerce_pdfs(source, recursive: bool = False) -> list[Path]:
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.is_file():
            if p.suffix.lower() != _PDF_EXTENSION:
                raise ValueError(f"Expected a PDF file, got '{p}'")
            return [p]
        files = p.rglob("*.pdf") if recursive else p.glob("*.pdf")
        return natsorted(list(files))
    if isinstance(source, list):
        out: list[Path] = []
        for s in source:
            path = Path(s)
            if path.suffix.lower() != _PDF_EXTENSION:
                raise ValueError(f"Expected a PDF file, got '{path}'")
            out.append(path)
        return natsorted(out)
    raise ValueError("source must be Path|str|list")


def _coerce_images(source, pattern: str) -> list[Path]:
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.is_file():
            return [p] if p.suffix.lower() in _IMAGE_EXTENSIONS else []
        paths = [x for x in p.glob(pattern) if x.suffix.lower() in _IMAGE_EXTENSIONS]
        return natsorted(paths)
    if isinstance(source, list):
        return natsorted([Path(s) for s in source if Path(s).suffix.lower() in _IMAGE_EXTENSIONS])
    raise ValueError("source must be Path|str|list")


def _coerce_tex(source, pattern: str, recursive: bool = False) -> list[Path]:
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.is_file():
            return [p]
        globber = p.rglob if recursive else p.glob
        return natsorted(list(globber(pattern)))
    if isinstance(source, list):
        return natsorted([Path(s) for s in source])
    raise ValueError("source must be Path|str|list")


def _coerce_videos(source, pattern: str, recursive: bool = False) -> list[Path]:
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.is_file():
            return [p] if p.suffix.lower() in _VIDEO_EXTENSIONS else []
        globber = p.rglob if recursive else p.glob
        paths = [x for x in globber(pattern) if x.suffix.lower() in _VIDEO_EXTENSIONS]
        return natsorted(paths)
    if isinstance(source, list):
        return natsorted([Path(s) for s in source if Path(s).suffix.lower() in _VIDEO_EXTENSIONS])
    raise ValueError("source must be Path|str|list")


def _filter_by_pattern(paths: list[Path], pat: str | None, exclude: list[int]) -> list[Path]:
    if pat is None:
        return natsorted(paths)
    out: list[Path] = []
    for p in paths:
        m = re.findall(pat, p.name)
        if not m or len(m) != 1:
            raise ValueError(f"Could not extract single lecture number from {p.name}")
        num = int(m[0])
        if num not in exclude:
            out.append(p)
    return natsorted(out)
