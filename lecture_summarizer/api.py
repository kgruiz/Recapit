from __future__ import annotations
from pathlib import Path
from typing import Iterable

from natsort import natsorted

from .config import AppConfig
from .templates import TemplateLoader
from .llm import LLMClient
from .pipeline import Pipeline, Kind, PDFMode
from .constants import GEMINI_2_FLASH_THINKING_EXP


def _mk(ctx_output_dir: Path | None = None) -> Pipeline:
    cfg = AppConfig.from_env()
    if ctx_output_dir:
        cfg = AppConfig(
            api_key=cfg.api_key,
            output_dir=Path(ctx_output_dir),
            templates_dir=cfg.templates_dir,
            default_model=cfg.default_model,
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
    paths = _coerce_pdfs(source)
    for p in _filter_by_pattern(paths, lectureNumPattern, excludeLectureNums):
        out_tex = pl.cfg.output_dir / p.stem / f"{p.stem}-transcribed.tex"
        if skipExisting and out_tex.exists():
            continue
        pl.transcribe_pdf(pdf=p, kind=Kind.SLIDES, model=active_model, mode=pdfMode)


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
    paths = _coerce_pdfs(source)
    for p in _filter_by_pattern(paths, lectureNumPattern, excludeLectureNums):
        out_tex = pl.cfg.output_dir / p.stem / f"{p.stem}-transcribed.tex"
        if skipExisting and out_tex.exists():
            continue
        pl.transcribe_pdf(pdf=p, kind=Kind.LECTURE, model=active_model, mode=pdfMode)


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
    paths = _coerce_pdfs(source, recursive=recursive)
    for p in paths:
        out_name = outputName or f"{p.stem}-transcribed"
        out_tex = pl.cfg.output_dir / p.stem / f"{out_name}.tex"
        if skipExisting and out_tex.exists():
            continue
        pl.transcribe_pdf(pdf=p, kind=Kind.DOCUMENT, model=active_model, output_name=out_name, mode=pdfMode)


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
    imgs = _coerce_images(source, pattern=filePattern)
    if not separateOutputs:
        pl.transcribe_images(images=imgs, kind=Kind.IMAGE, model=active_model, output_dir=outputDir, bulk=True)
        return
    for img in imgs:
        out_dir = (Path(outputDir) if outputDir else pl.cfg.output_dir) / img.stem
        out_tex = out_dir / f"{img.stem}-transcribed.tex"
        if skipExisting and out_tex.exists():
            continue
        pl.transcribe_images(images=[img], kind=Kind.IMAGE, model=active_model, output_dir=outputDir, bulk=False)


def LatexToMarkdown(
    source,
    outputDir: Path | None = None,
    filePattern: str = "*.tex",
    skipExisting: bool = True,
    model: str | None = None,
):
    pl = _mk(outputDir)
    active_model = model or GEMINI_2_FLASH_THINKING_EXP
    tex_files = _coerce_tex(source, pattern=filePattern)
    for t in tex_files:
        out_dir = (Path(outputDir) if outputDir else pl.cfg.output_dir) / t.stem
        out_md = out_dir / f"{t.stem}.md"
        if skipExisting and out_md.exists():
            continue
        pl.latex_to_markdown(tex_file=t, model=active_model, output_dir=out_dir, output_name=t.stem)


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
    tex_files = _coerce_tex(source, pattern=filePattern, recursive=recursive)
    for t in tex_files:
        out_dir = (Path(outputDir) if outputDir else pl.cfg.output_dir) / t.stem
        out_json = out_dir / f"{t.stem}.json"
        if skipExisting and out_json.exists():
            continue
        pl.latex_to_json(tex_file=t, model=active_model, output_dir=out_dir, output_name=t.stem)


# ---- helpers ----
from pathlib import Path
import re


def _coerce_pdfs(source, recursive: bool = False) -> list[Path]:
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.is_file():
            return [p]
        return natsorted(list(p.rglob("*.pdf") if recursive else p.glob("*.pdf")))
    if isinstance(source, list):
        return natsorted([Path(s) for s in source])
    raise ValueError("source must be Path|str|list")


def _coerce_images(source, pattern: str) -> list[Path]:
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.is_file():
            return [p]
        return natsorted(list(p.glob(pattern)))
    if isinstance(source, list):
        return natsorted([Path(s) for s in source])
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
