from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Protocol

from ..core.types import Asset, Job, SourceKind


class DriveDownloader(Protocol):
    def download(self, file_id: str, destination: Path) -> Path: ...


class DriveIngestor:
    """Download files referenced by drive:// identifiers."""

    def __init__(self, *, cache_dir: Path | None = None, downloader: DriveDownloader | None = None) -> None:
        self._cache_dir = Path(cache_dir or Path(tempfile.gettempdir()) / "lecture-drive-cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._downloader = downloader

    def discover(self, job: Job) -> list[Asset]:
        source = job.source
        if source.startswith("drive://"):
            file_id = source.split("drive://", 1)[1]
        elif source.startswith("gdrive://"):
            file_id = source.split("gdrive://", 1)[1]
        else:
            return []
        file_id = file_id.strip()
        if not file_id:
            raise ValueError("Drive URI missing file identifier")

        destination = self._cache_dir / file_id
        if destination.exists():
            return [self._to_asset(destination)]

        downloader = self._resolve_downloader()
        downloaded = downloader.download(file_id, destination)
        return [self._to_asset(downloaded)]

    def _resolve_downloader(self) -> DriveDownloader:
        if self._downloader is not None:
            return self._downloader
        try:
            from googleapiclient.discovery import build  # type: ignore
            from google.oauth2 import service_account  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("google-api-python-client is required for Drive ingestion") from exc

        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not credentials_path:
            raise RuntimeError("Set GOOGLE_APPLICATION_CREDENTIALS to use Drive ingestion")

        scopes = ["https://www.googleapis.com/auth/drive.readonly"]
        creds = service_account.Credentials.from_service_account_file(credentials_path, scopes=scopes)
        service = build("drive", "v3", credentials=creds)

        class _DefaultDownloader:
            def __init__(self, svc, cache: Path):
                self._service = svc
                self._cache = cache

            def download(self, file_id: str, destination: Path) -> Path:
                request = self._service.files().get_media(fileId=file_id)
                from googleapiclient.http import MediaIoBaseDownload  # type: ignore
                import io

                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                fh.seek(0)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("wb") as fp:
                    fp.write(fh.read())
                return destination

        return _DefaultDownloader(service, self._cache_dir)

    @staticmethod
    def _to_asset(path: Path) -> Asset:
        mime = "application/octet-stream"
        if path.suffix.lower() == ".pdf":
            media = "pdf"
            mime = "application/pdf"
        elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif"}:
            media = "image"
            mime = f"image/{path.suffix.lower().lstrip('.')}"
        elif path.suffix.lower() in {".mp4", ".mov"}:
            media = "video"
            mime = "video/mp4"
        elif path.suffix.lower() in {".mp3", ".wav", ".m4a"}:
            media = "audio"
            mime = "audio/mpeg"
        else:
            media = "pdf"
        return Asset(path=path, media=media, source_kind=SourceKind.DRIVE, mime=mime)
