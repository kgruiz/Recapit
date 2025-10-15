from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import mimetypes
import time

import PIL.Image
import pillow_avif  # noqa: F401
from google import genai
from google.genai import types

from .constants import MODEL_CAPABILITIES
from .video import seconds_to_iso8601

@dataclass
class LLMClient:
    api_key: str

    def __post_init__(self):
        self._client = genai.Client(api_key=self.api_key)

    def _upload_and_wait(self, *, path: Path) -> types.File:
        mime_type, _ = mimetypes.guess_type(str(path))
        upload_kwargs: dict[str, object] = {"file": path}
        if mime_type:
            upload_kwargs["config"] = {"mime_type": mime_type}
        file_ref = self._client.files.upload(**upload_kwargs)
        return self._await_active_file(file_ref)

    def _await_active_file(self, file_ref: types.File, *, timeout: float = 600.0, poll_interval: float = 2.0) -> types.File:
        deadline = time.monotonic() + timeout
        state_name = getattr(getattr(file_ref, "state", None), "name", "ACTIVE")
        while state_name == "PROCESSING":
            if time.monotonic() > deadline:
                raise TimeoutError(f"Timed out waiting for {file_ref.name} to become ACTIVE")
            time.sleep(poll_interval)
            file_ref = self._client.files.get(name=file_ref.name)
            state_name = getattr(getattr(file_ref, "state", None), "name", "ACTIVE")
        if state_name != "ACTIVE":
            raise RuntimeError(f"File upload failed with state '{state_name}' for {file_ref.name}")
        return file_ref

    def transcribe_image(self, *, model: str, instruction: str, image_path: Path) -> str:
        img = PIL.Image.open(image_path)
        resp = self._client.models.generate_content(
            model=model,
            contents=[(instruction, img)],
        )
        return (resp.text or "").strip()

    def transcribe_pdf(self, *, model: str, instruction: str, pdf_path: Path) -> str:
        upload = self._client.files.upload(file=pdf_path)
        resp = self._client.models.generate_content(
            model=model,
            contents=[instruction, upload],
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
    ):
        file_ref = self._upload_and_wait(path=video_path)

        metadata_kwargs: dict[str, object] = {}
        if start_offset is not None:
            metadata_kwargs["start_offset"] = seconds_to_iso8601(start_offset)
        if end_offset is not None:
            metadata_kwargs["end_offset"] = seconds_to_iso8601(end_offset)
        if fps is not None:
            metadata_kwargs["fps"] = fps

        file_uri = getattr(file_ref, "uri", None) or getattr(file_ref, "name", None)
        mime_type = getattr(file_ref, "mime_type", None)
        if not mime_type:
            mime_type, _ = mimetypes.guess_type(str(video_path))

        part_kwargs: dict[str, object] = {
            "file_data": types.FileData(file_uri=file_uri, mime_type=mime_type or "video/mp4"),
        }
        if metadata_kwargs:
            part_kwargs["video_metadata"] = types.VideoMetadata(**metadata_kwargs)

        parts = [
            types.Part(**part_kwargs),
            types.Part(text=instruction),
        ]
        content = types.Content(parts=parts)

        config_kwargs: dict[str, object] = {}
        if media_resolution:
            config_kwargs["media_resolution"] = media_resolution
        if thinking_budget is not None or include_thoughts:
            thinking_kwargs: dict[str, object] = {}
            if thinking_budget is not None:
                thinking_kwargs["budget_tokens"] = thinking_budget
            if include_thoughts:
                thinking_kwargs["include_thoughts"] = True
            config_kwargs["thinking_config"] = types.ThinkingConfig(**thinking_kwargs)

        if config_kwargs:
            config = types.GenerateContentConfig(**config_kwargs)
            resp = self._client.models.generate_content(model=model, contents=content, config=config)
        else:
            resp = self._client.models.generate_content(model=model, contents=content)

        return resp

    def latex_to_markdown(self, *, model: str, prompt: str, latex_text: str) -> str:
        if not latex_text.strip():
            return ""
        resp = self._client.models.generate_content(
            model=model,
            contents=[f"Instructions:\n{prompt}\n\nLaTeX:\n{latex_text}"],
        )
        return (resp.text or "").strip()

    def latex_to_json(self, *, model: str, prompt: str, latex_text: str) -> str:
        if not latex_text.strip():
            return "[]"
        resp = self._client.models.generate_content(
            model=model,
            contents=[f"Instructions:\n{prompt}\n\n```\n{latex_text}\n```"],
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
