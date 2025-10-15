from __future__ import annotations
from pathlib import Path
from functools import lru_cache
from textwrap import dedent

from .constants import TEMPLATES_DIR


DEFAULT_PREAMBLES = {
    "slides": dedent(
        r"""\documentclass[aspectratio=43]{beamer}

\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{amsfonts}
\usepackage{tikz}
\usepackage{xcolor}
\usepackage{graphicx}
\usepackage{hyperref}

\usetheme{Madrid}
\setbeamertemplate{navigation symbols}{}

\title{}
\author{}
\date{}

\begin{document}
"""
    ).strip()
    + "\n",
    "lecture": dedent(
        r"""\documentclass{article}

\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{amsfonts}
\usepackage{amsthm}
\usepackage{physics}
\usepackage{bm}
\usepackage{geometry}
\geometry{margin=1in}

\title{}
\author{}
\date{}

\begin{document}
"""
    ).strip()
    + "\n",
    "document": dedent(
        r"""\documentclass{article}

\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{amsfonts}
\usepackage{amsthm}
\usepackage{graphicx}
\usepackage{tabularx}
\usepackage{booktabs}
\usepackage{xcolor}
\usepackage{enumitem}

\title{}
\author{}
\date{}

\begin{document}
"""
    ).strip()
    + "\n",
    "image": dedent(
        r"""\documentclass{article}

\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{amsfonts}
\usepackage{amsthm}
\usepackage{geometry}
\geometry{margin=1in}

\begin{document}
"""
    ).strip()
    + "\n",
    "video": dedent(
        r"""\documentclass{article}

\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{amsfonts}
\usepackage{amsthm}
\usepackage{xcolor}
\usepackage{enumitem}
\usepackage{geometry}
\geometry{margin=1in}

\begin{document}
"""
    ).strip()
    + "\n",
}


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
    "video": dedent(
        """Watch the lecture video and produce LaTeX notes using the supplied preamble.\n"
        "- Provide concise paragraphs summarizing spoken content.\n"
        "- Add `\\textbf{[MM:SS]}` markers at key transitions (speaker changes, slide swaps).\n"
        "- Capture important equations in LaTeX, otherwise use `[Placeholder: description]` for visuals.\n"
        "- Include bullet lists (`itemize`) for enumerations mentioned verbally.\n"
        "- Do not fabricate material; if audio is unclear, note `[inaudible]` with an approximate timestamp.\n\n"
        "LaTeX Preamble:\n{{PREAMBLE}}"""
    ).strip(),
}


class TemplateLoader:
    def __init__(self, base: Path | None = None):
        self.base = Path(base or TEMPLATES_DIR)

    def _load_optional(self, name: str) -> str | None:
        path = self.base / name
        if not path.exists():
            return None
        return path.read_text()

    def _load_or_default(self, name: str, default: str | None) -> str:
        text = self._load_optional(name)
        if text is not None:
            return text
        if default is None:
            raise FileNotFoundError(f"Template not found: {self.base / name}")
        return default

    @lru_cache(maxsize=None)
    def slide_preamble(self) -> str:
        return self._load_or_default("slide-template.txt", DEFAULT_PREAMBLES["slides"])

    @lru_cache(maxsize=None)
    def lecture_preamble(self) -> str:
        return self._load_or_default("lecture-template.txt", DEFAULT_PREAMBLES["lecture"])

    @lru_cache(maxsize=None)
    def document_preamble(self) -> str:
        return self._load_or_default("document-template.txt", DEFAULT_PREAMBLES["document"])

    @lru_cache(maxsize=None)
    def image_preamble(self) -> str:
        return self._load_or_default("image-template.txt", DEFAULT_PREAMBLES["image"])

    @lru_cache(maxsize=None)
    def video_preamble(self) -> str:
        return self._load_or_default("video-template.txt", DEFAULT_PREAMBLES["video"])

    @lru_cache(maxsize=None)
    def latex_to_md_prompt(self) -> str:
        default = dedent(
            """Convert the LaTeX source into Markdown while preserving structure.
- Keep headings mapping section -> #, subsection -> ##.
- Preserve math using $...$ or $$...$$.
- Use bullet/numbered lists for itemize/enumerate.
- Render tables as GitHub-flavored Markdown tables where possible.
- Replace images or TikZ drawings with `[Placeholder: description]`.
- Remove LaTeX-only preamble commands.

Return only the Markdown.
"""
        ).strip()
        return self._load_or_default("latex-to-md-template.txt", default)

    @lru_cache(maxsize=None)
    def latex_to_json_prompt(self) -> str:
        default = dedent(
            """Convert the LaTeX table or structured content into well-formed JSON.
- Use the first row as headers when available.
- Preserve numeric types where obvious, otherwise use strings.
- Output a JSON array of objects.
- Do not include explanations.
"""
        ).strip()
        return self._load_or_default("latex-to-json-template.txt", default)

    @lru_cache(maxsize=None)
    def prompt(self, name: str) -> str:
        fname = f"{name}-prompt.txt"
        if (text := self._load_optional(fname)) is not None:
            return text
        if name in DEFAULT_PROMPTS:
            return DEFAULT_PROMPTS[name]
        raise FileNotFoundError(f"Prompt template not found for '{name}'. Expected {fname} in {self.base}")
