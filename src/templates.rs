use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use crate::core::Kind;

#[derive(Debug, Clone)]
pub struct TemplateLoader {
    base: Arc<PathBuf>,
    cache: Arc<Mutex<HashMap<String, String>>>,
}

impl TemplateLoader {
    pub fn new(base: impl Into<PathBuf>) -> Self {
        Self {
            base: Arc::new(base.into()),
            cache: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    fn load_cached(
        &self,
        key: &str,
        loader: impl FnOnce(&Path) -> Option<String>,
    ) -> Option<String> {
        if let Some(value) = self.cache.lock().unwrap().get(key).cloned() {
            return Some(value);
        }
        let result = loader(&self.base);
        if let Some(ref text) = result {
            self.cache
                .lock()
                .unwrap()
                .insert(key.to_string(), text.clone());
        }
        result
    }

    fn load_or_default(&self, filename: &str, default: &str) -> String {
        let key = format!("template::{filename}");
        if let Some(value) = self.load_cached(&key, |base| read_file(base.join(filename))) {
            return value;
        }
        default.to_string()
    }

    pub fn slide_preamble(&self) -> String {
        self.load_or_default("slide-template.txt", DEFAULT_PREAMBLES.slides)
    }

    pub fn lecture_preamble(&self) -> String {
        self.load_or_default("lecture-template.txt", DEFAULT_PREAMBLES.lecture)
    }

    pub fn document_preamble(&self) -> String {
        self.load_or_default("document-template.txt", DEFAULT_PREAMBLES.document)
    }

    pub fn image_preamble(&self) -> String {
        self.load_or_default("image-template.txt", DEFAULT_PREAMBLES.image)
    }

    pub fn video_preamble(&self) -> String {
        self.load_or_default("video-template.txt", DEFAULT_PREAMBLES.video)
    }

    pub fn latex_to_md_prompt(&self) -> String {
        self.load_or_default("latex-to-md-template.txt", DEFAULT_CONVERSIONS.latex_to_md)
    }

    pub fn latex_to_json_prompt(&self) -> String {
        self.load_or_default(
            "latex-to-json-template.txt",
            DEFAULT_CONVERSIONS.latex_to_json,
        )
    }

    pub fn markdown_to_json_prompt(&self) -> String {
        self.load_or_default(
            "markdown-to-json-template.txt",
            DEFAULT_CONVERSIONS.markdown_to_json,
        )
    }

    pub fn prompt(&self, kind: Kind, default: &str) -> String {
        let filename = format!("{}-prompt.txt", kind.as_str());
        self.load_or_default(&filename, default)
    }
}

fn read_file(path: PathBuf) -> Option<String> {
    match fs::read_to_string(path) {
        Ok(text) => Some(text),
        Err(_) => None,
    }
}

struct DefaultPreambles {
    slides: &'static str,
    lecture: &'static str,
    document: &'static str,
    image: &'static str,
    video: &'static str,
}

struct DefaultConversions {
    latex_to_md: &'static str,
    latex_to_json: &'static str,
    markdown_to_json: &'static str,
}

const SLIDES_PREAMBLE: &str = r"# Slide Deck Summary

";

const LECTURE_PREAMBLE: &str = r"# Lecture Summary

";

const DOCUMENT_PREAMBLE: &str = r"# Document Summary

";

const IMAGE_PREAMBLE: &str = r"# Image Analysis

";

const VIDEO_PREAMBLE: &str = r"# Video Summary

";

const LATEX_TO_MD_PROMPT: &str = r"Convert the LaTeX source into Markdown while preserving structure.
- Keep headings mapping section -> #, subsection -> ##.
- Preserve math using $...$ or $$...$$.
- Use bullet/numbered lists for itemize/enumerate.
- Render tables as GitHub-flavored Markdown tables where possible.
- Replace images or TikZ drawings with `[Placeholder: description]`.
- Remove LaTeX-only preamble commands.

Return only the Markdown.
";

const LATEX_TO_JSON_PROMPT: &str = r"Convert the LaTeX table or structured content into well-formed JSON.
- Use the first row as headers when available.
- Preserve numeric types where obvious, otherwise use strings.
- Output a JSON array of objects.
- Do not include explanations.
";

const MARKDOWN_TO_JSON_PROMPT: &str = r"Convert the Markdown tables or structured lists into well-formed JSON.
- Use the first row of each table as headers when available.
- Preserve numeric types where obvious, otherwise use strings.
- Output a JSON array of objects.
- Ignore narrative sections that do not map cleanly to data rows.
- Do not include explanations.
";

static DEFAULT_PREAMBLES: DefaultPreambles = DefaultPreambles {
    slides: SLIDES_PREAMBLE,
    lecture: LECTURE_PREAMBLE,
    document: DOCUMENT_PREAMBLE,
    image: IMAGE_PREAMBLE,
    video: VIDEO_PREAMBLE,
};

static DEFAULT_CONVERSIONS: DefaultConversions = DefaultConversions {
    latex_to_md: LATEX_TO_MD_PROMPT,
    latex_to_json: LATEX_TO_JSON_PROMPT,
    markdown_to_json: MARKDOWN_TO_JSON_PROMPT,
};
