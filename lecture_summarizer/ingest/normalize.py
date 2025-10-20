from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PIL import Image, ImageDraw

from ..core.types import Asset, Job, PdfMode, SourceKind
from ..pdf import pdf_to_png
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
from .youtube import YouTubeDownloader, YouTubeDownloadError


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
        capability_checker: Callable[[str], bool] | None = None,
        pdf_renderer: Callable[[Path, Path, str | None], Sequence[Path]] | None = None,
        youtube_downloader: YouTubeDownloader | None = None,
        max_chunk_seconds: float | None = None,
        max_chunk_bytes: int | None = None,
        token_limit: int | None = None,
        tokens_per_second: float | None = None,
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
        self._supports = capability_checker or (lambda _cap: True)
        self._pdf_renderer = pdf_renderer or pdf_to_png
        self._youtube_downloader = youtube_downloader
        self._resolved_pdf_mode: PdfMode | None = None
        self._max_chunk_seconds = float(max_chunk_seconds) if max_chunk_seconds else DEFAULT_MAX_CHUNK_SECONDS
        self._max_chunk_bytes = int(max_chunk_bytes) if max_chunk_bytes else DEFAULT_MAX_CHUNK_BYTES
        self._token_limit = int(token_limit) if token_limit else None
        self._tokens_per_second = float(tokens_per_second) if tokens_per_second else DEFAULT_TOKENS_PER_SECOND

    # Optional hook used by Engine/Planner
    def prepare(self, job: Job) -> None:  # pragma: no cover - simple setter
        self._job = job

    def normalize(self, assets: list[Asset], pdf_mode: PdfMode) -> list[Asset]:
        self._last_chunk_info = []
        self._last_manifest_path = None
        resolved_pdf_mode = self._resolve_pdf_mode(pdf_mode)
        self._resolved_pdf_mode = resolved_pdf_mode
        normalized: list[Asset] = []
        for asset in assets:
            if asset.media == "pdf":
                normalized.extend(self._normalize_pdf(asset, resolved_pdf_mode))
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
        if pdf_mode == PdfMode.PDF:
            return [asset]

        if pdf_mode != PdfMode.IMAGES:
            return [asset]

        output_dir = self._pdf_output_dir(asset)
        generated: list[Asset] = []
        try:
            image_paths = list(self._pdf_renderer(asset.path, output_dir, prefix=asset.path.stem))
        except Exception:  # noqa: BLE001
            image_paths = []
        if not image_paths:
            image_paths = self._render_placeholders(asset, output_dir)

        for idx, image_path in enumerate(image_paths):
            generated.append(
                Asset(
                    path=image_path,
                    media="image",
                    page_index=idx,
                    source_kind=asset.source_kind,
                    mime="image/png",
                    meta={
                        "source_pdf": str(asset.path),
                        "page_index": idx,
                        "page_total": len(image_paths),
                    },
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

    def _render_placeholders(self, asset: Asset, output_dir: Path) -> list[Path]:
        page_count = max(self._count_pages(asset), 1)
        ensure_dir(output_dir)
        paths: list[Path] = []
        for idx in range(page_count):
            image_path = output_dir / f"{asset.path.stem}-p{idx:03d}.png"
            self._render_placeholder(image_path, page_number=idx + 1)
            paths.append(image_path)
        return paths

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

        realized_asset = self._materialize_video(asset)
        normalized_dir = self._video_dir(realized_asset)
        chunk_dir = normalized_dir / "chunks"
        ensure_dir(chunk_dir)

        normalization = self._video.normalizer(
            realized_asset.path, output_dir=normalized_dir, encoder_chain=self._encoder_chain
        )
        normalized_path = Path(getattr(normalization, "path"))
        metadata = self._video.probe(normalized_path)

        manifest_path = self._manifest_path(realized_asset)
        plan = self._video.planner(
            metadata,
            normalized_path=normalized_path,
            max_seconds=self._max_chunk_seconds,
            max_bytes=self._max_chunk_bytes,
            token_limit=self._token_limit,
            tokens_per_second=self._tokens_per_second,
            chunk_dir=chunk_dir,
            manifest_path=manifest_path,
        )

        manifest_path, manifest_payload = self._write_manifest(plan, asset)
        self._last_manifest_path = manifest_path
        manifest_chunks = {
            int(entry.get("index")): entry
            for entry in manifest_payload.get("chunks", [])
            if isinstance(entry, dict) and "index" in entry
        }

        chunk_total = len(plan.chunks)
        chunk_assets: list[Asset] = []
        for chunk in plan.chunks:
            manifest_entry = manifest_chunks.get(chunk.index, {})
            meta = {
                "chunk_index": chunk.index,
                "chunk_total": chunk_total,
                "chunk_start_seconds": chunk.start_seconds,
                "chunk_end_seconds": chunk.end_seconds,
                "chunk_start_iso": chunk.start_iso,
                "chunk_end_iso": chunk.end_iso,
                "manifest_path": str(manifest_path),
                "normalized_path": str(plan.normalized_path),
                "source_video": str(realized_asset.path),
                "source_url": (realized_asset.meta or {}).get("source_url"),
                "response_path": manifest_entry.get("response_path"),
                "status": manifest_entry.get("status"),
                "file_uri": manifest_entry.get("file_uri"),
            }
            if realized_asset.meta:
                for key in ("youtube_id", "duration_seconds", "size_bytes", "downloaded"):
                    if realized_asset.meta.get(key) is not None:
                        meta[key] = realized_asset.meta[key]
            chunk_asset = Asset(
                path=chunk.path,
                media="video",
                source_kind=realized_asset.source_kind,
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
                    "response_path": manifest_entry.get("response_path"),
                }
            )
        return chunk_assets

    def _video_dir(self, asset: Asset) -> Path:
        root = self._job_root()
        safe = slugify(asset.path.stem or "video")
        return ensure_dir(root / "pickles" / "video-chunks" / safe)

    def _manifest_path(self, asset: Asset) -> Path:
        root = self._job_root()
        return ensure_dir(root / "manifests") / f"{slugify(asset.path.stem or 'video')}.json"

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
        created_time = datetime.now(timezone.utc)
        existing_payload: dict | None = None
        existing_chunks: dict[int, dict] = {}
        if manifest_path.exists():
            try:
                existing_payload = json.loads(manifest_path.read_text())
                existing_chunks = {
                    int(entry.get("index")): dict(entry)
                    for entry in existing_payload.get("chunks", [])
                    if isinstance(entry, dict) and "index" in entry
                }
            except (OSError, json.JSONDecodeError):  # pragma: no cover - defensive
                existing_payload = None

        chunks = [
            {
                "index": chunk.index,
                "start_seconds": chunk.start_seconds,
                "end_seconds": chunk.end_seconds,
                "start_iso": chunk.start_iso,
                "end_iso": chunk.end_iso,
                "path": str(chunk.path),
                "status": existing_chunks.get(chunk.index, {}).get("status", "pending"),
                "response_path": existing_chunks.get(chunk.index, {}).get("response_path"),
                "file_uri": existing_chunks.get(chunk.index, {}).get("file_uri"),
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
            "created_utc": (existing_payload or {}).get("created_utc") or created_time.isoformat(),
            "updated_utc": datetime.now(timezone.utc).isoformat(),
        }
        manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        return manifest_path, payload

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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _resolve_pdf_mode(self, requested: PdfMode) -> PdfMode:
        if requested == PdfMode.AUTO:
            if self._supports("pdf"):
                return PdfMode.PDF
            if self._supports("image"):
                return PdfMode.IMAGES
            raise RuntimeError("Provider does not support PDF or image ingestion")
        return requested

    def _materialize_video(self, asset: Asset) -> Asset:
        if asset.source_kind != SourceKind.YOUTUBE:
            return asset

        meta = dict(asset.meta or {})
        if meta.get("pass_through"):
            return asset

        downloader = self._youtube_downloader or YouTubeDownloader()
        target_dir = self._job_root() / "downloads" / "youtube"
        ensure_dir(target_dir)

        try:
            result = downloader.download(asset.path.as_posix(), target_dir=target_dir)
        except YouTubeDownloadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise YouTubeDownloadError(str(exc)) from exc

        info = result.info or {}
        meta.update(
            {
                "downloaded": True,
                "source_url": meta.get("source_url", asset.path.as_posix()),
                "youtube_id": info.get("id"),
                "duration_seconds": info.get("duration"),
                "size_bytes": result.path.stat().st_size if result.path.exists() else None,
            }
        )
        return Asset(
            path=result.path,
            media="video",
            source_kind=asset.source_kind,
            mime="video/mp4",
            meta=meta,
        )
