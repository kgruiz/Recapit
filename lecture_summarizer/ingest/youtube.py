from __future__ import annotations

from pathlib import Path
from urllib.parse import ParseResult, urlparse

from ..core.types import Asset, Job, SourceKind


_YOUTUBE_HOSTS = {
    "youtu.be",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
}


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
                meta={"pass_through": True},
            )
        ]
