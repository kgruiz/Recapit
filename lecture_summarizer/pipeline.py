from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Callable

from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    MofNCompleteColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
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


@dataclass
class Pipeline:
    cfg: AppConfig
    llm: LLMClient
    templates: TemplateLoader

    def _progress(self) -> Progress:
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
            transient=True,
        )

    def _instruction_for_kind(self, kind: Kind) -> tuple[str, str]:
        if kind == Kind.SLIDES:
            pre = self.templates.slide_preamble()
            instr = (
                "Transcribe the slide image, including math, in LaTeX. "
                "Use the given preamble as base. Escape &, %. "
                "Do not include external files. For graphics, recreate with tikz or leave a placeholder.\n\n"
                f"LaTeX Preamble:{pre}"
            )
            return instr, pre
        if kind == Kind.LECTURE:
            pre = self.templates.lecture_preamble()
            instr = (
                "Transcribe the lecture page, including math, in LaTeX. "
                "Use the given preamble as base. Escape &, %. No external files. "
                "For graphics, use tikz or leave placeholder.\n\n"
                f"LaTeX Preamble:{pre}"
            )
            return instr, pre
        if kind == Kind.DOCUMENT:
            pre = self.templates.document_preamble()
            instr = (
                "Transcribe the document page, including math, in LaTeX. "
                "Put tables into LaTeX tabular. Use the given preamble as base. "
                "Escape special characters. No need to add page numbers. "
                "For graphics, tikz or placeholder.\n\n"
                f"LaTeX Preamble:{pre}"
            )
            return instr, pre
        if kind == Kind.IMAGE:
            pre = self.templates.image_preamble()
            instr = (
                "Transcribe the image, including math, in LaTeX. "
                "Use the given preamble as base. Escape &, %. "
                "Graphics: tikz or placeholder.\n\n"
                f"LaTeX Preamble:{pre}"
            )
            return instr, pre
        raise ValueError(kind)

    def _bucket_for(self, model: str) -> TokenBucket:
        return TokenBucket(per_minute=RATE_LIMITS[model], window_sec=RATE_LIMIT_WINDOW_SEC)

    def _combine_and_write(self, *, texts: list[str], preamble: str, base_dir: Path, output_name: str):
        full_dir = ensure_dir(base_dir / FULL_RESPONSE_DIRNAME)
        combined = "\n".join(strip_code_fences(t) for t in texts if t is not None)
        (full_dir / f"{output_name}.txt").write_text(combined)
        cleaned = clean_latex(combined, preamble)
        (base_dir / f"{output_name}.tex").write_text(cleaned)

    def transcribe_pdf(self, *, pdf: Path, kind: Kind, model: str, output_name: str | None = None):
        bucket = self._bucket_for(model)
        instr, preamble = self._instruction_for_kind(kind)

        base_dir = self.cfg.output_dir / slugify(pdf.stem)
        pages_dir = ensure_dir(base_dir / PAGE_IMAGES_DIRNAME)
        output_name = output_name or f"{pdf.stem}-transcribed"

        images = pdf_to_png(pdf, pages_dir, prefix=output_name)

        texts: list[str] = []
        with self._progress() as progress:
            task = progress.add_task(f"Transcribing {pdf.name}", total=len(images))
            for img in images:
                bucket.acquire()
                text = self.llm.transcribe_image(model=model, instruction=instr, image_path=img)
                texts.append(text)
                progress.update(task, advance=1)
        self._combine_and_write(texts=texts, preamble=preamble, base_dir=base_dir, output_name=output_name)

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
        base_dir = (output_dir or self.cfg.output_dir)
        if bulk:
            output_name = "bulk-transcribed"
            texts: list[str] = []
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
                out_dir = ensure_dir(base_dir / slugify(img.stem))
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
        out_dir = (output_dir or (self.cfg.output_dir / slugify(tex_file.stem)))
        ensure_dir(out_dir)
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
        out_dir = (output_dir or (self.cfg.output_dir / slugify(tex_file.stem)))
        ensure_dir(out_dir)
        out_name = (output_name or tex_file.stem) + ".json"
        (out_dir / out_name).write_text(text)
