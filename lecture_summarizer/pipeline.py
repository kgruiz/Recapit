from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    MofNCompleteColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TaskID,
)

from .config import AppConfig
from .constants import (
    RATE_LIMITS,
    RATE_LIMIT_WINDOW_SEC,
    FULL_RESPONSE_DIRNAME,
    PAGE_IMAGES_DIRNAME,
    PICKLES_DIRNAME,
)
from .rate_limiter import TokenBucket
from .templates import TemplateLoader
from .llm import LLMClient
from .pdf import pdf_to_png, total_pages
from .clean import strip_code_fences, clean_latex
from .utils import ensure_dir, slugify


class Kind(Enum):
    SLIDES = "slides"
    LECTURE = "lecture"
    DOCUMENT = "document"
    IMAGE = "image"


class PDFMode(Enum):
    AUTO = "auto"
    IMAGES = "images"
    PDF = "pdf"


@dataclass
class Pipeline:
    cfg: AppConfig
    llm: LLMClient
    templates: TemplateLoader

    def _select_output_root(self, *, override: Path | None, fallback: Path) -> Path:
        if override is not None:
            return Path(override)
        if self.cfg.output_dir is not None:
            return self.cfg.output_dir
        return fallback

    def output_base_for(self, *, source: Path, override_root: Path | None = None) -> Path:
        root = self._select_output_root(override=override_root, fallback=source.parent)
        return root / slugify(source.stem)

    def _progress(self, *, transient: bool = False) -> Progress:
        return Progress(
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
            transient=transient,
        )

    def _instruction_for_kind(self, kind: Kind) -> tuple[str, str]:
        if kind == Kind.SLIDES:
            pre = self.templates.slide_preamble()
        elif kind == Kind.LECTURE:
            pre = self.templates.lecture_preamble()
        elif kind == Kind.DOCUMENT:
            pre = self.templates.document_preamble()
        elif kind == Kind.IMAGE:
            pre = self.templates.image_preamble()
        else:
            raise ValueError(kind)
        prompt_template = self.templates.prompt(kind.value)
        instr = prompt_template.replace("{{PREAMBLE}}", pre)
        return instr, pre

    def _bucket_for(self, model: str) -> TokenBucket:
        per_minute = RATE_LIMITS.get(model, 10)
        return TokenBucket(per_minute=per_minute, window_sec=RATE_LIMIT_WINDOW_SEC)

    def _combine_and_write(self, *, texts: list[str], preamble: str, base_dir: Path, output_name: str):
        combined = "\n".join(strip_code_fences(t) for t in texts if t is not None)
        if self.cfg.save_full_response:
            full_dir = ensure_dir(base_dir / FULL_RESPONSE_DIRNAME)
            (full_dir / f"{output_name}.txt").write_text(combined)
        cleaned = clean_latex(combined, preamble)
        (base_dir / f"{output_name}.tex").write_text(cleaned)

    def _transcribe_pdf_with_progress(
        self,
        progress: Progress,
        *,
        pdf: Path,
        kind: Kind,
        model: str,
        output_name: str | None,
        mode: PDFMode,
        output_root: Path | None,
        files_task: TaskID | None,
    ) -> None:
        bucket = self._bucket_for(model)
        instr, preamble = self._instruction_for_kind(kind)

        base_dir_path = self.output_base_for(source=pdf, override_root=output_root)
        base_dir = ensure_dir(base_dir_path)
        output_name = output_name or f"{pdf.stem}-transcribed"

        strategy = mode
        if isinstance(strategy, str):
            strategy = PDFMode(strategy)
        if strategy == PDFMode.AUTO:
            strategy = PDFMode.PDF if self.llm.supports(model, "pdf") else PDFMode.IMAGES

        if strategy == PDFMode.PDF and not self.llm.supports(model, "pdf"):
            raise ValueError(f"Model {model} does not support PDF inputs")

        if strategy == PDFMode.PDF:
            texts: list[str] = []
            try:
                page_total = total_pages([pdf])
            except Exception:
                page_total = 1
            if page_total <= 0:
                page_total = 1
            task_label = f"{pdf.name} ({page_total} page{'s' if page_total != 1 else ''})"
            page_task = progress.add_task(f"Transcribing {task_label}", total=page_total)
            bucket.acquire()
            text = self.llm.transcribe_pdf(model=model, instruction=instr, pdf_path=pdf)
            texts.append(text)
            progress.update(page_task, advance=page_total)
            self._combine_and_write(texts=texts, preamble=preamble, base_dir=base_dir, output_name=output_name)
            if files_task is not None:
                progress.update(files_task, advance=1)
            return

        pages_dir = ensure_dir(base_dir / PAGE_IMAGES_DIRNAME)
        images = pdf_to_png(pdf, pages_dir, prefix=output_name)
        page_total = max(len(images), 1)
        task_label = f"{pdf.name} ({page_total} page{'s' if page_total != 1 else ''})"
        page_task = progress.add_task(f"Transcribing {task_label}", total=page_total)

        texts: list[str] = []
        for img in images:
            bucket.acquire()
            text = self.llm.transcribe_image(model=model, instruction=instr, image_path=img)
            texts.append(text)
            progress.update(page_task, advance=1)
        self._combine_and_write(texts=texts, preamble=preamble, base_dir=base_dir, output_name=output_name)
        if files_task is not None:
            progress.update(files_task, advance=1)

    def transcribe_pdf(
        self,
        *,
        pdf: Path,
        kind: Kind,
        model: str,
        output_name: str | None = None,
        mode: PDFMode = PDFMode.AUTO,
        progress: Progress | None = None,
        output_root: Path | None = None,
        files_task: TaskID | None = None,
    ):
        if progress is None:
            with self._progress() as local_progress:
                self._transcribe_pdf_with_progress(
                    local_progress,
                    pdf=pdf,
                    kind=kind,
                    model=model,
                    output_name=output_name,
                    mode=mode,
                    output_root=output_root,
                    files_task=files_task,
                )
        else:
            self._transcribe_pdf_with_progress(
                progress,
                pdf=pdf,
                kind=kind,
                model=model,
                output_name=output_name,
                mode=mode,
                output_root=output_root,
                files_task=files_task,
            )

    def transcribe_images(
        self,
        *,
        images: Iterable[Path],
        kind: Kind,
        model: str,
        output_dir: Path | None = None,
        bulk: bool = False,
    ):
        bucket = self._bucket_for(model)
        instr, preamble = self._instruction_for_kind(kind)

        images = list(images)
        if not images:
            return

        if bulk:
            resolved_root = self._select_output_root(
                override=output_dir,
                fallback=images[0].parent,
            )
            output_name = "bulk-transcribed"
            texts: list[str] = []
            base_dir = ensure_dir(resolved_root)
            with self._progress() as progress:
                task = progress.add_task("Transcribing images", total=len(images))
                for img in images:
                    bucket.acquire()
                    text = self.llm.transcribe_image(model=model, instruction=instr, image_path=img)
                    texts.append(text)
                    progress.update(task, advance=1)
            self._combine_and_write(texts=texts, preamble=preamble, base_dir=base_dir, output_name=output_name)
            return

        with self._progress() as progress:
            task = progress.add_task("Transcribing images", total=len(images))
            for img in images:
                out_dir = ensure_dir(self.output_base_for(source=img, override_root=output_dir))
                output_name = f"{img.stem}-transcribed"
                bucket.acquire()
                text = self.llm.transcribe_image(model=model, instruction=instr, image_path=img)
                self._combine_and_write(texts=[text], preamble=preamble, base_dir=out_dir, output_name=output_name)
                progress.update(task, advance=1)

    def latex_to_markdown(
        self,
        *,
        tex_file: Path,
        model: str,
        output_dir: Path | None = None,
        output_name: str | None = None,
    ):
        prompt = self.templates.latex_to_md_prompt()
        latex_text = tex_file.read_text()
        text = self.llm.latex_to_markdown(model=model, prompt=prompt, latex_text=latex_text)
        resolved_root = self._select_output_root(override=output_dir, fallback=tex_file.parent)
        out_dir = ensure_dir(resolved_root / slugify(tex_file.stem))
        out_name = (output_name or tex_file.stem) + ".md"
        (out_dir / out_name).write_text(text)

    def latex_to_json(
        self,
        *,
        tex_file: Path,
        model: str,
        output_dir: Path | None = None,
        output_name: str | None = None,
    ):
        prompt = self.templates.latex_to_json_prompt()
        latex_text = tex_file.read_text()
        text = self.llm.latex_to_json(model=model, prompt=prompt, latex_text=latex_text)
        resolved_root = self._select_output_root(override=output_dir, fallback=tex_file.parent)
        out_dir = ensure_dir(resolved_root / slugify(tex_file.stem))
        out_name = (output_name or tex_file.stem) + ".json"
        (out_dir / out_name).write_text(text)
