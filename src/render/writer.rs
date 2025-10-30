use crate::core::OutputFormat;
use anyhow::Context;
use std::{
    fs::{self, File},
    io::Write,
    path::{Path, PathBuf},
};

pub struct CompositeWriter {
    markdown: MarkdownWriter,
    latex: LatexWriter,
}

impl CompositeWriter {
    pub fn new() -> Self {
        Self {
            markdown: MarkdownWriter,
            latex: LatexWriter,
        }
    }
}

impl crate::core::Writer for CompositeWriter {
    fn write(
        &self,
        format: OutputFormat,
        base: &Path,
        name: &str,
        preamble: &str,
        body: &str,
    ) -> anyhow::Result<PathBuf> {
        match format {
            OutputFormat::Markdown => self.markdown.write(base, name, preamble, body),
            OutputFormat::Latex => self.latex.write(base, name, preamble, body),
        }
    }
}

struct MarkdownWriter;

impl MarkdownWriter {
    fn write(&self, base: &Path, name: &str, header: &str, body: &str) -> anyhow::Result<PathBuf> {
        fs::create_dir_all(base)?;
        let dir = base.join(name);
        fs::create_dir_all(&dir)?;
        let path = dir.join(format!("{name}.md"));

        let mut content = String::new();
        if !header.is_empty() {
            content.push_str(header);
            if !header.ends_with("\n\n") {
                if header.ends_with('\n') {
                    content.push('\n');
                } else {
                    content.push_str("\n\n");
                }
            }
        }
        content.push_str(body.trim_end());
        content.push('\n');

        let mut file =
            File::create(&path).with_context(|| format!("creating {}", path.display()))?;
        file.write_all(content.as_bytes())?;
        Ok(path)
    }
}

struct LatexWriter;

impl LatexWriter {
    fn write(
        &self,
        base: &Path,
        name: &str,
        preamble: &str,
        body: &str,
    ) -> anyhow::Result<PathBuf> {
        fs::create_dir_all(base)?;
        let dir = base.join(name);
        fs::create_dir_all(&dir)?;
        let path = dir.join(format!("{name}.tex"));

        let mut content = String::new();
        content.push_str(preamble);
        if !preamble.ends_with('\n') {
            content.push('\n');
        }
        content.push_str(body);
        if !body.contains("\\end{document}") {
            content.push_str("\n\\end{document}\n");
        }

        let mut file =
            File::create(&path).with_context(|| format!("creating {}", path.display()))?;
        file.write_all(content.as_bytes())?;
        Ok(path)
    }
}
