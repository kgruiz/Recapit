from __future__ import annotations

import contextlib
import json
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import sys
import types
from pathlib import Path

from PIL import Image

from lecture_summarizer.core.types import Asset, Job, PdfMode, SourceKind
from lecture_summarizer.ingest import (
    LocalIngestor,
    URLIngestor,
    CompositeNormalizer,
    YouTubeIngestor,
    DriveIngestor,
    CompositeIngestor,
)
from lecture_summarizer.video import VideoChunk, VideoChunkPlan, VideoMetadata, EncoderSpec, VideoEncoderPreference


def _make_pdf(path: Path, pages: int = 1) -> None:
    path.write_text("stub pdf content")
    module = sys.modules.setdefault("PyPDF2", types.SimpleNamespace())
    module.PdfReader = lambda *_args, **_kwargs: types.SimpleNamespace(pages=[object() for _ in range(pages)])


@contextlib.contextmanager
def _serve_directory(directory: Path):
    class _Handler(SimpleHTTPRequestHandler):  # pragma: no cover - simple server shim
        def log_message(self, format: str, *args) -> None:
            return

    directory = directory.resolve()
    handler = lambda *args, **kwargs: _Handler(*args, directory=str(directory), **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join()


def _job_for(source: str, pdf_mode: PdfMode = PdfMode.AUTO, *, recursive: bool = False) -> Job:
    return Job(
        source=source,
        recursive=recursive,
        kind=None,
        pdf_mode=pdf_mode,
        output_dir=None,
        model="test-model",
    )


def test_local_ingestor_discovers_recursive(tmp_path: Path) -> None:
    nested = tmp_path / "folder"
    nested.mkdir()
    pdf = tmp_path / "doc.pdf"
    image = nested / "frame.png"
    _make_pdf(pdf)
    Image.new("RGB", (10, 10), color="red").save(image)

    ingestor = LocalIngestor()
    job = _job_for(str(tmp_path), recursive=True)
    assets = ingestor.discover(job)
    assert any(a.path == pdf for a in assets)
    assert any(a.path == image for a in assets)


def test_url_ingestor_fetches_remote_pdf(tmp_path: Path) -> None:
    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()
    pdf = remote_dir / "doc.pdf"
    _make_pdf(pdf)
    with _serve_directory(remote_dir) as base_url:
        url = f"{base_url}/doc.pdf"
        ingestor = URLIngestor(cache_dir=tmp_path / "cache")
        job = _job_for(url)
        assets = ingestor.discover(job)

    assert len(assets) == 1
    asset = assets[0]
    assert asset.media == "pdf"
    assert asset.source_kind == SourceKind.URL
    assert asset.path.exists()
    assert asset.meta["size_bytes"] > 0


def test_composite_normalizer_rasterizes_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=2)
    asset = Asset(path=pdf, media="pdf")
    normalizer = CompositeNormalizer(image_root=tmp_path / "images")
    images = normalizer.normalize([asset], PdfMode.IMAGES)
    assert len(images) == 2
    for idx, img_asset in enumerate(images):
        assert img_asset.media == "image"
        assert img_asset.page_index == idx
        assert img_asset.path.exists()
        assert img_asset.mime == "image/png"


def test_composite_normalizer_passthrough_pdf_when_mode_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf)
    asset = Asset(path=pdf, media="pdf")
    normalizer = CompositeNormalizer(image_root=tmp_path / "images")
    normalized = normalizer.normalize([asset], PdfMode.PDF)
    assert normalized == [asset]


def test_composite_normalizer_passthrough_images(tmp_path: Path) -> None:
    image = tmp_path / "frame.png"
    Image.new("RGB", (16, 16), color="blue").save(image)
    asset = Asset(path=image, media="image", mime="image/png", source_kind=SourceKind.LOCAL)
    normalizer = CompositeNormalizer(image_root=tmp_path / "images")
    normalized = normalizer.normalize([asset], PdfMode.AUTO)
    assert normalized == [asset]


def test_composite_normalizer_video_chunk_manifest(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_text("video")

    def fake_normalize(path: Path, output_dir: Path, encoder_chain=None):
        normalized = output_dir / f"{path.stem}-normalized.mp4"
        normalized.parent.mkdir(parents=True, exist_ok=True)
        normalized.write_text("normalized")
        return types.SimpleNamespace(
            path=normalized,
            encoder=EncoderSpec(VideoEncoderPreference.CPU, "libx264", tuple(), False),
            reused_existing=False,
        )

    def fake_probe(path: Path) -> VideoMetadata:
        return VideoMetadata(
            path=path,
            duration_seconds=120.0,
            size_bytes=1024,
            fps=30.0,
            width=1920,
            height=1080,
            video_codec="h264",
            audio_codec="aac",
            audio_sample_rate=44100,
        )

    def fake_plan(metadata: VideoMetadata, **kwargs) -> VideoChunkPlan:
        chunk_dir: Path = kwargs["chunk_dir"]
        chunk_dir.mkdir(parents=True, exist_ok=True)
        normalized_path: Path = kwargs["normalized_path"]
        chunks: list[VideoChunk] = []
        for idx, span in enumerate([(0.0, 60.0), (60.0, 120.0)]):
            chunk_path = chunk_dir / f"chunk-{idx}.mp4"
            chunk_path.write_text(f"chunk{idx}")
            chunks.append(VideoChunk(index=idx, start_seconds=span[0], end_seconds=span[1], path=chunk_path, source=normalized_path))
        return VideoChunkPlan(metadata=metadata, normalized_path=normalized_path, chunks=chunks, manifest_path=kwargs.get("manifest_path"))

    asset = Asset(path=source, media="video", source_kind=SourceKind.LOCAL)
    normalizer = CompositeNormalizer(
        video_root=tmp_path / "video",
        video_normalizer=fake_normalize,
        video_probe=fake_probe,
        video_planner=fake_plan,
    )
    job = _job_for(str(source))
    normalizer.prepare(job)
    chunks = normalizer.normalize([asset], PdfMode.AUTO)
    assert len(chunks) == 2
    for idx, chunk in enumerate(chunks):
        assert chunk.media == "video"
        assert chunk.meta["chunk_index"] == idx
        assert chunk.path.exists()

    descriptors = normalizer.chunk_descriptors()
    assert len(descriptors) == 2

    manifest_path = tmp_path / "clip" / "chunks.json"
    manifest_path = Path(chunks[0].meta["manifest_path"])
    data = json.loads(Path(manifest_path).read_text())
    assert len(data["chunks"]) == 2


def test_youtube_ingestor_passthrough() -> None:
    ingestor = YouTubeIngestor()
    job = _job_for("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assets = ingestor.discover(job)
    assert len(assets) == 1
    asset = assets[0]
    assert asset.media == "video"
    assert asset.meta["pass_through"] is True


class _StubDownloader:
    def __init__(self, content: bytes):
        self._content = content

    def download(self, file_id: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._content)
        return destination


def test_drive_ingestor_uses_downloader(tmp_path: Path) -> None:
    downloader = _StubDownloader(b"drive")
    ingestor = DriveIngestor(cache_dir=tmp_path / "cache", downloader=downloader)
    job = _job_for("drive://file123")
    assets = ingestor.discover(job)
    assert len(assets) == 1
    asset = assets[0]
    assert asset.source_kind == SourceKind.DRIVE
    assert asset.path.exists()


def test_composite_ingestor_routes(tmp_path: Path) -> None:
    local_file = tmp_path / "doc.pdf"
    _make_pdf(local_file)
    downloader = _StubDownloader(b"drive")
    composite = CompositeIngestor(drive=DriveIngestor(cache_dir=tmp_path / "cache", downloader=downloader))

    local_assets = composite.discover(_job_for(str(local_file)))
    assert local_assets and local_assets[0].source_kind == SourceKind.LOCAL

    yt_assets = composite.discover(_job_for("https://youtu.be/demo"))
    assert yt_assets and yt_assets[0].source_kind == SourceKind.YOUTUBE

    drive_assets = composite.discover(_job_for("drive://file123"))
    assert drive_assets and drive_assets[0].source_kind == SourceKind.DRIVE
