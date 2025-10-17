from __future__ import annotations
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
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
    TOKEN_LIMITS_PER_MINUTE,
    FULL_RESPONSE_DIRNAME,
    PAGE_IMAGES_DIRNAME,
    PICKLES_DIRNAME,
    VIDEO_CACHE_DIR,
)
from .rate_limiter import TokenBucket
from .templates import TemplateLoader
from .llm import LLMClient
from .pdf import pdf_to_png, total_pages
from .clean import strip_code_fences, clean_latex
from .telemetry import RunMonitor
from .quota import QuotaMonitor, QuotaConfig
from .utils import ensure_dir, slugify
from .video import (
    DEFAULT_MAX_CHUNK_BYTES,
    DEFAULT_MAX_CHUNK_SECONDS,
    DEFAULT_TOKENS_PER_SECOND,
    VideoChunk,
    assess_video_normalization,
    normalize_video,
    plan_video_chunks,
    probe_video,
    select_encoder_chain,
    sha256sum,
)


logger = logging.getLogger(__name__)


_INDENT_STEP = "  "
_SUBTASK_PREFIX = "|_ "


def _format_timestamp(seconds: float) -> str:
    total = max(int(round(seconds)), 0)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


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

    def __post_init__(self):
        self._bucket_lock = Lock()
        self._rate_limiters: dict[str, TokenBucket] = {}
        existing_monitor = getattr(self.llm, "_recorder", None)
        if isinstance(existing_monitor, RunMonitor):
            self.monitor = existing_monitor
        else:
            self.monitor = RunMonitor()
            self.llm.set_recorder(self.monitor)

        existing_quota = getattr(self.llm, "_quota", None)
        if isinstance(existing_quota, QuotaMonitor):
            self.quota = existing_quota
        else:
            quota_config = QuotaConfig(
                request_limits=RATE_LIMITS,
                token_limits=TOKEN_LIMITS_PER_MINUTE,
            )
            self.quota = QuotaMonitor(quota_config)
            self.llm.set_quota_monitor(self.quota)

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
        with self._bucket_lock:
            bucket = self._rate_limiters.get(model)
            if bucket is None:
                per_minute = RATE_LIMITS.get(model, 10)
                bucket = TokenBucket(per_minute=per_minute, window_sec=RATE_LIMIT_WINDOW_SEC, label=model)
                self._rate_limiters[model] = bucket
            return bucket

    def _acquire_token(self, bucket: TokenBucket, model: str) -> None:
        if hasattr(self, "quota") and self.quota is not None:
            utilization = bucket.utilization()
            self.quota.check_rpm(model, utilization, bucket.per_minute, bucket.window_sec)
        bucket.acquire()

    def _combine_and_write(
        self,
        *,
        texts: list[str],
        preamble: str,
        base_dir: Path,
        output_name: str,
        media_kind: Kind | None = None,
        chunk_metadata: list[VideoChunk] | None = None,
    ) -> Path:
        cleaned_texts = [strip_code_fences(t or "") for t in texts]
        combined = "\n".join(t for t in cleaned_texts if t)
        full_dir: Path | None = None
        if self.cfg.save_full_response:
            full_dir = ensure_dir(base_dir / FULL_RESPONSE_DIRNAME)
            (full_dir / f"{output_name}.txt").write_text(combined)
        if media_kind == Kind.VIDEO:
            sections: list[str] = []
            chunks = chunk_metadata or []
            for idx, text in enumerate(cleaned_texts):
                body = text.strip()
                if not body:
                    continue
                chunk = chunks[idx] if idx < len(chunks) else None
                if chunk is not None:
                    start_label = _format_timestamp(chunk.start_seconds)
                    end_label = _format_timestamp(chunk.end_seconds)
                    heading = f"\\section*{{Chunk {idx + 1} ({start_label}–{end_label})}}"
                else:
                    heading = f"\\section*{{Chunk {idx + 1}}}"
                sections.append(f"{heading}\n{body}")
            combined = "\n\n".join(sections)
        cleaned = clean_latex(combined, preamble)
        output_path = base_dir / f"{output_name}.tex"
        output_path.write_text(cleaned)
        if media_kind == Kind.VIDEO and full_dir is not None:
            chunks = chunk_metadata or []
            chunk_dir = ensure_dir(full_dir / "chunks")
            for idx, text in enumerate(cleaned_texts):
                chunk = chunks[idx] if idx < len(chunks) else None
                if chunk is None:
                    start_label = "unknown"
                else:
                    start_label = _format_timestamp(chunk.start_seconds)
                chunk_path = chunk_dir / f"{output_name}-chunk{idx:02d}.txt"
                chunk_path.write_text(text.strip() + "\n")
        return output_path

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
            self._acquire_token(bucket, model)
            metadata = {
                "kind": kind.value,
                "mode": "pdf",
                "output_name": output_name,
                "page_total": page_total,
            }
            text = self.llm.transcribe_pdf(model=model, instruction=instr, pdf_path=pdf, metadata=metadata)
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
        for idx, img in enumerate(images):
            self._acquire_token(bucket, model)
            metadata = {
                "kind": kind.value,
                "mode": "image",
                "page_index": idx,
                "page_total": page_total,
                "output_name": output_name,
                "source_pdf": str(pdf),
            }
            text = self.llm.transcribe_image(model=model, instruction=instr, image_path=img, metadata=metadata)
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
        mode: PDFMode = PDFMode.IMAGES,
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
                for idx, img in enumerate(images):
                    self._acquire_token(bucket, model)
                    metadata = {
                        "kind": kind.value,
                        "bulk": True,
                        "index": idx,
                        "total": len(images),
                        "output_name": output_name,
                    }
                    text = self.llm.transcribe_image(
                        model=model,
                        instruction=instr,
                        image_path=img,
                        metadata=metadata,
                    )
                    texts.append(text)
                    progress.update(task, advance=1)
            self._combine_and_write(texts=texts, preamble=preamble, base_dir=base_dir, output_name=output_name)
            return

        with self._progress() as progress:
            task = progress.add_task(
                self._format_task_description("Transcribing images", level=0),
                total=len(images),
            )
            for idx, img in enumerate(images):
                out_dir = ensure_dir(self.output_base_for(source=img, override_root=output_dir))
                output_name = f"{img.stem}-transcribed"
                self._acquire_token(bucket, model)
                metadata = {
                    "kind": kind.value,
                    "bulk": False,
                    "output_name": output_name,
                    "index": idx,
                    "total": len(images),
                }
                text = self.llm.transcribe_image(
                    model=model,
                    instruction=instr,
                    image_path=img,
                    metadata=metadata,
                )
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
        configured_tokens_per_sec = tokens_per_second if tokens_per_second and tokens_per_second > 0 else None
        default_tokens_per_sec = configured_tokens_per_sec or DEFAULT_TOKENS_PER_SECOND

        if not self.llm.supports(model, "video"):
            raise ValueError(f"Model {model} does not support video inputs")

        encoder_chain, encoder_messages = select_encoder_chain(self.cfg.video_encoder_preference)

        bucket = self._bucket_for(model)
        instr, preamble = self._instruction_for_kind(Kind.VIDEO)

        single_video = len(videos) == 1
        with self._progress() as progress:
            for message in encoder_messages:
                logger.debug("video-encoder: %s", message)
            files_task: TaskID | None = None
            if not single_video:
                files_task = progress.add_task(
                    self._format_task_description("Videos", level=0),
                    total=len(videos),
                )
            for video_path in videos:
                base_dir = ensure_dir(self.output_base_for(source=video_path, override_root=output_dir))
                output_name = f"{video_path.stem}-transcribed"
                tex_path = base_dir / f"{output_name}.tex"
                if skip_existing and tex_path.exists():
                    logger.info(
                        "Skipping %s (existing outputs). Use --no-skip-existing to regenerate.",
                        video_path.name,
                    )
                    if files_task is not None:
                        progress.update(files_task, advance=1)
                    continue
                logger.debug(
                    "%s: selected base_dir=%s output_name=%s",
                    video_path.name,
                    base_dir,
                    output_name,
                )
                video_task: TaskID | None = None
                if not single_video:
                    video_task = progress.add_task(
                        self._format_task_description(f"Transcribing {video_path.name}", level=1),
                        total=1,
                    )
                else:
                    video_task = progress.add_task(
                        self._format_task_description(f"Transcribing {video_path.name}", level=0),
                        total=1,
                    )

                source_hash = sha256sum(video_path)
                cache_key = f"{slugify(video_path.stem)}-{source_hash[:12]}"
                cache_dir = VIDEO_CACHE_DIR / cache_key
                manifest_path = cache_dir / "chunks.json"
                chunk_output_dir = base_dir / PICKLES_DIRNAME / "video-chunks"
                normalized_output_path = base_dir / PICKLES_DIRNAME / f"{video_path.stem}-normalized.mp4"
                normalized_path: Path = video_path
                keep_intermediates = self.cfg.save_intermediates
                logger.debug("%s: cache location %s", video_path.name, cache_dir)
                manifest_data: dict[str, object] | None = None
                if not keep_intermediates and manifest_path.exists():
                    try:
                        manifest_path.unlink()
                    except OSError as exc:
                        logger.warning(
                            "%s: failed to remove stale manifest: %s",
                            video_path.name,
                            exc,
                        )
                if keep_intermediates and manifest_path.exists():
                    try:
                        manifest_data = json.loads(manifest_path.read_text())
                    except Exception as exc:
                        logger.warning(
                            "%s: failed to load %s: %s",
                            video_path.name,
                            manifest_path.name,
                            exc,
                        )
                cached_source_hash = (
                    manifest_data.get("source_hash") if isinstance(manifest_data, dict) else None
                )
                cached_normalized_hash = (
                    manifest_data.get("normalized_hash") if isinstance(manifest_data, dict) else None
                )
                cached_token_info = (
                    manifest_data.get("token_count") if isinstance(manifest_data, dict) else None
                ) if keep_intermediates else None
                cached_encoder_effective = (
                    manifest_data.get("encoder_effective") if isinstance(manifest_data, dict) else None
                )
                cached_encoder_codec = (
                    manifest_data.get("encoder_codec") if isinstance(manifest_data, dict) else None
                )
                cached_encoder_accelerated = (
                    manifest_data.get("encoder_accelerated") if isinstance(manifest_data, dict) else None
                )
                cached_encoder_known = (
                    manifest_data.get("encoder_known") if isinstance(manifest_data, dict) else None
                )

                acceptable, checks, source_meta = assess_video_normalization(video_path)
                normalize_task: TaskID | None = None
                normalized_hash: str | None = None
                encoder_effective_pref: str | None = None
                encoder_effective_codec: str | None = None
                encoder_accelerated = False
                encoder_known = False
                encoder_diag: list[str] = []
                # The quick checks above ensure the bare minimum: readable container, H.264 video, AAC audio,
                # sensible sample rate, and a fast-start layout. They intentionally do **not** replicate every
                # invariant the ffmpeg normalize pass enforces (e.g., keyframe cadence, bitrate caps). If any
                # check fails—or we simply want deterministic behavior—we defer to the full normalization path.
                if acceptable:
                    logger.debug("%s: source passes normalization checks; using original file", video_path.name)
                    if normalized_output_path.exists():
                        try:
                            normalized_output_path.unlink()
                            logger.debug("%s: removed normalized copy (no longer needed)", video_path.name)
                        except OSError as exc:
                            logger.warning(
                                "%s: failed to remove normalized copy: %s",
                                video_path.name,
                                exc,
                            )
                    normalized_hash = source_hash
                    encoder_effective_pref = "source"
                    encoder_effective_codec = (source_meta.video_codec if source_meta else None) or "unknown"
                    encoder_accelerated = False
                    encoder_known = True
                else:
                    failing = ", ".join(name for name, ok in checks.items() if not ok) or "unknown"
                    logger.debug("%s: normalization required (failing checks: %s)", video_path.name, failing)
                    reuse_normalized = False
                    if (
                        keep_intermediates
                        and cached_source_hash == source_hash
                        and cached_normalized_hash
                        and normalized_output_path.exists()
                    ):
                        candidate_hash = sha256sum(normalized_output_path)
                        if candidate_hash == cached_normalized_hash:
                            reuse_normalized = True
                            normalized_path = normalized_output_path
                            normalized_hash = candidate_hash
                            logger.debug("%s: reusing normalized file (%s)", video_path.name, normalized_path.name)
                            encoder_effective_pref = (
                                str(cached_encoder_effective) if cached_encoder_effective else "unknown"
                            )
                            encoder_effective_codec = (
                                str(cached_encoder_codec) if cached_encoder_codec else "unknown"
                            )
                            encoder_accelerated = bool(cached_encoder_accelerated) if cached_encoder_accelerated is not None else False
                            encoder_known = bool(cached_encoder_known) if cached_encoder_known is not None else False
                            encoder_diag.append("Reused cached normalized artifact (hash match)")
                    if not reuse_normalized:
                        normalize_task = progress.add_task(
                            self._format_task_description(f"Normalizing {video_path.name}", level=2), total=1
                        )
                        logger.debug("%s: starting normalization", video_path.name)
                        try:
                            ensure_dir(normalized_output_path.parent)
                            result = normalize_video(
                                video_path,
                                output_dir=normalized_output_path.parent,
                                encoder_chain=encoder_chain,
                            )
                            normalized_path = result.path
                            encoder_effective_pref = result.encoder.preference.value
                            encoder_effective_codec = result.encoder.codec
                            encoder_accelerated = result.encoder.accelerated
                            encoder_known = result.encoder_known
                            encoder_diag.extend(result.diagnostics)
                            for message in result.diagnostics:
                                logger.debug("%s: %s", video_path.name, message)
                            logger.debug("%s: finished normalization -> %s", video_path.name, normalized_path.name)
                            progress.update(normalize_task, advance=1)
                        finally:
                            if normalize_task is not None:
                                progress.remove_task(normalize_task)
                        normalized_hash = sha256sum(normalized_path)

                if normalized_hash is None:
                    normalized_hash = sha256sum(normalized_path)
                logger.debug(
                    "%s: source_hash=%s normalized_hash=%s",
                    video_path.name,
                    source_hash[:12],
                    normalized_hash[:12],
                )
                if encoder_effective_pref is None:
                    encoder_effective_pref = "unknown"
                if encoder_effective_codec is None:
                    encoder_effective_codec = "unknown"

                normalized_meta = probe_video(normalized_path)
                if not encoder_effective_codec or encoder_effective_codec == "unknown":
                    encoder_effective_codec = (normalized_meta.video_codec or "unknown")
                logger.debug(
                    "%s: encoder_effective=%s codec=%s accelerated=%s known=%s",
                    video_path.name,
                    encoder_effective_pref,
                    encoder_effective_codec,
                    encoder_accelerated,
                    encoder_known,
                )
                if source_meta and abs(normalized_meta.duration_seconds - source_meta.duration_seconds) > 1.0:
                    logger.warning(
                        "%s: normalized duration %.2fs differs from source %.2fs",
                        video_path.name,
                        normalized_meta.duration_seconds,
                        source_meta.duration_seconds,
                    )
                tokens_per_sec = default_tokens_per_sec
                token_count_payload: dict[str, object] | None = None
                if configured_tokens_per_sec is None and normalized_meta.duration_seconds > 0:
                    cached_token_valid = (
                        isinstance(cached_token_info, dict)
                        and cached_token_info.get("model") == model
                        and cached_token_info.get("normalized_hash") == normalized_hash
                    )
                    if cached_token_valid:
                        logger.debug("%s: using cached token count", video_path.name)
                        total_tokens_cached = cached_token_info.get("total_tokens")
                        if total_tokens_cached:
                            tokens_per_sec = max(
                                float(total_tokens_cached) / max(normalized_meta.duration_seconds, 1e-6),
                                1.0,
                            )
                        elif cached_token_info.get("tokens_per_second"):
                            tokens_per_sec = max(float(cached_token_info["tokens_per_second"]), 1.0)
                        token_count_payload = dict(cached_token_info)
                        token_count_payload.setdefault("model", model)
                        token_count_payload["normalized_hash"] = normalized_hash
                        token_count_payload.setdefault(
                            "tokens_per_second", tokens_per_sec
                        )
                        token_count_payload.setdefault(
                            "timestamp", datetime.now(timezone.utc).isoformat()
                        )
                    else:
                        token_task: TaskID | None = None
                        if single_video:
                            progress.update(video_task, description=self._format_task_description("Counting tokens", level=0))
                        else:
                            token_task = progress.add_task(
                                self._format_task_description(f"Counting tokens for {video_path.name}", level=2), total=1
                            )
                        logger.debug("%s: starting token counting", video_path.name)
                        try:
                            token_count_response = self.llm.count_video_tokens(
                                model=model,
                                instruction=instr,
                                video_path=normalized_path,
                                fps=fps_override,
                                metadata={
                                    "video_path": str(video_path),
                                    "normalized_path": str(normalized_path),
                                    "cache_key": cache_key,
                                },
                            )
                            observed_total = getattr(token_count_response, "total_tokens", None)
                            if observed_total is not None and observed_total > 0:
                                tokens_per_sec = max(
                                    float(observed_total) / max(normalized_meta.duration_seconds, 1e-6),
                                    1.0,
                                )
                            token_count_payload = {
                                "model": model,
                                "normalized_hash": normalized_hash,
                                "total_tokens": getattr(token_count_response, "total_tokens", None),
                                "cached_tokens": getattr(token_count_response, "cached_tokens", None),
                                "tokens_per_second": tokens_per_sec,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                            logger.debug(
                                "%s: finished token counting with total_tokens=%s",
                                video_path.name,
                                token_count_payload["total_tokens"],
                            )
                            if single_video:
                                progress.update(video_task, description=self._format_task_description(f"Transcribing {video_path.name}", level=0))
                            elif token_task is not None:
                                progress.update(token_task, advance=1)
                        except Exception as exc:
                            logger.warning("Token counting failed for %s: %s. Using defaults.", video_path.name, exc)
                            if isinstance(cached_token_info, dict):
                                logger.debug("%s: falling back to cached token count", video_path.name)
                                token_count_payload = dict(cached_token_info)
                                token_count_payload.setdefault("model", model)
                                token_count_payload["normalized_hash"] = normalized_hash
                                total_tokens_cached = token_count_payload.get("total_tokens")
                                if total_tokens_cached:
                                    tokens_per_sec = max(
                                        float(total_tokens_cached) / max(normalized_meta.duration_seconds, 1e-6),
                                        1.0,
                                    )
                                elif token_count_payload.get("tokens_per_second"):
                                    tokens_per_sec = max(float(token_count_payload["tokens_per_second"]), 1.0)
                                token_count_payload.setdefault(
                                    "tokens_per_second", tokens_per_sec
                                )
                                token_count_payload.setdefault(
                                    "timestamp", datetime.now(timezone.utc).isoformat()
                                )
                            else:
                                logger.debug(
                                    "%s: falling back to tokens_per_second=%.2f",
                                    video_path.name,
                                    tokens_per_sec,
                                )
                            if token_task is not None:
                                progress.update(token_task, advance=1)
                        finally:
                            if single_video:
                                progress.update(video_task, description=self._format_task_description(f"Transcribing {video_path.name}", level=0))
                            elif token_task is not None:
                                progress.remove_task(token_task)
                elif configured_tokens_per_sec is not None:
                    tokens_per_sec = configured_tokens_per_sec
                    logger.debug(
                        "%s: using override tokens_per_second=%.2f",
                        video_path.name,
                        tokens_per_sec,
                    )
                else:
                    logger.debug(
                        "%s: skipping token counting (duration %.2fs)",
                        video_path.name,
                        normalized_meta.duration_seconds,
                    )

                logger.debug(
                    "%s: planning with tokens_per_second=%.2f token_limit=%s",
                    video_path.name,
                    tokens_per_sec,
                    effective_token_limit,
                )
                logger.debug("%s: constructing chunk plan", video_path.name)

                plan = plan_video_chunks(
                    normalized_meta,
                    normalized_path=normalized_path,
                    max_seconds=chunk_seconds,
                    max_bytes=chunk_bytes,
                    token_limit=effective_token_limit,
                    tokens_per_second=tokens_per_sec,
                    chunk_dir=chunk_output_dir,
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
                    "encoder_requested": self.cfg.video_encoder_preference.value,
                    "encoder_effective": encoder_effective_pref,
                    "encoder_codec": encoder_effective_codec,
                    "encoder_accelerated": bool(encoder_accelerated),
                    "encoder_known": bool(encoder_known),
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
                if token_count_payload is not None:
                    manifest_payload["token_count"] = token_count_payload
                    logger.debug(
                        "%s: total_tokens=%s cached_tokens=%s observed_tokens_per_second=%.2f",
                        video_path.name,
                        token_count_payload.get("total_tokens"),
                        token_count_payload.get("cached_tokens"),
                        tokens_per_sec,
                    )
                else:
                    manifest_payload.pop("token_count", None)
                    logger.debug("%s: no token count available; using manifest defaults", video_path.name)
                manifest_payload["source_hash"] = source_hash
                manifest_payload["normalized_hash"] = normalized_hash
                if encoder_diag:
                    manifest_payload["encoder_diagnostics"] = encoder_diag
                if keep_intermediates:
                    ensure_dir(cache_dir)
                    manifest_path.write_text(json.dumps(manifest_payload, indent=2, sort_keys=True))
                elif manifest_path.exists():
                    try:
                        manifest_path.unlink()
                    except OSError:
                        pass
                logger.debug(
                    "%s: wrote manifest %s with %s chunk entries",
                    video_path.name,
                    manifest_path.name,
                    len(manifest_payload["chunks"]),
                )

                chunk_total = max(len(plan.chunks), 1)
                logger.debug(
                    "%s: chunk_total=%s chunk_duration≈%.2fs",
                    video_path.name,
                    chunk_total,
                    (plan.chunks[0].duration_seconds if plan.chunks else 0),
                )
                if chunk_total > 1 and plan.chunks:
                    logger.debug("%s: first_chunk=%s", video_path.name, plan.chunks[0].path.name)
                logger.debug("%s: starting transcription loop", video_path.name)
                chunk_level = 1 if single_video else 2
                chunk_description = (
                    f"{video_path.name} chunks" if not single_video else "Chunks"
                )
                chunk_task = progress.add_task(
                    self._format_task_description(chunk_description, level=chunk_level),
                    total=chunk_total,
                )
                texts: list[str] = [""] * chunk_total

                def _run_chunk(idx_chunk: tuple[int, VideoChunk]) -> tuple[int, str]:
                    idx, chunk = idx_chunk
                    self._acquire_token(bucket, model)
                    metadata = {
                        "video_path": str(video_path),
                        "chunk_path": str(chunk.path),
                        "chunk_index": idx,
                        "chunk_count": chunk_total,
                        "chunk_start_seconds": chunk.start_seconds,
                        "chunk_end_seconds": chunk.end_seconds,
                        "normalized_path": str(normalized_path),
                        "cache_key": cache_key,
                    }
                    response = self.llm.transcribe_video(
                        model=model,
                        instruction=instr,
                        video_path=chunk.path,
                        fps=fps_override,
                        media_resolution=media_resolution,
                        thinking_budget=thinking_budget,
                        include_thoughts=include_thoughts,
                        metadata=metadata,
                    )
                    return idx, (response.text or "").strip()

                chunk_workers = min(self.cfg.max_video_workers, chunk_total)
                if chunk_workers <= 1:
                    for idx, chunk in enumerate(plan.chunks):
                        logger.debug(
                            "%s: transcribing chunk %s (%.2fs→%.2fs)",
                            video_path.name,
                            chunk.index,
                            chunk.start_seconds,
                            chunk.end_seconds,
                        )
                        _, text = _run_chunk((idx, chunk))
                        texts[idx] = text
                        progress.update(chunk_task, advance=1)
                else:
                    idx_chunks = list(enumerate(plan.chunks))
                    with ThreadPoolExecutor(max_workers=chunk_workers) as executor:
                        futures = {executor.submit(_run_chunk, item): item for item in idx_chunks}
                        for future in as_completed(futures):
                            idx, text = future.result()
                            texts[idx] = text
                            _, chunk = futures[future]
                            logger.debug(
                                "%s: chunk %s complete (%.2fs→%.2fs)",
                                video_path.name,
                                chunk.index,
                                chunk.start_seconds,
                                chunk.end_seconds,
                            )
                            progress.update(chunk_task, advance=1)

                written_path = self._combine_and_write(
                    texts=texts,
                    preamble=preamble,
                    base_dir=base_dir,
                    output_name=output_name,
                    media_kind=Kind.VIDEO,
                    chunk_metadata=plan.chunks,
                )
                logger.debug("%s: finished transcription and writing outputs", video_path.name)
                logger.info("Saved %s", written_path)
                if not keep_intermediates:
                    if normalized_output_path.exists() and normalized_output_path != video_path:
                        try:
                            normalized_output_path.unlink()
                        except OSError:
                            pass
                    for chunk in plan.chunks:
                        if chunk.path == normalized_path:
                            continue
                        try:
                            if chunk.path.exists():
                                chunk.path.unlink()
                        except OSError:
                            pass
                    if chunk_output_dir.exists() and not any(chunk_output_dir.iterdir()):
                        try:
                            chunk_output_dir.rmdir()
                        except OSError:
                            pass
                if video_task is not None:
                    progress.update(video_task, advance=1)
                if files_task is not None:
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
        metadata = {
            "source_path": str(tex_file),
            "output_name": output_name or tex_file.stem,
        }
        text = self.llm.latex_to_markdown(model=model, prompt=prompt, latex_text=latex_text, metadata=metadata)
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
        metadata = {
            "source_path": str(tex_file),
            "output_name": output_name or tex_file.stem,
        }
        text = self.llm.latex_to_json(model=model, prompt=prompt, latex_text=latex_text, metadata=metadata)
        resolved_root = self._select_output_root(override=output_dir, fallback=tex_file.parent)
        out_dir = ensure_dir(resolved_root / slugify(tex_file.stem))
        out_name = (output_name or tex_file.stem) + ".json"
        (out_dir / out_name).write_text(text)
