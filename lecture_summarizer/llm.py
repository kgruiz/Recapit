from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import PIL.Image
import pillow_avif  # noqa: F401
from google import genai


@dataclass
class LLMClient:
    api_key: str

    def __post_init__(self):
        self._client = genai.Client(api_key=self.api_key)

    def transcribe_image(self, *, model: str, instruction: str, image_path: Path) -> str:
        img = PIL.Image.open(image_path)
        resp = self._client.models.generate_content(
            model=model,
            contents=[(instruction, img)],
        )
        return (resp.text or "").strip()

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
