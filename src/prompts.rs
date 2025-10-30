use std::collections::HashMap;

use crate::core::{Kind, OutputFormat, PromptStrategy};
use crate::templates::TemplateLoader;

#[derive(Clone)]
pub struct TemplatePromptStrategy {
    loader: TemplateLoader,
    kind: Kind,
}

impl TemplatePromptStrategy {
    pub fn new(loader: TemplateLoader, kind: Kind) -> Self {
        Self { loader, kind }
    }

    fn default_prompt(&self, format: OutputFormat) -> &'static str {
        match (self.kind, format) {
            (Kind::Slides, OutputFormat::Markdown) => "{{PREAMBLE}}\nTranscribe each slide faithfully in Markdown. Use level-2 headings for slide titles when they exist, preserve bullet hierarchies, and keep equations with $...$ or $$...$$.",
            (Kind::Lecture, OutputFormat::Markdown) => "{{PREAMBLE}}\nTranscribe the lecture notes verbatim in Markdown. Preserve the original order, headings, lists, tables, and math, adding timestamps only when present in the source.",
            (Kind::Document, OutputFormat::Markdown) => "{{PREAMBLE}}\nTranscribe the document faithfully in Markdown. Reproduce headings, lists, tables, and math exactly as they appear without adding extra commentary or structure.",
            (Kind::Image, OutputFormat::Markdown) => "{{PREAMBLE}}\nTranscribe text from the image into Markdown. Keep source ordering, mark unreadable regions as [illegible], and use $...$ or $$...$$ for math.",
            (Kind::Video, OutputFormat::Markdown) => "{{PREAMBLE}}\nProduce a Markdown transcript with sections for 'Transcript', 'Timeline', and 'Key Terms'. Include [MM:SS] timestamps, summarize key visuals, and note gestures when relevant.",
            (Kind::Slides, OutputFormat::Latex) => "{{PREAMBLE}}\nTranscribe each slide faithfully in LaTeX. Use \\section*{} for slide titles, maintain bullet structure with itemize/enumerate, and preserve math environments.",
            (Kind::Lecture, OutputFormat::Latex) => "{{PREAMBLE}}\nTranscribe the lecture notes directly into LaTeX. Preserve source ordering, headings, lists, tables, and math, noting [sic] only when text is unclear.",
            (Kind::Document, OutputFormat::Latex) => "{{PREAMBLE}}\nTranscribe the document content verbatim into LaTeX, keeping the original structure, math environments, and tables exactly as given.",
            (Kind::Image, OutputFormat::Latex) => "{{PREAMBLE}}\nTranscribe the image content into LaTeX. Reproduce text in order, render math with LaTeX notation, and annotate unreadable pieces as [illegible].",
            (Kind::Video, OutputFormat::Latex) => "{{PREAMBLE}}\nProduce a LaTeX transcript with sections for Transcript, Timeline, and Key Terms. Include [MM:SS] timestamps, describe key visuals, and preserve important gestures or speaker notes.",
        }
    }
}

impl PromptStrategy for TemplatePromptStrategy {
    fn preamble(&self, format: OutputFormat) -> String {
        self.loader.preamble(self.kind, format)
    }

    fn instruction(&self, format: OutputFormat, preamble: &str) -> String {
        self.loader
            .prompt(self.kind, format, self.default_prompt(format))
            .replace("{{PREAMBLE}}", preamble)
    }
}

pub fn build_prompt_strategies(loader: &TemplateLoader) -> HashMap<Kind, TemplatePromptStrategy> {
    let kinds = [
        Kind::Slides,
        Kind::Lecture,
        Kind::Document,
        Kind::Image,
        Kind::Video,
    ];
    kinds
        .iter()
        .copied()
        .map(|kind| (kind, TemplatePromptStrategy::new(loader.clone(), kind)))
        .collect()
}
