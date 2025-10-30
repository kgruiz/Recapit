use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use crate::core::{Kind, OutputFormat};

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

    pub fn preamble(&self, kind: Kind, format: OutputFormat) -> String {
        let (filename, default) = match (kind, format) {
            (Kind::Slides, OutputFormat::Markdown) => {
                ("slide-template.txt", MARKDOWN_PREAMBLES.slides)
            }
            (Kind::Slides, OutputFormat::Latex) => {
                ("slide-latex-template.txt", LATEX_PREAMBLES.slides)
            }
            (Kind::Lecture, OutputFormat::Markdown) => {
                ("lecture-template.txt", MARKDOWN_PREAMBLES.lecture)
            }
            (Kind::Lecture, OutputFormat::Latex) => {
                ("lecture-latex-template.txt", LATEX_PREAMBLES.lecture)
            }
            (Kind::Document, OutputFormat::Markdown) => {
                ("document-template.txt", MARKDOWN_PREAMBLES.document)
            }
            (Kind::Document, OutputFormat::Latex) => {
                ("document-latex-template.txt", LATEX_PREAMBLES.document)
            }
            (Kind::Image, OutputFormat::Markdown) => {
                ("image-template.txt", MARKDOWN_PREAMBLES.image)
            }
            (Kind::Image, OutputFormat::Latex) => {
                ("image-latex-template.txt", LATEX_PREAMBLES.image)
            }
            (Kind::Video, OutputFormat::Markdown) => {
                ("video-template.txt", MARKDOWN_PREAMBLES.video)
            }
            (Kind::Video, OutputFormat::Latex) => {
                ("video-latex-template.txt", LATEX_PREAMBLES.video)
            }
        };
        self.load_or_default(filename, default)
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

    pub fn prompt(&self, kind: Kind, format: OutputFormat, default: &str) -> String {
        let filename = match (kind, format) {
            (Kind::Slides, OutputFormat::Markdown) => "slide-prompt.txt",
            (Kind::Slides, OutputFormat::Latex) => "slide-prompt-latex.txt",
            (Kind::Lecture, OutputFormat::Markdown) => "lecture-prompt.txt",
            (Kind::Lecture, OutputFormat::Latex) => "lecture-prompt-latex.txt",
            (Kind::Document, OutputFormat::Markdown) => "document-prompt.txt",
            (Kind::Document, OutputFormat::Latex) => "document-prompt-latex.txt",
            (Kind::Image, OutputFormat::Markdown) => "image-prompt.txt",
            (Kind::Image, OutputFormat::Latex) => "image-prompt-latex.txt",
            (Kind::Video, OutputFormat::Markdown) => "video-prompt.txt",
            (Kind::Video, OutputFormat::Latex) => "video-prompt-latex.txt",
        };
        self.load_or_default(filename, default)
    }
}

fn read_file(path: PathBuf) -> Option<String> {
    match fs::read_to_string(path) {
        Ok(text) => Some(text),
        Err(_) => None,
    }
}

struct FormatPreambles {
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

const SLIDES_PREAMBLE_MARKDOWN: &str = "";

const LECTURE_PREAMBLE_MARKDOWN: &str = "";

const DOCUMENT_PREAMBLE_MARKDOWN: &str = "";

const IMAGE_PREAMBLE_MARKDOWN: &str = "";

const VIDEO_PREAMBLE_MARKDOWN: &str = "";

const SLIDES_PREAMBLE_LATEX: &str = r"\documentclass[aspectratio=43]{beamer}

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
";

const LECTURE_PREAMBLE_LATEX: &str = r"\documentclass{article}

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
";

const DOCUMENT_PREAMBLE_LATEX: &str = r"\documentclass{article}

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
";

const IMAGE_PREAMBLE_LATEX: &str = r"\documentclass{article}

\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{amsfonts}
\usepackage{amsthm}
\usepackage{geometry}
\geometry{margin=1in}

\begin{document}
";

const VIDEO_PREAMBLE_LATEX: &str = r"\documentclass{article}

\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{amsfonts}
\usepackage{amsthm}
\usepackage{xcolor}
\usepackage{enumitem}
\usepackage{geometry}
\geometry{margin=1in}

\begin{document}
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

static MARKDOWN_PREAMBLES: FormatPreambles = FormatPreambles {
    slides: SLIDES_PREAMBLE_MARKDOWN,
    lecture: LECTURE_PREAMBLE_MARKDOWN,
    document: DOCUMENT_PREAMBLE_MARKDOWN,
    image: IMAGE_PREAMBLE_MARKDOWN,
    video: VIDEO_PREAMBLE_MARKDOWN,
};

static LATEX_PREAMBLES: FormatPreambles = FormatPreambles {
    slides: SLIDES_PREAMBLE_LATEX,
    lecture: LECTURE_PREAMBLE_LATEX,
    document: DOCUMENT_PREAMBLE_LATEX,
    image: IMAGE_PREAMBLE_LATEX,
    video: VIDEO_PREAMBLE_LATEX,
};

static DEFAULT_CONVERSIONS: DefaultConversions = DefaultConversions {
    latex_to_md: LATEX_TO_MD_PROMPT,
    latex_to_json: LATEX_TO_JSON_PROMPT,
    markdown_to_json: MARKDOWN_TO_JSON_PROMPT,
};
