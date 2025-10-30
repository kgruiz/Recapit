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
            (Kind::Slides, OutputFormat::Markdown) => "{{PREAMBLE}}\nSummarize slide content using GitHub-flavored Markdown. Preserve slide order and hierarchy. Render equations with inline ($...$) or block ($$...$$) math fences.",
            (Kind::Lecture, OutputFormat::Markdown) => "{{PREAMBLE}}\nProduce a lecture summary with [MM:SS] timestamps. Capture key arguments, definitions, and examples using GitHub-flavored Markdown. Render mathematics with $...$ or $$...$$.",
            (Kind::Document, OutputFormat::Markdown) => "{{PREAMBLE}}\nSummarize the document in GitHub-flavored Markdown. Preserve headings and highlight key conclusions. Render equations with Markdown math fences.",
            (Kind::Image, OutputFormat::Markdown) => "{{PREAMBLE}}\nDescribe the image with technical precision in GitHub-flavored Markdown. Capture any embedded text (render math using $...$/$$...$$) and note significant visual details.",
            (Kind::Video, OutputFormat::Markdown) => "{{PREAMBLE}}\nTask: Produce a transcript with [MM:SS] timestamps and a timeline of salient visual events.\nInclude: visual descriptions, slide titles, equations rendered with Markdown math fences, and noteworthy gestures or annotations.\nOutput: GitHub-flavored Markdown with headings 'Transcript', 'Timeline', and 'Key Terms'.",
            (Kind::Slides, OutputFormat::Latex) => "{{PREAMBLE}}\nSummarize slide content. Preserve slide order and hierarchy. Output LaTeX with appropriate sectioning. Render mathematics as LaTeX environments or inline math.",
            (Kind::Lecture, OutputFormat::Latex) => "{{PREAMBLE}}\nProduce a lecture summary with [MM:SS] timestamps. Capture key arguments, definitions, and examples. Render mathematics as LaTeX (use align/gather/equation when helpful).",
            (Kind::Document, OutputFormat::Latex) => "{{PREAMBLE}}\nSummarize the document. Preserve headings and highlight key conclusions. Render all mathematics as LaTeX.",
            (Kind::Image, OutputFormat::Latex) => "{{PREAMBLE}}\nDescribe the image with technical precision. Capture any embedded text (convert math to LaTeX) and notable visual details. Output LaTeX.",
            (Kind::Video, OutputFormat::Latex) => "{{PREAMBLE}}\nTask: Produce a transcript with [MM:SS] timestamps and a timeline of salient visual events.\nInclude: visual descriptions, slide titles, equations in LaTeX, and noteworthy gestures or annotations.\nOutput: LaTeX with sections for 'Transcript', 'Timeline', and 'Key Terms'.",
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
