from __future__ import annotations

import contextlib
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import sys
import types
from pathlib import Path

from PIL import Image

from lecture_summarizer.core.types import Asset, Job, PdfMode, SourceKind
from lecture_summarizer.ingest import LocalIngestor, URLIngestor, CompositeNormalizer


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
