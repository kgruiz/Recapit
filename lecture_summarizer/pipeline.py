from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timezone
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
from .video import (
    DEFAULT_MAX_CHUNK_BYTES,
    DEFAULT_MAX_CHUNK_SECONDS,
    DEFAULT_TOKENS_PER_SECOND,
    normalize_video,
    plan_video_chunks,
    probe_video,
)


_INDENT_STEP = "  "
_SUBTASK_PREFIX = "|_ "


class Kind(Enum):
    SLIDES = "slides"
    LECTURE = "lecture"
    DOCUMENT = "document"
    IMAGE = "image"
    VIDEO = "video"


class PDFMode(Enum):
    AUTO = "auto"
    IMAGES = "images"
    PDF = "pdf"


@dataclass
class Pipeline:
    cfg: AppConfig
    llm: LLMClient
    templates: TemplateLoader

    def _format_task_description(self, description: str, *, level: int = 0) -> str:
        prefix_level = max(level, 0)
        prefix = _INDENT_STEP * prefix_level
        if prefix_level > 0:
            prefix += _SUBTASK_PREFIX
        return f"{prefix}{description}"

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
        elif kind == Kind.VIDEO:
            pre = self.templates.video_preamble()
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
        if not self.llm.supports(model, "video"):
            raise ValueError(f"Model {model} does not support video inputs")

        bucket = self._bucket_for(model)
        instr, preamble = self._instruction_for_kind(kind)

        base_dir_path = self.output_base_for(source=pdf, override_root=output_root)
        base_dir = ensure_dir(base_dir_path)
        output_name = output_name or f"{pdf.stem}-transcribed"

        task_level = 1 if files_task is not None else 0
        file_task = progress.add_task(
            self._format_task_description(f"Transcribing {pdf.name}", level=task_level),
            total=1,
        )

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
            page_task = progress.add_task(
                self._format_task_description(task_label, level=task_level + 1),
                total=page_total,
            )
            bucket.acquire()
            text = self.llm.transcribe_pdf(model=model, instruction=instr, pdf_path=pdf)
            texts.append(text)
            progress.update(page_task, advance=page_total)
            self._combine_and_write(texts=texts, preamble=preamble, base_dir=base_dir, output_name=output_name)
            progress.update(file_task, advance=1)
            if files_task is not None:
                progress.update(files_task, advance=1)
            return

        pages_dir = ensure_dir(base_dir / PAGE_IMAGES_DIRNAME)
        images = pdf_to_png(pdf, pages_dir, prefix=output_name)
        page_total = max(len(images), 1)
        task_label = f"{pdf.name} ({page_total} page{'s' if page_total != 1 else ''})"
        page_task = progress.add_task(
            self._format_task_description(task_label, level=task_level + 1),
            total=page_total,
        )

        texts: list[str] = []
        for img in images:
            bucket.acquire()
            text = self.llm.transcribe_image(model=model, instruction=instr, image_path=img)
            texts.append(text)
            progress.update(page_task, advance=1)
        self._combine_and_write(texts=texts, preamble=preamble, base_dir=base_dir, output_name=output_name)
        progress.update(file_task, advance=1)
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
                task = progress.add_task(
                    self._format_task_description("Transcribing images", level=0),
                    total=len(images),
                )
                for img in images:
                    bucket.acquire()
                    text = self.llm.transcribe_image(model=model, instruction=instr, image_path=img)
                    texts.append(text)
                    progress.update(task, advance=1)
            self._combine_and_write(texts=texts, preamble=preamble, base_dir=base_dir, output_name=output_name)
            return

        with self._progress() as progress:
            task = progress.add_task(
                self._format_task_description("Transcribing images", level=0),
                total=len(images),
            )
            for img in images:
                out_dir = ensure_dir(self.output_base_for(source=img, override_root=output_dir))
                output_name = f"{img.stem}-transcribed"
                bucket.acquire()
                text = self.llm.transcribe_image(model=model, instruction=instr, image_path=img)
                self._combine_and_write(texts=[text], preamble=preamble, base_dir=out_dir, output_name=output_name)
                progress.update(task, advance=1)

    def transcribe_videos(
        self,
        *,
        videos: Iterable[Path],
        model: str,
        output_dir: Path | None = None,
        skip_existing: bool = True,
        max_chunk_seconds: float | None = None,
        max_chunk_bytes: int | None = None,
        media_resolution: str | None = None,
        fps_override: float | None = None,
        thinking_budget: int | None = None,
        include_thoughts: bool = False,
        token_limit: int | None = None,
        tokens_per_second: float | None = None,
    ):
        videos = list(videos)
        if not videos:
            return

        chunk_seconds = max_chunk_seconds or DEFAULT_MAX_CHUNK_SECONDS
        chunk_bytes = max_chunk_bytes or DEFAULT_MAX_CHUNK_BYTES
        effective_token_limit = token_limit if token_limit and token_limit > 0 else self.cfg.video_token_limit
        tokens_per_sec = tokens_per_second if tokens_per_second and tokens_per_second > 0 else DEFAULT_TOKENS_PER_SECOND

        if not self.llm.supports(model, "video"):
            raise ValueError(f"Model {model} does not support video inputs")

        bucket = self._bucket_for(model)
        instr, preamble = self._instruction_for_kind(Kind.VIDEO)

        with self._progress() as progress:
            files_task = progress.add_task(
                self._format_task_description("Videos", level=0),
                total=len(videos),
            )
            for video_path in videos:
                base_dir = ensure_dir(self.output_base_for(source=video_path, override_root=output_dir))
                output_name = f"{video_path.stem}-transcribed"
                tex_path = base_dir / f"{output_name}.tex"
                if skip_existing and tex_path.exists():
                    progress.console.print(
                        f"[yellow]Skipping {video_path.name} (existing outputs). Use --no-skip-existing to regenerate.[/yellow]"
                    )
                    progress.update(files_task, advance=1)
                    continue

                video_task = progress.add_task(
                    self._format_task_description(f"Transcribing {video_path.name}", level=1),
                    total=1,
                )

                chunk_root = ensure_dir(base_dir / PICKLES_DIRNAME / "video-chunks")
                manifest_path = chunk_root / f"{slugify(video_path.stem)}-chunks.json"

                normalized_path = normalize_video(video_path, output_dir=chunk_root)
                normalized_meta = probe_video(normalized_path)
                plan = plan_video_chunks(
                    normalized_meta,
                    normalized_path=normalized_path,
                    max_seconds=chunk_seconds,
                    max_bytes=chunk_bytes,
                    token_limit=effective_token_limit,
                    tokens_per_second=tokens_per_sec,
                    chunk_dir=chunk_root,
                    manifest_path=manifest_path,
                )

                manifest_payload = {
                    "source": str(video_path),
                    "normalized": str(normalized_path),
                    "duration_seconds": normalized_meta.duration_seconds,
                    "size_bytes": normalized_meta.size_bytes,
                    "fps": normalized_meta.fps,
                    "video_codec": normalized_meta.video_codec,
                    "audio_codec": normalized_meta.audio_codec,
                    "model": model,
                    "token_limit": effective_token_limit,
                    "tokens_per_second": tokens_per_sec,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "chunks": [
                        {
                            "index": chunk.index,
                            "start_seconds": chunk.start_seconds,
                            "end_seconds": chunk.end_seconds,
                            "start_iso": chunk.start_iso,
                            "end_iso": chunk.end_iso,
                            "path": str(chunk.path),
                        }
                        for chunk in plan.chunks
                    ],
                }
                manifest_path.write_text(json.dumps(manifest_payload, indent=2, sort_keys=True))

                chunk_total = max(len(plan.chunks), 1)
                chunk_task = progress.add_task(
                    self._format_task_description(f"{video_path.name} chunks", level=2),
                    total=chunk_total,
                )
                texts: list[str] = []
                for chunk in plan.chunks:
                    bucket.acquire()
                    response = self.llm.transcribe_video(
                        model=model,
                        instruction=instr,
                        video_path=chunk.path,
                        start_offset=chunk.start_seconds if plan.requires_splitting() else None,
                        end_offset=chunk.end_seconds if plan.requires_splitting() else None,
                        fps=fps_override,
                        media_resolution=media_resolution,
                        thinking_budget=thinking_budget,
                        include_thoughts=include_thoughts,
                    )
                    texts.append((response.text or "").strip())
                    progress.update(chunk_task, advance=1)

                self._combine_and_write(texts=texts, preamble=preamble, base_dir=base_dir, output_name=output_name)
                progress.console.print(f"[green]Saved[/green] {tex_path}")
                progress.update(video_task, advance=1)
                progress.update(files_task, advance=1)

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
