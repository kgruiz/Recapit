from __future__ import annotations
from pathlib import Path
from functools import lru_cache

from .constants import TEMPLATES_DIR


class TemplateLoader:
    def __init__(self, base: Path | None = None):
        self.base = Path(base or TEMPLATES_DIR)

    def _load(self, name: str) -> str:
        path = self.base / name
        if not path.exists():
            raise FileNotFoundError(f"Template not found: {path}")
        return path.read_text()

    @lru_cache(maxsize=None)
    def slide_preamble(self) -> str:
        return self._load("slide-template.txt")

    @lru_cache(maxsize=None)
    def lecture_preamble(self) -> str:
        return self._load("lecture-template.txt")

    @lru_cache(maxsize=None)
    def document_preamble(self) -> str:
        return self._load("document-template.txt")

    @lru_cache(maxsize=None)
    def image_preamble(self) -> str:
        return self._load("image-template.txt")

    @lru_cache(maxsize=None)
    def latex_to_md_prompt(self) -> str:
        return self._load("latex-to-md-template.txt")

    @lru_cache(maxsize=None)
    def latex_to_json_prompt(self) -> str:
        return self._load("latex-to-json-template.txt")
