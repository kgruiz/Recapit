from __future__ import annotations

import hashlib
import mimetypes
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from ..core.types import Asset, Job, SourceKind


_DEFAULT_MEDIA_BY_MIME = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/tiff": "image",
    "video/mp4": "video",
    "audio/mpeg": "audio",
}


class URLIngestor:
    """Fetch remote assets over HTTP(S) and store them locally."""

    def __init__(self, *, cache_dir: Path | None = None, timeout: float = 10.0) -> None:
        self._cache_dir = Path(cache_dir or Path(tempfile.gettempdir()) / "lecture-url-cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._timeout = timeout

    def discover(self, job: Job) -> list[Asset]:  # pragma: no cover - covered via tests
        parsed = urlparse(job.source)
        if parsed.scheme not in {"http", "https"}:
            return []

        with urlopen(job.source, timeout=self._timeout) as response:  # noqa: S310 - controlled URL
            raw = response.read()
            mime = response.headers.get_content_type()

        media = self._infer_media(job.source, mime)
        if media is None:
            return []

        target = self._materialize(job.source, raw, mime)
        return [
            Asset(
                path=target,
                media=media,
                source_kind=SourceKind.URL,
                mime=mime,
                meta={"url": job.source, "size_bytes": len(raw)},
            )
        ]

    def _materialize(self, url: str, raw: bytes, mime: str | None) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        suffix = self._guess_suffix(url, mime)
        target = self._cache_dir / f"{digest}{suffix}"
        target.write_bytes(raw)
        return target

    @staticmethod
    def _guess_suffix(url: str, mime: str | None) -> str:
        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix
        if suffix:
            return suffix
        if mime:
            guess = mimetypes.guess_extension(mime)
            if guess:
                return guess
        return ""

    @staticmethod
    def _infer_media(url: str, mime: str | None) -> str | None:
        if mime and mime in _DEFAULT_MEDIA_BY_MIME:
            return _DEFAULT_MEDIA_BY_MIME[mime]
        parsed = urlparse(url)
        return _extension_to_media(Path(parsed.path).suffix)


def _extension_to_media(ext: str) -> str | None:
    lookup = {
        ".pdf": "pdf",
        ".png": "image",
        ".jpg": "image",
        ".jpeg": "image",
        ".gif": "image",
        ".tif": "image",
        ".tiff": "image",
        ".mp4": "video",
        ".mov": "video",
        ".mp3": "audio",
        ".wav": "audio",
        ".m4a": "audio",
    }
    return lookup.get(ext.lower())
