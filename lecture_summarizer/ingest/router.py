from __future__ import annotations

from urllib.parse import urlparse

from ..core.types import Job
from .local import LocalIngestor
from .url import URLIngestor


class CompositeIngestor:
    """Route discovery requests to local or remote ingestors."""

    def __init__(self, *, local: LocalIngestor | None = None, url: URLIngestor | None = None) -> None:
        self._local = local or LocalIngestor()
        self._url = url or URLIngestor()

    def discover(self, job: Job):  # pragma: no cover - behaviour tested indirectly
        parsed = urlparse(job.source)
        if parsed.scheme in {"http", "https"}:
            return self._url.discover(job)
        return self._local.discover(job)
