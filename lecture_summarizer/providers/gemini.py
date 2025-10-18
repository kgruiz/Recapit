from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from ..core.types import Asset, SourceKind
from ..telemetry import RunMonitor, RequestEvent

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
        monitor: RunMonitor | None = None,
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
        self._monitor = monitor

    def supports(self, capability: str) -> bool:
        return _DEFAULT_CAPABILITIES.get(capability, False)

    def transcribe(self, *, instruction: str, assets: Iterable[Asset], modality: str, meta: dict) -> str:
        asset_list = list(assets)
        parts, event_assets = self._build_media_parts(asset_list)
        parts.append(self._types.Part(text=instruction))

        config = getattr(self._types, "GenerateContentConfig", None)
        if config is None:
            raise RuntimeError("types module missing GenerateContentConfig")

        content_ctor = getattr(self._types, "Content", None)
        if content_ctor is None:
            raise RuntimeError("types module missing Content")

        request = content_ctor(parts=parts)
        started = datetime.now(timezone.utc)
        resp = self._client.models.generate_content(
            model=self._model,
            contents=request,
            config=config(),
        )
        finished = datetime.now(timezone.utc)
        text = getattr(resp, "text", "") or ""

        self._record_event(
            modality=modality,
            started=started,
            finished=finished,
            meta=meta,
            assets=event_assets,
            response=resp,
        )

        return text

    def _build_media_parts(self, assets: list[Asset]) -> tuple[list[object], list[dict]]:
        parts: list[object] = []
        event_assets: list[dict] = []
        for asset in assets:
            asset_info = {
                "path": str(asset.path),
                "media": asset.media,
                "source_kind": asset.source_kind.value,
            }
            if asset.meta:
                asset_info.update({k: v for k, v in asset.meta.items() if isinstance(k, str)})
            if asset.source_kind == SourceKind.YOUTUBE and asset.meta and asset.meta.get("pass_through"):
                uri = asset.path.as_posix()
                file_part = self._types.Part(
                    file_data=self._types.FileData(file_uri=uri, mime_type=asset.mime or "video/*")
                )
                self._attach_video_metadata(file_part, asset)
                parts.append(file_part)
                asset_info["file_uri"] = uri
                event_assets.append(asset_info)
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
                asset_info["file_uri"] = asset.meta["file_uri"]
                event_assets.append(asset_info)
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
                event_assets.append(asset_info)
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
            asset_info["file_uri"] = getattr(uploaded, "uri", None)
            asset_info["upload_state"] = self._state_name(uploaded)
            event_assets.append(asset_info)
        return parts, event_assets

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

    def _record_event(
        self,
        *,
        modality: str,
        started: datetime,
        finished: datetime,
        meta: dict,
        assets: list[dict],
        response: object,
    ) -> None:
        if self._monitor is None:
            return

        usage = getattr(response, "usage_metadata", None)

        def _token(attr: str, fallback: str | None = None) -> Optional[int]:
            if usage is None:
                return None
            value = getattr(usage, attr, None)
            if value is None and fallback:
                value = getattr(usage, fallback, None)
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        input_tokens = _token("input_tokens", fallback="prompt_token_count")
        output_tokens = _token("output_tokens", fallback="candidates_token_count")
        total_tokens = _token("total_tokens", fallback="total_token_count")
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens

        metadata_payload = dict(meta or {})
        metadata_payload.setdefault("assets", assets)

        self._monitor.record(
            RequestEvent(
                model=self._model,
                modality=modality,
                started_at=started,
                finished_at=finished,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                metadata=metadata_payload,
            )
        )
