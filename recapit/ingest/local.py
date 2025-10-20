from __future__ import annotations

from pathlib import Path

from ..core.types import Asset, Job, SourceKind


_MEDIA_BY_SUFFIX = {
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
    ".bmp": "image",
    ".gif": "image",
    ".mp4": "video",
    ".mov": "video",
    ".mkv": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
}


class LocalIngestor:
    """Discover local files that can be processed by the engine."""

    def discover(self, job: Job) -> list[Asset]:  # pragma: no cover - covered indirectly
        root = Path(job.source).expanduser()
        if not root.exists():
            return []

        if root.is_file():
            asset = self._asset_from_path(root)
            return [asset] if asset else []

        assets: list[Asset] = []
        if job.recursive:
            iterator = root.rglob("*")
        else:
            iterator = root.glob("*")
        for path in iterator:
            if path.is_file():
                asset = self._asset_from_path(path)
                if asset:
                    assets.append(asset)
        return assets

    def _asset_from_path(self, path: Path) -> Asset | None:
        media = _MEDIA_BY_SUFFIX.get(path.suffix.lower())
        if media is None:
            return None
        return Asset(path=path, media=media, source_kind=SourceKind.LOCAL)
