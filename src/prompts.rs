use std::collections::HashMap;

use crate::core::{Kind, PromptStrategy};
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

    fn default_prompt(&self) -> &'static str {
        match self.kind {
            Kind::Slides => "{{PREAMBLE}}\nSummarize slide content. Preserve slide order and hierarchy. Keep math as LaTeX.",
            Kind::Lecture => "{{PREAMBLE}}\nProduce a lecture summary with [MM:SS] timestamps. Capture key arguments, definitions, and examples. Keep math as LaTeX.",
            Kind::Document => "{{PREAMBLE}}\nSummarize the document. Preserve headings and highlight key conclusions. Keep math as LaTeX.",
            Kind::Image => "{{PREAMBLE}}\nDescribe the image with technical precision. Capture any text (convert math to LaTeX) and notable visual details.",
            Kind::Video => "{{PREAMBLE}}\nTask: Produce a transcript with [MM:SS] timestamps and a timeline of salient visual events.\nInclude: visual descriptions, slide titles, equations in LaTeX, and noteworthy gestures or annotations.\nOutput: Markdown with headings 'Transcript', 'Timeline', and 'Key Terms'.",
        }
    }
}

impl PromptStrategy for TemplatePromptStrategy {
    fn preamble(&self) -> String {
        match self.kind {
            Kind::Slides => self.loader.slide_preamble(),
            Kind::Lecture => self.loader.lecture_preamble(),
            Kind::Image => self.loader.image_preamble(),
            Kind::Video => self.loader.video_preamble(),
            Kind::Document => self.loader.document_preamble(),
        }
    }

    fn instruction(&self, preamble: &str) -> String {
        self.loader
            .prompt(self.kind, self.default_prompt())
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
