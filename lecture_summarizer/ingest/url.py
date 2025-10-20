from __future__ import annotations

import mimetypes
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from ..core.types import Asset, Job, SourceKind
from ..utils import ensure_dir


_DEFAULT_MEDIA_BY_MIME = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/tiff": "image",
    "video/mp4": "video",
    "audio/mpeg": "audio",
}

_INLINE_THRESHOLD = 20 * 1024 * 1024  # 20 MB


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

        size_hint = self._content_length(job.source)
        inline_allowed = size_hint is not None and size_hint <= _INLINE_THRESHOLD
        if inline_allowed:
            raw, mime = self._fetch_bytes(job.source)
            size = len(raw)
            target = self._materialize(job.source, raw, mime)
            meta = {
                "url": job.source,
                "size_bytes": size,
                "inline_bytes": raw,
                "upload_cache_key": self._cache_key(job.source),
            }
        else:
            target, mime, size = self._stream_to_disk(job.source)
            meta = {
                "url": job.source,
                "size_bytes": size,
                "upload_cache_key": self._cache_key(job.source),
            }
        media = self._infer_media(job.source, mime)
        if media is None:
            return []

        return [
            Asset(
                path=target,
                media=media,
                source_kind=SourceKind.URL,
                mime=mime,
                meta=meta,
            )
        ]

    def _content_length(self, url: str) -> int | None:
        request = Request(url, method="HEAD")
        try:
            with urlopen(request, timeout=self._timeout) as response:  # noqa: S310 - trusted URL from user input
                length = response.headers.get("Content-Length")
        except HTTPError as exc:  # pragma: no cover - network dependent
            if exc.code in {400, 401, 403, 405, 500, 501}:
                return None
            return None
        except Exception:  # noqa: BLE001 - treat unknown as no hint
            return None
        try:
            return int(length) if length is not None else None
        except (TypeError, ValueError):
            return None

    def _fetch_bytes(self, url: str) -> tuple[bytes, str]:
        with urlopen(url, timeout=self._timeout) as response:  # noqa: S310 - controlled URL
            mime = response.headers.get_content_type()
            raw = response.read()
        return raw, mime

    def _stream_to_disk(self, url: str) -> tuple[Path, str, int | None]:
        request = Request(url, method="GET")
        with urlopen(request, timeout=self._timeout) as response:  # noqa: S310 - controlled URL
            mime = response.headers.get_content_type()
            suffix = self._guess_suffix(url, mime)
            target = self._cache_dir / f"{self._cache_key(url)}{suffix}"
            ensure_dir(target.parent)
            temp = target.with_suffix(target.suffix + ".part")
            size_total = 0
            with temp.open("wb") as fh:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    size_total += len(chunk)
            temp.replace(target)
        return target, mime, size_total or None

    def _materialize(self, url: str, raw: bytes, mime: str | None) -> Path:
        digest = self._cache_key(url)[:16]
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

    @staticmethod
    def _cache_key(url: str) -> str:
        import hashlib

        return hashlib.sha256(url.encode("utf-8")).hexdigest()


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
