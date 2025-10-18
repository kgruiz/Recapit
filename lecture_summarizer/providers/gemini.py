from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Optional

from ..core.types import Asset, SourceKind

try:  # pragma: no cover - exercised in integration, not unit tests
    from google import genai  # type: ignore
    from google.genai import types as genai_types  # type: ignore
except ImportError:  # pragma: no cover - handled in tests via injection
    genai = None
    genai_types = None


_DEFAULT_CAPABILITIES = {
    "pdf": True,
    "image": True,
    "video": True,
    "audio": True,
    "text": True,
}


class GeminiProvider:
    """Wrapper around google-genai Client for media transcription."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: Optional[object] = None,
        types_module: Optional[object] = None,
        poll_interval: float = 0.5,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            if genai is None:
                raise ImportError("google-genai is required unless a client is provided")
            self._client = genai.Client(api_key=api_key)
        self._types = types_module or genai_types
        if self._types is None:
            raise ImportError("google-genai types are required unless a types_module is provided")
        self._model = model
        self._poll_interval = max(poll_interval, 0.1)

    def supports(self, capability: str) -> bool:
        return _DEFAULT_CAPABILITIES.get(capability, False)

    def transcribe(self, *, instruction: str, assets: Iterable[Asset], modality: str, meta: dict) -> str:
        parts = self._build_media_parts(list(assets))
        parts.append(self._types.Part(text=instruction))

        config = getattr(self._types, "GenerateContentConfig", None)
        if config is None:
            raise RuntimeError("types module missing GenerateContentConfig")

        content_ctor = getattr(self._types, "Content", None)
        if content_ctor is None:
            raise RuntimeError("types module missing Content")

        request = content_ctor(parts=parts)
        resp = self._client.models.generate_content(
            model=self._model,
            contents=request,
            config=config(),
        )
        return getattr(resp, "text", "") or ""

    def _build_media_parts(self, assets: list[Asset]) -> list[object]:
        parts: list[object] = []
        for asset in assets:
            if asset.source_kind == SourceKind.YOUTUBE and asset.meta and asset.meta.get("pass_through"):
                uri = asset.path.as_posix()
                file_part = self._types.Part(
                    file_data=self._types.FileData(file_uri=uri, mime_type=asset.mime or "video/*")
                )
                self._attach_video_metadata(file_part, asset)
                parts.append(file_part)
                continue

            if asset.meta and asset.meta.get("file_uri"):
                file_part = self._types.Part(
                    file_data=self._types.FileData(
                        file_uri=asset.meta["file_uri"],
                        mime_type=asset.mime or self._guess_mime(asset.path),
                    )
                )
                self._attach_video_metadata(file_part, asset)
                parts.append(file_part)
                continue

            if asset.meta and asset.meta.get("inline_bytes"):
                part = self._types.Part(
                    inline_data=self._types.Blob(
                        data=asset.meta["inline_bytes"],
                        mime_type=asset.mime or self._guess_mime(asset.path),
                    )
                )
                self._attach_video_metadata(part, asset)
                parts.append(part)
                continue

            uploaded = self._upload_asset(asset)
            file_part = self._types.Part(
                file_data=self._types.FileData(
                    file_uri=getattr(uploaded, "uri", None),
                    mime_type=getattr(uploaded, "mime_type", asset.mime or self._guess_mime(asset.path)),
                )
            )
            self._attach_video_metadata(file_part, asset)
            parts.append(file_part)
        return parts

    def _upload_asset(self, asset: Asset):
        result = self._client.files.upload(file=str(asset.path))
        name = getattr(result, "name", None)
        state = self._state_name(result)
        while state == "PROCESSING" and name:
            time.sleep(self._poll_interval)
            result = self._client.files.get(name=name)
            state = self._state_name(result)
        if state != "ACTIVE":
            raise RuntimeError(f"File upload failed with state {state}")
        return result

    @staticmethod
    def _state_name(obj: object) -> str | None:
        state = getattr(obj, "state", None)
        if state is None:
            return None
        if isinstance(state, str):
            return state
        return getattr(state, "name", None)

    def _attach_video_metadata(self, part: object, asset: Asset) -> None:
        if not asset.meta:
            return
        metadata_fields = {k: asset.meta.get(k) for k in ("start_offset", "end_offset", "fps") if asset.meta.get(k) is not None}
        if not metadata_fields:
            return
        video_meta_cls = getattr(self._types, "VideoMetadata", None)
        if video_meta_cls is None:
            return
        part.video_metadata = video_meta_cls(**metadata_fields)

    @staticmethod
    def _guess_mime(path: Path) -> str:
        if path.suffix.lower() == ".pdf":
            return "application/pdf"
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif"}:
            return f"image/{path.suffix.lower().lstrip('.')}"
        if path.suffix.lower() in {".mp4", ".mov"}:
            return "video/mp4"
        if path.suffix.lower() in {".mp3", ".wav", ".m4a"}:
            return "audio/mpeg"
        return "application/octet-stream"
