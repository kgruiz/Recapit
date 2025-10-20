from __future__ import annotations

from pathlib import Path
import types

import pytest

from recapit.core.types import Asset, SourceKind
from recapit.providers.gemini import GeminiProvider
from recapit.telemetry import RunMonitor


class _FakeTypes:
    class FileData:
        def __init__(self, file_uri: str, mime_type: str | None = None):
            self.file_uri = file_uri
            self.mime_type = mime_type

    class Blob:
        def __init__(self, data: bytes, mime_type: str | None = None):
            self.data = data
            self.mime_type = mime_type

    class VideoMetadata:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Part:
        def __init__(self, file_data=None, inline_data=None, text=None):
            self.file_data = file_data
            self.inline_data = inline_data
            self.text = text
            self.video_metadata = None

    class Content:
        def __init__(self, *, parts):
            self.parts = parts

    class GenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs


class _FakeFiles:
    def __init__(self) -> None:
        self.upload_calls: list[str] = []
        self.get_calls: list[str] = []
        self._uploaded = {}

    def upload(self, *, file: str):
        self.upload_calls.append(file)
        obj = type("Upload", (), {"name": file, "uri": f"file://{Path(file).name}", "mime_type": "application/pdf", "state": type("S", (), {"name": "ACTIVE"})()})
        self._uploaded[file] = obj
        return obj

    def get(self, *, name: str):
        self.get_calls.append(name)
        return self._uploaded[name]


class _FakeModels:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate_content(self, *, model: str, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return type("Resp", (), {"text": "stub response"})()


class _FakeClient:
    def __init__(self) -> None:
        self.files = _FakeFiles()
        self.models = _FakeModels()


def test_provider_uploads_and_calls_generate(tmp_path) -> None:
    asset_path = tmp_path / "doc.pdf"
    asset_path.write_text("stub")
    asset = Asset(path=asset_path, media="pdf", source_kind=SourceKind.LOCAL)

    client = _FakeClient()
    provider = GeminiProvider(api_key="dummy", model="gemini-test", client=client, types_module=_FakeTypes())

    output = provider.transcribe(instruction="Summarize", assets=[asset], modality="pdf", meta={})

    assert output == "stub response"
    assert client.files.upload_calls == [str(asset_path)]
    call = client.models.calls[0]
    assert call["model"] == "gemini-test"
    parts = call["contents"].parts
    assert len(parts) == 2
    assert parts[0].file_data.file_uri.endswith("doc.pdf")
    assert parts[1].text == "Summarize"


def test_provider_respects_existing_file_uri(tmp_path) -> None:
    asset_path = tmp_path / "image.png"
    asset_path.write_bytes(b"data")
    asset = Asset(
        path=asset_path,
        media="image",
        source_kind=SourceKind.LOCAL,
        meta={"file_uri": "gs://bucket/image.png"},
        mime="image/png",
    )

    client = _FakeClient()
    provider = GeminiProvider(api_key="dummy", model="gemini-test", client=client, types_module=_FakeTypes())
    provider.transcribe(instruction="describe", assets=[asset], modality="image", meta={})

    assert not client.files.upload_calls
    parts = client.models.calls[0]["contents"].parts
    assert parts[0].file_data.file_uri == "gs://bucket/image.png"


def test_provider_supports_capabilities(tmp_path) -> None:
    provider = GeminiProvider(api_key="dummy", model="x", client=_FakeClient(), types_module=_FakeTypes())
    assert provider.supports("pdf")
    assert not provider.supports("unknown")


def test_provider_records_events(tmp_path) -> None:
    asset_path = tmp_path / "doc.pdf"
    asset_path.write_text("stub")
    asset = Asset(path=asset_path, media="pdf", source_kind=SourceKind.LOCAL)

    client = _FakeClient()

    class _Usage:
        input_tokens = 100
        output_tokens = 20
        total_tokens = 120

    class _Response:
        text = "response"
        usage_metadata = _Usage()

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _Response()

    client.models.generate_content = types.MethodType(generate_content, client.models)  # type: ignore[attr-defined]

    monitor = RunMonitor()
    provider = GeminiProvider(api_key="dummy", model="gemini-test", client=client, types_module=_FakeTypes(), monitor=monitor)
    provider.transcribe(instruction="Summarize", assets=[asset], modality="pdf", meta={"source": "doc"})

    events = monitor.events()
    assert len(events) == 1
    event = events[0]
    assert event.model == "gemini-test"
    assert event.input_tokens == 100
    assert event.metadata["assets"][0]["file_uri"].endswith("doc.pdf")
