from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable, Sequence, TypeVar

from natsort import natsorted

from .config import AppConfig
from .constants import GEMINI_2_FLASH_THINKING_EXP
from .llm import LLMClient
from .telemetry import RunMonitor
from .templates import TemplateLoader

T = TypeVar("T")


def _run_parallel(work_items: Sequence[T], *, max_workers: int, fn: Callable[[T], None]) -> None:
    """Execute *fn* over *work_items* using a bounded worker pool."""
    if not work_items:
        return
    worker_count = min(max_workers, len(work_items))
    if worker_count <= 1:
        for item in work_items:
            fn(item)
        return
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(fn, item): item for item in work_items}
        for future in as_completed(futures):
            future.result()


def LatexToMarkdown(
    source: Iterable[str | Path] | str | Path,
    outputDir: Path | None = None,
    filePattern: str = "*.tex",
    skipExisting: bool = True,
    model: str | None = None,
) -> None:
    """Convert LaTeX sources to Markdown using Gemini."""
    cfg = AppConfig.from_sources()
    if outputDir is not None:
        cfg = replace(cfg, output_dir=Path(outputDir).expanduser())
    monitor = RunMonitor()
    llm = LLMClient(api_key=cfg.api_key, recorder=monitor, quota=None)
    loader = TemplateLoader(cfg.templates_dir)
    prompt = loader.latex_to_md_prompt()
    tex_files = _coerce_tex(source, pattern=filePattern)
    default_model = model or GEMINI_2_FLASH_THINKING_EXP

    for tex_file in tex_files:
        out_dir = cfg.output_dir or tex_file.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{tex_file.stem}.md"
        if skipExisting and out_path.exists():
            continue
        latex_text = tex_file.read_text()
        markdown = llm.latex_to_markdown(model=default_model, prompt=prompt, latex_text=latex_text, metadata={"source": str(tex_file)})
        out_path.write_text(markdown + "\n")


def LatexToJson(
    source: Iterable[str | Path] | str | Path,
    outputDir: Path | None = None,
    filePattern: str = "*.tex",
    skipExisting: bool = True,
    model: str | None = None,
    recursive: bool = False,
) -> None:
    """Convert LaTeX tables/structured content into JSON via Gemini."""
    cfg = AppConfig.from_sources()
    if outputDir is not None:
        cfg = replace(cfg, output_dir=Path(outputDir).expanduser())
    monitor = RunMonitor()
    llm = LLMClient(api_key=cfg.api_key, recorder=monitor, quota=None)
    loader = TemplateLoader(cfg.templates_dir)
    prompt = loader.latex_to_json_prompt()
    tex_files = _coerce_tex(source, pattern=filePattern, recursive=recursive)
    default_model = model or GEMINI_2_FLASH_THINKING_EXP

    for tex_file in tex_files:
        out_dir = cfg.output_dir or tex_file.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{tex_file.stem}.json"
        if skipExisting and out_path.exists():
            continue
        latex_text = tex_file.read_text()
        json_text = llm.latex_to_json(model=default_model, prompt=prompt, latex_text=latex_text, metadata={"source": str(tex_file)})
        out_path.write_text(json_text.strip() + "\n")


def _coerce_tex(source: Iterable[str | Path] | str | Path, pattern: str, recursive: bool = False) -> list[Path]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_file():
            return [path]
        globber = path.rglob if recursive else path.glob
        return natsorted(globber(pattern))
    if isinstance(source, Iterable):
        return natsorted(Path(item) for item in source)
    raise ValueError("source must be a path, string, or iterable of paths")
