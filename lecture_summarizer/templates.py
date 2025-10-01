from __future__ import annotations
from pathlib import Path
from functools import lru_cache
from textwrap import dedent

from .constants import TEMPLATES_DIR


DEFAULT_PROMPTS = {
    "slides": dedent(
        """Transcribe the slide or deck page into LaTeX using the provided preamble.\n"
        "- Preserve math in LaTeX form.\n"
        "- Render bullet points with itemize/enumerate.\n"
        "- For graphics, insert tikz approximations or `[Placeholder: <description>]`.\n"
        "- Escape special characters such as %, $, &, _ appropriately.\n\n"
        "LaTeX Preamble:\n{{PREAMBLE}}"""
    ).strip(),
    "lecture": dedent(
        """Transcribe the handwritten or scanned lecture content into LaTeX.\n"
        "- Keep section structure, theorem environments, and displayed math.\n"
        "- Convert tables into tabular/tabularx when present.\n"
        "- Describe diagrams as placeholders when recreation is impractical.\n"
        "- Do not invent content; preserve typos with `[sic]` if needed.\n\n"
        "LaTeX Preamble:\n{{PREAMBLE}}"""
    ).strip(),
    "document": dedent(
        """Transcribe the document into LaTeX ready for compilation.\n"
        "- Preserve headings, lists, tables, and math.\n"
        "- Represent structured data in tabular/tabularx.\n"
        "- Replace complex figures with `[Placeholder: description]`.\n"
        "- Escape LaTeX-sensitive characters and keep provided metadata fields.\n\n"
        "LaTeX Preamble:\n{{PREAMBLE}}"""
    ).strip(),
    "image": dedent(
        """Transcribe the image contents into LaTeX using the supplied preamble.\n"
        "- Capture printed or handwritten math verbatim.\n"
        "- Use itemize/enumerate for bullet lists.\n"
        "- Replace illustrations with descriptive placeholders if necessary.\n"
        "- Do not include external files or packages beyond the preamble.\n\n"
        "LaTeX Preamble:\n{{PREAMBLE}}"""
    ).strip(),
}


class TemplateLoader:
    def __init__(self, base: Path | None = None):
        self.base = Path(base or TEMPLATES_DIR)

    def _load(self, name: str) -> str:
        path = self.base / name
        if not path.exists():
            raise FileNotFoundError(f"Template not found: {path}")
        return path.read_text()

    def _load_optional(self, name: str) -> str | None:
        path = self.base / name
        if not path.exists():
            return None
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

    @lru_cache(maxsize=None)
    def prompt(self, name: str) -> str:
        fname = f"{name}-prompt.txt"
        if (text := self._load_optional(fname)) is not None:
            return text
        if name in DEFAULT_PROMPTS:
            return DEFAULT_PROMPTS[name]
        raise FileNotFoundError(f"Prompt template not found for '{name}'. Expected {fname} in {self.base}")
