from __future__ import annotations

from urllib.parse import urlparse

from ..core.types import Job
from .local import LocalIngestor
from .url import URLIngestor
from .youtube import YouTubeIngestor
from .drive import DriveIngestor


class CompositeIngestor:
    """Route discovery requests to local, URL, YouTube, or Drive ingestors."""

    def __init__(
        self,
        *,
        local: LocalIngestor | None = None,
        url: URLIngestor | None = None,
        youtube: YouTubeIngestor | None = None,
        drive: DriveIngestor | None = None,
    ) -> None:
        self._local = local or LocalIngestor()
        self._url = url or URLIngestor()
        self._youtube = youtube or YouTubeIngestor()
        self._drive = drive or DriveIngestor()

    def discover(self, job: Job):  # pragma: no cover - behaviour tested indirectly
        parsed = urlparse(job.source)
        scheme = parsed.scheme.lower()
        if scheme in {"http", "https"}:
            if self._youtube.supports(parsed):
                return self._youtube.discover(job)
            return self._url.discover(job)
        if scheme in {"yt", "youtube"}:
            return self._youtube.discover(job)
        if scheme in {"drive", "gdrive"}:
            return self._drive.discover(job)
        return self._local.discover(job)
