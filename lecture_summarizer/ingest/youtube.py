from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Any, Callable
from urllib.parse import ParseResult, urlparse

from ..core.types import Asset, Job, SourceKind

try:  # pragma: no cover - optional dependency exercised in integration tests
    import yt_dlp  # type: ignore
    from yt_dlp.utils import DownloadError  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - dependency checked at runtime
    yt_dlp = None
    DownloadError = Exception


_YOUTUBE_HOSTS = {
    "youtu.be",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
}


@dataclass(frozen=True)
class YouTubeDownload:
    path: Path
    info: dict[str, Any]


class YouTubeDownloadError(RuntimeError):
    """Raised when yt-dlp fails to download or prepare a YouTube asset."""


class YouTubeDownloader:
    """Download YouTube videos to a local cache using yt-dlp."""

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        options_factory: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._cache_dir = Path(cache_dir or Path(tempfile.gettempdir()) / "lecture-youtube-cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._options_factory = options_factory

    def download(self, url: str, *, target_dir: Path | None = None) -> YouTubeDownload:
        if yt_dlp is None:
            raise YouTubeDownloadError("yt-dlp is required for YouTube downloads but is not installed")

        target_base = Path(target_dir or self._cache_dir)
        target_base.mkdir(parents=True, exist_ok=True)

        def _default_options() -> dict[str, Any]:
            return {
                "quiet": True,
                "noprogress": True,
                "ignoreerrors": False,
                "noplaylist": True,
                "retries": 3,
                "outtmpl": str(target_base / "%(id)s.%(ext)s"),
                "merge_output_format": "mp4",
                "format": "bv*+ba/bestvideo+bestaudio/best",
                "continuedl": True,
                "paths": {"home": str(target_base)},
                "postprocessors": [
                    {
                        "key": "FFmpegVideoConvertor",
                        "preferedformat": "mp4",
                    }
                ],
                "writeinfojson": True,
            }

        opts = (_default_options() if self._options_factory is None else self._options_factory())
        opts.setdefault("outtmpl", str(target_base / "%(id)s.%(ext)s"))
        opts.setdefault("paths", {"home": str(target_base)})
        opts.setdefault("noplaylist", True)

        try:
            with yt_dlp.YoutubeDL(opts) as downloader:
                info = downloader.extract_info(url, download=False)
                expected_path = Path(downloader.prepare_filename(info)).with_suffix(".mp4")
                final_path = target_base / expected_path.name
                if final_path.exists():
                    return YouTubeDownload(path=final_path, info=info)

                result = downloader.extract_info(url, download=True)
                file_path = self._resolve_path(result, default=final_path)
        except DownloadError as exc:  # pragma: no cover - exercised in integration scenarios
            raise YouTubeDownloadError(f"Failed to download YouTube video: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise YouTubeDownloadError(f"Unexpected error downloading YouTube video: {exc}") from exc

        if not file_path.exists():
            raise YouTubeDownloadError(f"YouTube download did not produce an MP4 file at {file_path}")
        return YouTubeDownload(path=file_path, info=result if isinstance(result, dict) else info)

    @staticmethod
    def _resolve_path(info: dict[str, Any], default: Path) -> Path:
        if "_filename" in info:
            candidate = Path(info["_filename"]).with_suffix(".mp4")
            if candidate.exists():
                return candidate
        requested = info.get("requested_downloads")
        if requested:
            for entry in requested:
                filepath = entry.get("filepath")
                if filepath:
                    candidate = Path(filepath)
                    if candidate.exists():
                        return candidate
        if "filepath" in info:
            candidate = Path(info["filepath"])
            if candidate.exists():
                return candidate
        return default


class YouTubeIngestor:
    """Emit passthrough assets for YouTube URLs."""

    def supports(self, parsed: ParseResult) -> bool:
        if parsed.scheme.lower() in {"yt", "youtube"}:
            return True
        host = parsed.netloc.lower()
        return host in _YOUTUBE_HOSTS

    def discover(self, job: Job) -> list[Asset]:
        parsed = urlparse(job.source)
        if not self.supports(parsed):
            return []
        url = job.source if parsed.scheme else f"https://{job.source}"
        return [
            Asset(
                path=Path(url),
                media="video",
                source_kind=SourceKind.YOUTUBE,
                mime="video/*",
                meta={
                    "pass_through": False,
                    "source_url": url,
                },
            )
        ]
