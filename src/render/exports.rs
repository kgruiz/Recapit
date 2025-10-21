use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde_json::{json, Value};

pub fn write_markdown(base: &Path, name: &str, preamble: &str, text: &str) -> Result<PathBuf> {
    let path = base.join(format!("{name}.md"));
    let mut content = String::new();
    if !preamble.trim().is_empty() {
        content.push_str(preamble.trim());
        content.push_str("\n\n");
    }
    content.push_str(text.trim());
    content.push('\n');
    fs::write(&path, content).with_context(|| format!("writing {}", path.display()))?;
    Ok(path)
}

pub fn write_plaintext(base: &Path, name: &str, preamble: &str, text: &str) -> Result<PathBuf> {
    let path = base.join(format!("{name}.txt"));
    let mut content = String::new();
    if !preamble.trim().is_empty() {
        content.push_str(preamble.trim());
        content.push_str("\n\n");
    }
    content.push_str(text.trim());
    content.push('\n');
    fs::write(&path, content).with_context(|| format!("writing {}", path.display()))?;
    Ok(path)
}

pub fn write_summary_json(
    base: &Path,
    name: &str,
    preamble: &str,
    text: &str,
    chunks: &[Value],
) -> Result<PathBuf> {
    let path = base.join(format!("{name}.json"));
    let payload = json!({
        "preamble": preamble,
        "text": text,
        "chunks": chunks,
    });
    let serialized =
        serde_json::to_string_pretty(&payload).context("serializing summary export")?;
    fs::write(&path, serialized).with_context(|| format!("writing {}", path.display()))?;
    Ok(path)
}
