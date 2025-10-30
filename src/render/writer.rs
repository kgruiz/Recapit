use anyhow::Context;
use std::{
    fs::{self, File},
    io::Write,
    path::{Path, PathBuf},
};

pub struct MarkdownWriter;

impl MarkdownWriter {
    pub fn new() -> Self {
        Self
    }
}

impl crate::core::Writer for MarkdownWriter {
    fn write_markdown(
        &self,
        base: &Path,
        name: &str,
        header: &str,
        body: &str,
    ) -> anyhow::Result<PathBuf> {
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
