from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PIL import Image, ImageDraw

from ..core.types import Asset, Job, PdfMode, SourceKind
from ..utils import slugify, ensure_dir
from ..video import (
    DEFAULT_MAX_CHUNK_BYTES,
    DEFAULT_MAX_CHUNK_SECONDS,
    DEFAULT_TOKENS_PER_SECOND,
    EncoderSpec,
    VideoChunkPlan,
    normalize_video,
    plan_video_chunks,
    probe_video,
    sha256sum,
)


@dataclass
class _VideoDependencies:
    normalizer: Callable[[Path, Path, Sequence[EncoderSpec] | None], object]
    probe: Callable[[Path], object]
    planner: Callable[..., VideoChunkPlan]
    hash_func: Callable[[Path], str]


class CompositeNormalizer:
    """Normalize assets for downstream processing (PDFs, videos, etc.)."""

    def __init__(
        self,
        *,
        image_root: Path | None = None,
        video_root: Path | None = None,
        encoder_chain: Sequence[EncoderSpec] | None = None,
        video_normalizer=normalize_video,
        video_probe=probe_video,
        video_planner=plan_video_chunks,
        hash_func=sha256sum,
    ) -> None:
        self._fallback_image_root = Path(image_root or Path(tempfile.gettempdir()) / "lecture-pdf-pages")
        self._fallback_image_root.mkdir(parents=True, exist_ok=True)
        self._fallback_video_root = Path(video_root or Path(tempfile.gettempdir()) / "lecture-video")
        self._fallback_video_root.mkdir(parents=True, exist_ok=True)
        self._encoder_chain = encoder_chain
        self._video = _VideoDependencies(video_normalizer, video_probe, video_planner, hash_func)
        self._job: Job | None = None
        self._last_chunk_info: list[dict] = []
        self._last_manifest_path: Path | None = None

    # Optional hook used by Engine/Planner
    def prepare(self, job: Job) -> None:  # pragma: no cover - simple setter
        self._job = job

    def normalize(self, assets: list[Asset], pdf_mode: PdfMode) -> list[Asset]:
        self._last_chunk_info = []
        self._last_manifest_path = None
        normalized: list[Asset] = []
        for asset in assets:
            if asset.media == "pdf":
                normalized.extend(self._normalize_pdf(asset, pdf_mode))
            elif asset.media in {"video", "audio"}:
                normalized.extend(self._normalize_video(asset))
            else:
                normalized.append(asset)
        return normalized

    def chunk_descriptors(self) -> list[dict]:
        return list(self._last_chunk_info)

    # ------------------------------------------------------------------
    # PDF handling
    # ------------------------------------------------------------------
    def _normalize_pdf(self, asset: Asset, pdf_mode: PdfMode) -> list[Asset]:
        if pdf_mode == PdfMode.PDF or pdf_mode == PdfMode.AUTO:
            return [asset]
        if pdf_mode != PdfMode.IMAGES:
            return [asset]

        page_count = self._count_pages(asset)
        if page_count == 0:
            return []

        output_dir = self._pdf_output_dir(asset)
        output_dir.mkdir(parents=True, exist_ok=True)
        generated: list[Asset] = []
        for idx in range(page_count):
            image_path = output_dir / f"{asset.path.stem}-p{idx:03d}.png"
            self._render_placeholder(image_path, page_number=idx + 1)
            generated.append(
                Asset(
                    path=image_path,
                    media="image",
                    page_index=idx,
                    source_kind=asset.source_kind,
                    mime="image/png",
                    meta={"source_pdf": str(asset.path)},
                )
            )
        return generated

    def _pdf_output_dir(self, asset: Asset) -> Path:
        if self._job is None:
            root = self._fallback_image_root
        else:
            root = self._job_root()
        safe = slugify(asset.path.stem or "document")
        return ensure_dir(root / "page-images" / safe)

    @staticmethod
    def _render_placeholder(path: Path, *, page_number: int) -> None:
        image = Image.new("RGB", (1024, 1325), color="white")
        draw = ImageDraw.Draw(image)
        text = f"Page {page_number}"
        draw.text((40, 40), text, fill="black")
        image.save(path, format="PNG")

    @staticmethod
    def _count_pages(asset: Asset) -> int:
        try:
            from PyPDF2 import PdfReader
        except ImportError:  # pragma: no cover - dependency should exist but keep fallback
            return 1

        try:
            reader = PdfReader(str(asset.path))
        except Exception:  # noqa: BLE001
            return 1

        pages = getattr(reader, "pages", [])
        try:
            return len(pages)
        except TypeError:  # pragma: no cover - defensive
            return 0

    # ------------------------------------------------------------------
    # Video handling
    # ------------------------------------------------------------------
    def _normalize_video(self, asset: Asset) -> list[Asset]:
        if asset.source_kind == SourceKind.YOUTUBE and (asset.meta or {}).get("pass_through"):
            return [asset]

        normalized_dir = self._video_dir(asset)
        chunk_dir = normalized_dir / "chunks"
        ensure_dir(chunk_dir)

        normalization = self._video.normalizer(asset.path, output_dir=normalized_dir, encoder_chain=self._encoder_chain)
        normalized_path = Path(getattr(normalization, "path"))
        metadata = self._video.probe(normalized_path)

        manifest_path = self._manifest_path(asset)
        plan = self._video.planner(
            metadata,
            normalized_path=normalized_path,
            max_seconds=DEFAULT_MAX_CHUNK_SECONDS,
            max_bytes=DEFAULT_MAX_CHUNK_BYTES,
            tokens_per_second=DEFAULT_TOKENS_PER_SECOND,
            chunk_dir=chunk_dir,
            manifest_path=manifest_path,
        )

        manifest_path = self._write_manifest(plan, asset)
        self._last_manifest_path = manifest_path

        chunk_total = len(plan.chunks)
        chunk_assets: list[Asset] = []
        for chunk in plan.chunks:
            meta = {
                "chunk_index": chunk.index,
                "chunk_total": chunk_total,
                "chunk_start_seconds": chunk.start_seconds,
                "chunk_end_seconds": chunk.end_seconds,
                "chunk_start_iso": chunk.start_iso,
                "chunk_end_iso": chunk.end_iso,
                "manifest_path": str(manifest_path),
                "normalized_path": str(plan.normalized_path),
                "source_video": str(asset.path),
            }
            chunk_asset = Asset(
                path=chunk.path,
                media="video",
                source_kind=asset.source_kind,
                mime="video/mp4",
                meta=meta,
            )
            chunk_assets.append(chunk_asset)
            self._last_chunk_info.append(
                {
                    "index": chunk.index,
                    "start_seconds": chunk.start_seconds,
                    "end_seconds": chunk.end_seconds,
                    "path": str(chunk.path),
                    "manifest_path": str(manifest_path),
                }
            )
        return chunk_assets

    def _video_dir(self, asset: Asset) -> Path:
        root = self._job_root()
        safe = slugify(asset.path.stem or "video")
        return ensure_dir(root / "pickles" / "video-chunks" / safe)

    def _manifest_path(self, asset: Asset) -> Path:
        root = self._job_root()
        return root / "chunks.json"

    def _job_root(self) -> Path:
        if self._job is None:
            return self._fallback_video_root
        if self._job.output_dir is not None:
            base = Path(self._job.output_dir)
        else:
            if "://" in self._job.source:
                base = self._fallback_video_root
            else:
                base = Path(self._job.source).expanduser().parent
        slug_source = "remote" if "://" in self._job.source else Path(self._job.source).stem
        slug = slugify(slug_source or "job")
        return ensure_dir(base / slug)

    def _write_manifest(self, plan: VideoChunkPlan, asset: Asset) -> Path:
        manifest_path = plan.manifest_path or self._manifest_path(asset)
        ensure_dir(manifest_path.parent)
        created = datetime.now(timezone.utc).isoformat()
        chunks = [
            {
                "index": chunk.index,
                "start_seconds": chunk.start_seconds,
                "end_seconds": chunk.end_seconds,
                "start_iso": chunk.start_iso,
                "end_iso": chunk.end_iso,
                "path": str(chunk.path),
                "status": "pending",
                "response_path": None,
            }
            for chunk in plan.chunks
        ]

        payload = {
            "version": 1,
            "source": str(plan.metadata.path),
            "source_hash": f"sha256:{self._safe_hash(asset.path)}",
            "source_kind": asset.source_kind.value,
            "normalized": str(plan.normalized_path),
            "normalized_hash": f"sha256:{self._safe_hash(plan.normalized_path)}",
            "duration_seconds": plan.metadata.duration_seconds,
            "size_bytes": plan.metadata.size_bytes,
            "fps": plan.metadata.fps,
            "model": self._job.model if self._job else None,
            "token_limit": None,
            "tokens_per_second": DEFAULT_TOKENS_PER_SECOND,
            "video_metadata_defaults": {"fps": 1},
            "chunks": chunks,
            "created_utc": created,
        }
        manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        return manifest_path

    def _safe_hash(self, path: Path) -> str:
        try:
            return self._video.hash_func(Path(path))
        except FileNotFoundError:
            return ""

    def artifact_paths(self) -> list[Path]:  # pragma: no cover - simple accessor
        paths: list[Path] = []
        if self._last_manifest_path is not None:
            paths.append(self._last_manifest_path)
        return paths
