from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
import mimetypes
import logging
import time
from typing import Any, Callable, Dict

import PIL.Image
import pillow_avif  # noqa: F401
import httpx
from google import genai
from google.genai import types

from .constants import MODEL_CAPABILITIES
from .telemetry import RunMonitor, RequestEvent
from .quota import QuotaMonitor
from .video import seconds_to_iso8601

logger = logging.getLogger(__name__)

@dataclass
class LLMClient:
    api_key: str
    recorder: RunMonitor | None = field(default=None, repr=False)
    quota: QuotaMonitor | None = field(default=None, repr=False)

    def __post_init__(self):
        # Increase HTTP timeouts so large media uploads and responses have ample time to complete.
        self._timeout_ms = 600_000  # Gemini SDK interprets timeout in milliseconds.
        self._http_options = types.HttpOptions(timeout=self._timeout_ms)
        self._client = genai.Client(api_key=self.api_key, http_options=self._http_options)
        # Ensure the underlying httpx client uses generous timeouts for all requests.
        api_client = getattr(self._client, "_api_client", None)
        if api_client and hasattr(api_client, "_httpx_client"):
            timeout = httpx.Timeout(timeout=600.0, connect=30.0)
            api_client._httpx_client.timeout = timeout
        self._recorder = self.recorder
        self._quota = self.quota
        self._max_retries = 3
        self._backoff_base = 1.0
        self._backoff_cap = 8.0

    def set_recorder(self, recorder: RunMonitor | None) -> None:
        self._recorder = recorder

    def set_quota_monitor(self, quota: QuotaMonitor | None) -> None:
        self._quota = quota

    @staticmethod
    def _merge_metadata(
        base: Dict[str, object] | None,
        extra: Dict[str, object],
    ) -> Dict[str, object]:
        merged: Dict[str, object] = dict(extra)
        if base:
            merged.update(base)
        return merged

    @staticmethod
    def _extract_usage_counts(usage: Any) -> tuple[int | None, int | None, int | None]:
        if usage is None:
            return None, None, None
        if isinstance(usage, dict):
            prompt = usage.get("prompt_token_count")
            output = usage.get("candidates_token_count")
            total = usage.get("total_token_count")
            fallback_total = usage.get("total_tokens")
        else:
            prompt = getattr(usage, "prompt_token_count", None)
            output = getattr(usage, "candidates_token_count", None)
            total = getattr(usage, "total_token_count", None)
            fallback_total = getattr(usage, "total_tokens", None)
        if total is None:
            total = fallback_total
        if prompt is None:
            prompt = total
        # Ensure ints or None
        prompt = int(prompt) if prompt is not None else None
        output = int(output) if output is not None else None
        total = int(total) if total is not None else None
        return prompt, output, total

    def _record_event(
        self,
        *,
        model: str,
        modality: str,
        metadata: Dict[str, object],
        started_at: float,
        finished_at: float,
        usage: Any,
    ) -> None:
        if self._recorder is None:
            return
        input_tokens, output_tokens, total_tokens = self._extract_usage_counts(usage)
        event = RequestEvent(
            model=model,
            modality=modality,
            started_at=datetime.fromtimestamp(started_at, tz=timezone.utc),
            finished_at=datetime.fromtimestamp(finished_at, tz=timezone.utc),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            metadata=dict(metadata),
        )
        self._recorder.record(event)
        logger.debug("gemini_request %s", event.to_dict())
        if self._quota is not None:
            self._quota.register_event(
                model=model,
                timestamp=event.finished_at.timestamp(),
                total_tokens=event.total_tokens,
            )

    def _execute_with_retries(
        self,
        func: Callable[[], Any],
        *,
        model: str,
        modality: str,
        metadata: Dict[str, object],
    ) -> Any:
        attempt = 0
        while True:
            try:
                return func()
            except Exception as exc:  # noqa: BLE001
                if attempt >= self._max_retries or not self._should_retry(exc):
                    raise
                wait_for = min(self._backoff_base * (2**attempt), self._backoff_cap)
                source = metadata.get("source_path") if metadata else None
                if source:
                    logger.warning(
                        "Retrying %s request for %s (%s) in %.2fs due to %s",
                        modality,
                        model,
                        source,
                        wait_for,
                        exc,
                    )
                else:
                    logger.warning(
                        "Retrying %s request for %s in %.2fs due to %s",
                        modality,
                        model,
                        wait_for,
                        exc,
                    )
                time.sleep(wait_for)
                attempt += 1

    @staticmethod
    def _should_retry(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if status_code == 429:
            return True
        response = getattr(exc, "response", None)
        if response is not None and getattr(response, "status_code", None) == 429:
            return True
        code = getattr(exc, "code", None)
        if code == 429:
            return True
        message = str(exc).lower()
        return "rate limit" in message or "quota" in message or "429" in message

    def _upload_and_wait(self, *, path: Path) -> types.File:
        mime_type, _ = mimetypes.guess_type(str(path))
        upload_kwargs: dict[str, object] = {"file": path}
        if mime_type:
            upload_kwargs["config"] = types.UploadFileConfig(mimeType=mime_type)
        size_bytes = 0
        try:
            size_bytes = path.stat().st_size
        except OSError:
            size_bytes = 0

        def _perform_upload() -> types.File:
            reference = self._execute_with_retries(
                lambda: self._client.files.upload(**upload_kwargs),
                model="files",
                modality="upload",
                metadata={"source_path": str(path)},
            )
            return self._await_active_file(reference)

        if self._quota is not None:
            with self._quota.track_upload(path=str(path), size_bytes=size_bytes):
                return _perform_upload()
        return _perform_upload()

    def _await_active_file(self, file_ref: types.File, *, timeout: float = 600.0, poll_interval: float = 2.0) -> types.File:
        deadline = time.monotonic() + timeout
        state_name = getattr(getattr(file_ref, "state", None), "name", "ACTIVE")
        while state_name == "PROCESSING":
            if time.monotonic() > deadline:
                raise TimeoutError(f"Timed out waiting for {file_ref.name} to become ACTIVE")
            time.sleep(poll_interval)
            file_ref = self._client.files.get(
                name=file_ref.name,
                config=types.GetFileConfig(httpOptions=self._http_options),
            )
            state_name = getattr(getattr(file_ref, "state", None), "name", "ACTIVE")
        if state_name != "ACTIVE":
            raise RuntimeError(f"File upload failed with state '{state_name}' for {file_ref.name}")
        return file_ref

    def transcribe_image(
        self,
        *,
        model: str,
        instruction: str,
        image_path: Path,
        metadata: Dict[str, object] | None = None,
    ) -> str:
        metadata_payload = self._merge_metadata(metadata, {"source_path": str(image_path)})
        started = time.time()

        def _invoke() -> types.GenerateContentResponse:
            img = PIL.Image.open(image_path)
            try:
                return self._client.models.generate_content(
                    model=model,
                    contents=[(instruction, img)],
                    config=types.GenerateContentConfig(httpOptions=self._http_options),
                )
            finally:
                try:
                    img.close()
                except Exception:  # noqa: BLE001
                    pass

        resp = self._execute_with_retries(
            _invoke,
            model=model,
            modality="image",
            metadata=metadata_payload,
        )
        finished = time.time()
        self._record_event(
            model=model,
            modality="image",
            metadata=metadata_payload,
            started_at=started,
            finished_at=finished,
            usage=getattr(resp, "usage_metadata", None),
        )
        return (resp.text or "").strip()

    def transcribe_pdf(
        self,
        *,
        model: str,
        instruction: str,
        pdf_path: Path,
        metadata: Dict[str, object] | None = None,
    ) -> str:
        metadata_payload = self._merge_metadata(metadata, {"source_path": str(pdf_path)})
        started = time.time()
        upload = self._upload_and_wait(path=pdf_path)

        def _invoke() -> types.GenerateContentResponse:
            return self._client.models.generate_content(
                model=model,
                contents=[instruction, upload],
                config=types.GenerateContentConfig(httpOptions=self._http_options),
            )

        resp = self._execute_with_retries(
            _invoke,
            model=model,
            modality="pdf",
            metadata=metadata_payload,
        )
        finished = time.time()
        self._record_event(
            model=model,
            modality="pdf",
            metadata=metadata_payload,
            started_at=started,
            finished_at=finished,
            usage=getattr(resp, "usage_metadata", None),
        )
        return (resp.text or "").strip()

    def transcribe_video(
        self,
        *,
        model: str,
        instruction: str,
        video_path: Path,
        start_offset: float | None = None,
        end_offset: float | None = None,
        fps: float | None = None,
        media_resolution: str | None = None,
        thinking_budget: int | None = None,
        include_thoughts: bool = False,
        metadata: Dict[str, object] | None = None,
    ):
        metadata_payload = self._merge_metadata(
            metadata,
            {
                "source_path": str(video_path),
                "start_offset": start_offset,
                "end_offset": end_offset,
                "fps": fps,
                "media_resolution": media_resolution,
                "thinking_budget": thinking_budget,
                "include_thoughts": include_thoughts,
            },
        )
        started = time.time()
        file_ref = self._upload_and_wait(path=video_path)

        file_uri = getattr(file_ref, "uri", None) or getattr(file_ref, "name", None)
        mime_type = getattr(file_ref, "mime_type", None)
        if not mime_type:
            mime_type, _ = mimetypes.guess_type(str(video_path))

        part_kwargs: dict[str, object] = {
            "file_data": types.FileData(file_uri=file_uri, mime_type=mime_type or "video/mp4"),
        }

        parts = [
            types.Part(**part_kwargs),
            types.Part(text=instruction),
        ]
        content = types.Content(parts=parts)

        config_kwargs: dict[str, object] = {"httpOptions": self._http_options}
        if media_resolution:
            config_kwargs["media_resolution"] = media_resolution
        if thinking_budget is not None or include_thoughts:
            thinking_kwargs: dict[str, object] = {}
            if thinking_budget is not None:
                thinking_kwargs["budget_tokens"] = thinking_budget
            if include_thoughts:
                thinking_kwargs["include_thoughts"] = True
            config_kwargs["thinking_config"] = types.ThinkingConfig(**thinking_kwargs)

        config = types.GenerateContentConfig(**config_kwargs)

        def _invoke() -> types.GenerateContentResponse:
            return self._client.models.generate_content(model=model, contents=content, config=config)

        resp = self._execute_with_retries(
            _invoke,
            model=model,
            modality="video",
            metadata=metadata_payload,
        )

        finished = time.time()
        self._record_event(
            model=model,
            modality="video",
            metadata=metadata_payload,
            started_at=started,
            finished_at=finished,
            usage=getattr(resp, "usage_metadata", None),
        )
        return resp

    def count_video_tokens(
        self,
        *,
        model: str,
        instruction: str | None,
        video_path: Path,
        start_offset: float | None = None,
        end_offset: float | None = None,
        fps: float | None = None,
        metadata: Dict[str, object] | None = None,
    ):
        started = time.time()
        file_ref = self._upload_and_wait(path=video_path)

        file_uri = getattr(file_ref, "uri", None) or getattr(file_ref, "name", None)
        mime_type = getattr(file_ref, "mime_type", None)
        if not mime_type:
            mime_type, _ = mimetypes.guess_type(str(video_path))

        part_kwargs: dict[str, object] = {
            "file_data": types.FileData(file_uri=file_uri, mime_type=mime_type or "video/mp4"),
        }

        parts = [types.Part(**part_kwargs)]
        if instruction:
            parts.append(types.Part(text=instruction))

        content = types.Content(parts=parts)
        count_config = types.CountTokensConfig(httpOptions=self._http_options)
        metadata_payload = self._merge_metadata(
            metadata,
            {
                "source_path": str(video_path),
                "start_offset": start_offset,
                "end_offset": end_offset,
                "fps": fps,
            },
        )

        def _invoke() -> types.CountTokensResponse:
            return self._client.models.count_tokens(model=model, contents=[content], config=count_config)

        resp = self._execute_with_retries(
            _invoke,
            model=model,
            modality="video_token_count",
            metadata=metadata_payload,
        )
        finished = time.time()
        usage_payload: Dict[str, object] | None = None
        total_tokens = getattr(resp, "total_tokens", None)
        if total_tokens is not None:
            usage_payload = {"prompt_token_count": total_tokens, "candidates_token_count": None, "total_token_count": total_tokens}
        self._record_event(
            model=model,
            modality="video_token_count",
            metadata=metadata_payload,
            started_at=started,
            finished_at=finished,
            usage=usage_payload or getattr(resp, "usage_metadata", None),
        )
        return resp

    def latex_to_markdown(
        self,
        *,
        model: str,
        prompt: str,
        latex_text: str,
        metadata: Dict[str, object] | None = None,
    ) -> str:
        if not latex_text.strip():
            return ""
        started = time.time()
        metadata_payload = self._merge_metadata(metadata, {"operation": "latex_to_markdown"})

        def _invoke() -> types.GenerateContentResponse:
            return self._client.models.generate_content(
                model=model,
                contents=[f"Instructions:\n{prompt}\n\nLaTeX:\n{latex_text}"],
                config=types.GenerateContentConfig(httpOptions=self._http_options),
            )

        resp = self._execute_with_retries(
            _invoke,
            model=model,
            modality="latex_to_markdown",
            metadata=metadata_payload,
        )
        finished = time.time()
        self._record_event(
            model=model,
            modality="latex_to_markdown",
            metadata=metadata_payload,
            started_at=started,
            finished_at=finished,
            usage=getattr(resp, "usage_metadata", None),
        )
        return (resp.text or "").strip()

    def latex_to_json(
        self,
        *,
        model: str,
        prompt: str,
        latex_text: str,
        metadata: Dict[str, object] | None = None,
    ) -> str:
        if not latex_text.strip():
            return "[]"
        started = time.time()
        metadata_payload = self._merge_metadata(metadata, {"operation": "latex_to_json"})

        def _invoke() -> types.GenerateContentResponse:
            return self._client.models.generate_content(
                model=model,
                contents=[f"Instructions:\n{prompt}\n\n```\n{latex_text}\n```"],
                config=types.GenerateContentConfig(httpOptions=self._http_options),
            )

        resp = self._execute_with_retries(
            _invoke,
            model=model,
            modality="latex_to_json",
            metadata=metadata_payload,
        )
        finished = time.time()
        self._record_event(
            model=model,
            modality="latex_to_json",
            metadata=metadata_payload,
            started_at=started,
            finished_at=finished,
            usage=getattr(resp, "usage_metadata", None),
        )
        return (resp.text or "").strip()

    def supports(self, model: str, capability: str) -> bool:
        caps = MODEL_CAPABILITIES.get(model)
        if caps is None and "-preview" in model:
            caps = MODEL_CAPABILITIES.get(model.split("-preview", 1)[0])
        if caps is None and "-exp" in model:
            caps = MODEL_CAPABILITIES.get(model.split("-exp", 1)[0])
        if caps is None:
            return capability == "text"
        return capability in caps
