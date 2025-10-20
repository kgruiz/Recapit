from __future__ import annotations

from dataclasses import dataclass

from ..core.types import Kind
from ..templates import TemplateLoader


DEFAULT_PROMPTS: dict[Kind, str] = {
    Kind.SLIDES: "{{PREAMBLE}}\nSummarize slide content. Preserve slide order and hierarchy. Keep math as LaTeX.",
    Kind.LECTURE: "{{PREAMBLE}}\nProduce a lecture summary with [MM:SS] timestamps. Capture key arguments, definitions, and any examples. Keep math as LaTeX.",
    Kind.DOCUMENT: "{{PREAMBLE}}\nSummarize the document. Preserve headings and highlight key conclusions. Keep math as LaTeX.",
    Kind.IMAGE: "{{PREAMBLE}}\nDescribe the image with technical precision. Capture any text (convert math to LaTeX) and notable visual details.",
    Kind.VIDEO: (
        "{{PREAMBLE}}\n"
        "Task: Produce a transcript with [MM:SS] timestamps and a timeline of salient visual events.\n"
        "Include: visual descriptions, slide titles, equations in LaTeX, and noteworthy gestures or annotations.\n"
        "Output: Markdown with headings 'Transcript', 'Timeline', and 'Key Terms'."
    ),
}


@dataclass
class TemplatePromptStrategy:
    loader: TemplateLoader
    kind: Kind
    default_prompt: str

    def preamble(self) -> str:
        if self.kind == Kind.SLIDES:
            return self.loader.slide_preamble()
        if self.kind == Kind.LECTURE:
            return self.loader.lecture_preamble()
        if self.kind == Kind.IMAGE:
            return self.loader.image_preamble()
        if self.kind == Kind.VIDEO:
            return self.loader.video_preamble()
        return self.loader.document_preamble()

    def instruction(self, preamble: str) -> str:
        prompt = self.loader.prompt(self.kind.value, default=self.default_prompt)
        return prompt.replace("{{PREAMBLE}}", preamble)


def build_prompt_strategies(loader: TemplateLoader) -> dict[Kind, TemplatePromptStrategy]:
    return {
        kind: TemplatePromptStrategy(loader=loader, kind=kind, default_prompt=DEFAULT_PROMPTS[kind])
        for kind in Kind
    }
