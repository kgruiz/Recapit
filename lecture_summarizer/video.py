from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .utils import ensure_dir


DEFAULT_MAX_CHUNK_SECONDS = 7200.0  # 2 hours
DEFAULT_MAX_CHUNK_BYTES = 500 * 1024 * 1024  # 500 MB safety cap


@dataclass(frozen=True)
class VideoMetadata:
    path: Path
    duration_seconds: float
    size_bytes: int
    fps: float | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None


@dataclass(frozen=True)
class VideoChunk:
    index: int
    start_seconds: float
    end_seconds: float
    path: Path
    source: Path

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)

    @property
    def start_iso(self) -> str:
        return seconds_to_iso8601(self.start_seconds)

    @property
    def end_iso(self) -> str:
        return seconds_to_iso8601(self.end_seconds)


@dataclass(frozen=True)
class VideoChunkPlan:
    metadata: VideoMetadata
    normalized_path: Path
    chunks: list[VideoChunk]
    manifest_path: Path | None = None

    def requires_splitting(self) -> bool:
        return len(self.chunks) > 1


def probe_video(path: Path) -> VideoMetadata:
    """Return basic metadata for a video using ffprobe."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffprobe executable not found; install ffmpeg to process videos.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffprobe failed for {path}: {exc.stderr}") from exc

    data = json.loads(result.stdout or "{}")
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    duration = _safe_float(fmt.get("duration"))
    size_bytes = _safe_int(fmt.get("size"), default=path.stat().st_size)

    fps = None
    width = None
    height = None
    video_codec = None
    audio_codec = None

    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video":
            video_codec = stream.get("codec_name")
            width = stream.get("width")
            height = stream.get("height")
            rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
            fps = _parse_rate(rate)
            if duration is None:
                duration = _safe_float(stream.get("duration"))
        elif codec_type == "audio":
            audio_codec = stream.get("codec_name")
            if duration is None:
                duration = _safe_float(stream.get("duration"))

    if duration is None:
        duration = _safe_float(fmt.get("duration")) or 0.0

    return VideoMetadata(
        path=path,
        duration_seconds=max(duration or 0.0, 0.0),
        size_bytes=size_bytes,
        fps=fps,
        width=_safe_int(width),
        height=_safe_int(height),
        video_codec=video_codec,
        audio_codec=audio_codec,
    )


def normalize_video(path: Path, *, output_dir: Path) -> Path:
    """Normalize to H.264/AAC MP4 for consistent Gemini ingestion."""
    path = Path(path)
    output_dir = ensure_dir(Path(output_dir))
    normalized = output_dir / f"{path.stem}-normalized.mp4"
    source_mtime = path.stat().st_mtime
    if normalized.exists() and normalized.stat().st_mtime >= source_mtime:
        return normalized

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(path),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(normalized),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg executable not found; install ffmpeg to process videos.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg failed while normalizing {path}: {exc.stderr}") from exc
    return normalized


def plan_video_chunks(
    metadata: VideoMetadata,
    *,
    normalized_path: Path,
    max_seconds: float = DEFAULT_MAX_CHUNK_SECONDS,
    max_bytes: int = DEFAULT_MAX_CHUNK_BYTES,
    chunk_dir: Path | None = None,
    manifest_path: Path | None = None,
) -> VideoChunkPlan:
    """Compute chunk boundaries and optionally prepare file paths."""
    chunk_dir = ensure_dir(Path(chunk_dir or normalized_path.parent))
    boundaries = _compute_chunk_boundaries(metadata, max_seconds=max_seconds, max_bytes=max_bytes)

    if len(boundaries) == 1:
        start, end = boundaries[0]
        chunk = VideoChunk(index=0, start_seconds=start, end_seconds=end, path=normalized_path, source=metadata.path)
        return VideoChunkPlan(
            metadata=metadata,
            normalized_path=normalized_path,
            chunks=[chunk],
            manifest_path=manifest_path,
        )

    chunks: list[VideoChunk] = []
    for idx, (start, end) in enumerate(boundaries):
        chunk_name = f"{normalized_path.stem}-chunk{idx:02d}.mp4"
        chunk_path = chunk_dir / chunk_name
        _extract_segment(normalized_path, chunk_path, start, end)
        chunks.append(VideoChunk(index=idx, start_seconds=start, end_seconds=end, path=chunk_path, source=metadata.path))

    return VideoChunkPlan(metadata=metadata, normalized_path=normalized_path, chunks=chunks, manifest_path=manifest_path)


def _compute_chunk_boundaries(
    metadata: VideoMetadata,
    *,
    max_seconds: float,
    max_bytes: int,
) -> list[tuple[float, float]]:
    duration = max(metadata.duration_seconds, 0.0)
    if duration == 0.0:
        return [(0.0, 0.0)]

    # Estimate bytes per second to enforce byte-based splitting.
    bytes_per_second = metadata.size_bytes / duration if duration > 0 else metadata.size_bytes
    max_seconds_by_size = float("inf")
    if max_bytes > 0 and bytes_per_second > 0:
        max_seconds_by_size = max_bytes / bytes_per_second

    effective_max = max_seconds
    if math.isfinite(max_seconds_by_size):
        effective_max = min(effective_max, max_seconds_by_size)
    effective_max = max(effective_max, 1.0)

    chunk_count = max(1, math.ceil(duration / effective_max))
    boundaries: list[tuple[float, float]] = []
    for idx in range(chunk_count):
        start = idx * effective_max
        end = min(duration, (idx + 1) * effective_max)
        boundaries.append((float(start), float(end)))
    if boundaries:
        boundaries[-1] = (boundaries[-1][0], float(duration))
    return boundaries


def _extract_segment(source: Path, dest: Path, start: float, end: float) -> None:
    if dest.exists() and dest.stat().st_mtime >= source.stat().st_mtime:
        return
    ensure_dir(dest.parent)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-c",
        "copy",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg executable not found; install ffmpeg to process videos.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg failed while extracting segment {start}-{end}s: {exc.stderr}") from exc


def seconds_to_iso8601(value: float) -> str:
    total_seconds = max(0, int(round(value)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"PT{hours}H{minutes}M{seconds}S"


def _parse_rate(rate: str | None) -> float | None:
    if not rate:
        return None
    if "/" in rate:
        num, denom = rate.split("/", 1)
        denom_val = _safe_float(denom)
        num_val = _safe_float(num)
        if denom_val and denom_val != 0:
            return (num_val or 0.0) / denom_val
        return None
    return _safe_float(rate)


def _safe_float(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
