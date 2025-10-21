use anyhow::Context;
use std::{
    fs::{self, File},
    io::Write,
    path::{Path, PathBuf},
};

pub struct LatexWriter;

impl LatexWriter {
    pub fn new() -> Self {
        Self
    }
}

impl crate::core::Writer for LatexWriter {
    fn write_latex(
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
