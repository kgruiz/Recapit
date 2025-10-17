from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

from .constants import DEFAULT_VIDEO_TOKENS_PER_SECOND
from .utils import ensure_dir


DEFAULT_MAX_CHUNK_SECONDS = 7200.0  # 2 hours
DEFAULT_MAX_CHUNK_BYTES = 500 * 1024 * 1024  # 500 MB safety cap
DEFAULT_TOKENS_PER_SECOND = float(DEFAULT_VIDEO_TOKENS_PER_SECOND)

_ACCEPTABLE_VIDEO_CODECS = {"h264", "avc1"}
_ACCEPTABLE_AUDIO_CODECS = {"aac", "mp4a"}
_ACCEPTABLE_AUDIO_SAMPLE_RATES = {None, 44100, 48000}

_ENCODER_NAME_CACHE: set[str] | None = None


def _ffmpeg_encoder_names() -> set[str]:
    global _ENCODER_NAME_CACHE
    if _ENCODER_NAME_CACHE is not None:
        return _ENCODER_NAME_CACHE
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        _ENCODER_NAME_CACHE = set()
        return _ENCODER_NAME_CACHE
    except subprocess.CalledProcessError:
        _ENCODER_NAME_CACHE = set()
        return _ENCODER_NAME_CACHE

    names: set[str] = set()
    pattern = re.compile(r"^\s*[A-Z\.]{6}\s+(\S+)")
    for line in (result.stdout or "").splitlines():
        match = pattern.match(line)
        if match:
            names.add(match.group(1))
    _ENCODER_NAME_CACHE = names
    return _ENCODER_NAME_CACHE


def _auto_encoder_priority() -> tuple[VideoEncoderPreference, ...]:
    if sys.platform == "darwin":
        return (
            VideoEncoderPreference.VIDEOTOOLBOX,
            VideoEncoderPreference.NVENC,
            VideoEncoderPreference.QSV,
            VideoEncoderPreference.AMF,
        )
    if sys.platform.startswith("win"):
        return (
            VideoEncoderPreference.NVENC,
            VideoEncoderPreference.AMF,
            VideoEncoderPreference.QSV,
            VideoEncoderPreference.VIDEOTOOLBOX,
        )
    return (
        VideoEncoderPreference.NVENC,
        VideoEncoderPreference.QSV,
        VideoEncoderPreference.AMF,
        VideoEncoderPreference.VIDEOTOOLBOX,
    )


def select_encoder_chain(preference: VideoEncoderPreference) -> tuple[list[EncoderSpec], list[str]]:
    available = _ffmpeg_encoder_names()
    diagnostics: list[str] = []
    chain: list[EncoderSpec] = []
    cpu_spec = _ENCODER_SPECS[VideoEncoderPreference.CPU]

    def _supports(pref: VideoEncoderPreference) -> bool:
        spec = _ENCODER_SPECS.get(pref)
        if spec is None:
            return False
        return spec.codec in available

    if preference == VideoEncoderPreference.AUTO:
        for candidate in _auto_encoder_priority():
            spec = _ENCODER_SPECS.get(candidate)
            if spec is None:
                continue
            if _supports(candidate):
                chain.append(spec)
                diagnostics.append(
                    f"auto: detected FFmpeg encoder '{spec.codec}' for preference '{candidate.value}'"
                )
                break
            diagnostics.append(f"auto: FFmpeg encoder for '{candidate.value}' not available")
        if not chain:
            diagnostics.append("auto: no hardware encoder detected; falling back to libx264")
        if cpu_spec not in chain:
            chain.append(cpu_spec)
        return chain, diagnostics

    if preference == VideoEncoderPreference.CPU:
        diagnostics.append("cpu: using libx264 software encoder")
        return [cpu_spec], diagnostics

    spec = _ENCODER_SPECS.get(preference)
    if spec is None:
        diagnostics.append(
            f"{preference.value}: unsupported encoder preference; defaulting to libx264"
        )
        return [cpu_spec], diagnostics

    if _supports(preference):
        chain.append(spec)
        diagnostics.append(f"{preference.value}: FFmpeg encoder '{spec.codec}' available")
    else:
        diagnostics.append(
            f"{preference.value}: FFmpeg encoder '{spec.codec}' not available; using libx264"
        )
    if cpu_spec not in chain:
        chain.append(cpu_spec)
    return chain, diagnostics


class VideoEncoderPreference(str, Enum):
    """User-facing preference for which FFmpeg encoder to try."""

    AUTO = "auto"
    CPU = "cpu"
    NVENC = "nvenc"
    VIDEOTOOLBOX = "videotoolbox"
    QSV = "qsv"
    VAAPI = "vaapi"
    AMF = "amf"

    @classmethod
    def parse(cls, value: str | "VideoEncoderPreference" | None) -> "VideoEncoderPreference":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.AUTO
        normalized = value.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(
            f"Unknown video encoder preference '{value}'. Expected one of: "
            + ", ".join(member.value for member in cls)
        )


@dataclass(frozen=True)
class EncoderSpec:
    preference: VideoEncoderPreference
    codec: str
    args: tuple[str, ...]
    accelerated: bool

    @property
    def label(self) -> str:
        if self.accelerated:
            return f"{self.preference.value}:{self.codec}"
        return self.codec


@dataclass(frozen=True)
class NormalizationResult:
    path: Path
    encoder: EncoderSpec
    reused_existing: bool
    diagnostics: tuple[str, ...] = ()
    encoder_known: bool = True


_ENCODER_SPECS: dict[VideoEncoderPreference, EncoderSpec] = {
    VideoEncoderPreference.CPU: EncoderSpec(
        preference=VideoEncoderPreference.CPU,
        codec="libx264",
        args=(
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-profile:v",
            "high",
            "-bf",
            "2",
        ),
        accelerated=False,
    ),
    VideoEncoderPreference.NVENC: EncoderSpec(
        preference=VideoEncoderPreference.NVENC,
        codec="h264_nvenc",
        args=(
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-rc:v",
            "vbr_hq",
            "-cq",
            "19",
            "-b:v",
            "6M",
            "-maxrate",
            "12M",
            "-bufsize",
            "24M",
            "-g",
            "240",
            "-bf",
            "3",
            "-profile:v",
            "high",
        ),
        accelerated=True,
    ),
    VideoEncoderPreference.VIDEOTOOLBOX: EncoderSpec(
        preference=VideoEncoderPreference.VIDEOTOOLBOX,
        codec="h264_videotoolbox",
        args=(
            "-c:v",
            "h264_videotoolbox",
            "-allow_sw",
            "1",
            "-b:v",
            "6M",
            "-maxrate",
            "12M",
            "-bufsize",
            "24M",
            "-g",
            "240",
            "-profile:v",
            "high",
        ),
        accelerated=True,
    ),
    VideoEncoderPreference.QSV: EncoderSpec(
        preference=VideoEncoderPreference.QSV,
        codec="h264_qsv",
        args=(
            "-c:v",
            "h264_qsv",
            "-preset",
            "medium",
            "-profile:v",
            "high",
            "-global_quality",
            "23",
            "-look_ahead",
            "1",
            "-g",
            "240",
        ),
        accelerated=True,
    ),
    VideoEncoderPreference.AMF: EncoderSpec(
        preference=VideoEncoderPreference.AMF,
        codec="h264_amf",
        args=(
            "-c:v",
            "h264_amf",
            "-quality",
            "quality",
            "-usage",
            "transcoding",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-rc",
            "cqp",
            "-qp_i",
            "20",
            "-qp_p",
            "23",
            "-qp_b",
            "25",
        ),
        accelerated=True,
    ),
}

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
    audio_sample_rate: int | None


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
    audio_sample_rate: int | None = None

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
            audio_sample_rate = _safe_int(stream.get("sample_rate"))
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
        audio_sample_rate=audio_sample_rate,
    )


def normalize_video(
    path: Path,
    *,
    output_dir: Path,
    encoder_chain: Sequence[EncoderSpec] | None = None,
) -> NormalizationResult:
    """Normalize to MP4 using the fastest available encoder preference."""
    path = Path(path)
    output_dir = ensure_dir(Path(output_dir))
    normalized = output_dir / f"{path.stem}-normalized.mp4"
    source_mtime = path.stat().st_mtime
    chain = list(encoder_chain or [_ENCODER_SPECS[VideoEncoderPreference.CPU]])
    diagnostics: list[str] = []
    if normalized.exists() and normalized.stat().st_mtime >= source_mtime:
        try:
            probe_video(normalized)
            diagnostics.append("Reusing existing normalized file; skipping re-encode")
            return NormalizationResult(
                path=normalized,
                encoder=chain[0],
                reused_existing=True,
                diagnostics=tuple(diagnostics),
                encoder_known=False,
            )
        except RuntimeError:
            # Previous normalization left a corrupt artifact; re-create it.
            try:
                normalized.unlink()
            except FileNotFoundError:
                pass

    last_error: subprocess.CalledProcessError | None = None
    for spec in chain:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            *spec.args,
            "-pix_fmt",
            "yuv420p",
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
            diagnostics.append(f"Encoded with {spec.codec} ({spec.preference.value})")
            return NormalizationResult(
                path=normalized,
                encoder=spec,
                reused_existing=False,
                diagnostics=tuple(diagnostics),
            )
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg executable not found; install ffmpeg to process videos.") from exc
        except subprocess.CalledProcessError as exc:
            last_error = exc
            message = (exc.stderr or exc.stdout or str(exc)).strip()
            snippet = message.splitlines()[-1] if message else "ffmpeg reported an error"
            diagnostics.append(f"{spec.preference.value}: ffmpeg failed ({spec.codec}): {snippet}")
            try:
                normalized.unlink()
            except FileNotFoundError:
                pass

    error_reason = diagnostics[-1] if diagnostics else "Unknown ffmpeg failure"
    if last_error is not None:
        raise RuntimeError(f"ffmpeg failed while normalizing {path}: {error_reason}") from last_error
    raise RuntimeError(f"ffmpeg failed while normalizing {path}: {error_reason}")


def sha256sum(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Compute the SHA-256 hash of *path* using streaming reads."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fp:
        while True:
            chunk = fp.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _moov_before_mdat(path: Path) -> bool:
    """Return True if the MP4 structure stores the moov atom before mdat."""
    try:
        with path.open("rb") as fp:
            offset = 0
            moov_offset: int | None = None
            mdat_offset: int | None = None
            while True:
                header = fp.read(8)
                if len(header) < 8:
                    break
                box_size = int.from_bytes(header[:4], byteorder="big")
                box_type = header[4:8].decode("latin-1", errors="ignore")
                header_size = 8
                if box_size == 1:
                    extended = fp.read(8)
                    if len(extended) < 8:
                        break
                    box_size = int.from_bytes(extended, byteorder="big")
                    header_size = 16
                if box_size < header_size:
                    break
                if box_type == "moov":
                    moov_offset = offset
                elif box_type == "mdat":
                    mdat_offset = offset
                skip = box_size - header_size
                fp.seek(skip, os.SEEK_CUR)
                offset += box_size
                if moov_offset is not None and mdat_offset is not None:
                    break
    except FileNotFoundError:
        return False
    except OSError:
        return False
    if moov_offset is None or mdat_offset is None:
        return False
    return moov_offset < mdat_offset


def assess_video_normalization(path: Path) -> tuple[bool, dict[str, bool], VideoMetadata | None]:
    """Check whether a source video already satisfies the *minimal* constraints required to skip normalization.

    Notes
    -----
    This only verifies container readability, baseline codec/sample-rate expectations, and that the MP4 places the
    `moov` atom before `mdat`. It does **not** guarantee secondary properties that the full normalize step enforces
    (e.g., keyframe cadence, bitrate ceilings, color metadata). Callers should still handle the fallback path when
    these quick checks fail or whenever downstream processing needs the stricter guarantees from re-encoding.
    """
    checks: dict[str, bool] = {}
    try:
        meta = probe_video(path)
    except Exception:
        checks["probe"] = False
        return False, checks, None

    checks["probe"] = True
    video_codec = (meta.video_codec or "").lower()
    checks["video_codec"] = video_codec in _ACCEPTABLE_VIDEO_CODECS
    audio_codec = (meta.audio_codec or "").lower()
    checks["audio_codec"] = (audio_codec in _ACCEPTABLE_AUDIO_CODECS) if audio_codec else True
    checks["audio_rate"] = meta.audio_sample_rate in _ACCEPTABLE_AUDIO_SAMPLE_RATES
    checks["faststart"] = _moov_before_mdat(path)

    acceptable = all(checks.values())
    return acceptable, checks, meta


def plan_video_chunks(
    metadata: VideoMetadata,
    *,
    normalized_path: Path,
    max_seconds: float = DEFAULT_MAX_CHUNK_SECONDS,
    max_bytes: int = DEFAULT_MAX_CHUNK_BYTES,
    token_limit: int | None = None,
    tokens_per_second: float = DEFAULT_TOKENS_PER_SECOND,
    chunk_dir: Path | None = None,
    manifest_path: Path | None = None,
) -> VideoChunkPlan:
    """Compute chunk boundaries and optionally prepare file paths."""
    chunk_dir_path = Path(chunk_dir) if chunk_dir else normalized_path.parent
    boundaries = _compute_chunk_boundaries(
        metadata,
        max_seconds=max_seconds,
        max_bytes=max_bytes,
        token_limit=token_limit,
        tokens_per_second=tokens_per_second,
    )

    if len(boundaries) == 1:
        start, end = boundaries[0]
        chunk = VideoChunk(index=0, start_seconds=start, end_seconds=end, path=normalized_path, source=metadata.path)
        return VideoChunkPlan(
            metadata=metadata,
            normalized_path=normalized_path,
            chunks=[chunk],
            manifest_path=manifest_path,
        )

    chunk_dir_path = ensure_dir(chunk_dir_path)
    chunks: list[VideoChunk] = []
    for idx, (start, end) in enumerate(boundaries):
        chunk_name = f"{normalized_path.stem}-chunk{idx:02d}.mp4"
        chunk_path = chunk_dir_path / chunk_name
        _extract_segment(normalized_path, chunk_path, start, end)
        chunks.append(VideoChunk(index=idx, start_seconds=start, end_seconds=end, path=chunk_path, source=metadata.path))

    return VideoChunkPlan(metadata=metadata, normalized_path=normalized_path, chunks=chunks, manifest_path=manifest_path)


def _compute_chunk_boundaries(
    metadata: VideoMetadata,
    *,
    max_seconds: float,
    max_bytes: int,
    token_limit: int | None,
    tokens_per_second: float,
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
    if token_limit and tokens_per_second > 0:
        max_seconds_by_tokens = token_limit / tokens_per_second
        if math.isfinite(max_seconds_by_tokens):
            effective_max = min(effective_max, max_seconds_by_tokens)
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
