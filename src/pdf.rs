use anyhow::{anyhow, bail, Context, Result};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

pub fn pdf_to_png(
    pdf: &Path,
    out_dir: &Path,
    prefix: Option<&str>,
    dpi: u32,
) -> Result<Vec<PathBuf>> {
    if out_dir.exists() {
        fs::remove_dir_all(out_dir)?;
    }

    fs::create_dir_all(out_dir)?;

    let pdftoppm = which::which("pdftoppm")
        .map_err(|_| anyhow!("pdftoppm not found; install poppler-utils"))?;
    let stem = prefix
        .map(|s| s.to_string())
        .or_else(|| pdf.file_stem().map(|s| s.to_string_lossy().to_string()))
        .unwrap_or_else(|| "page".into());
    let output = out_dir.join(stem);

    let status = Command::new(pdftoppm)
        .arg("-png")
        .arg("-r")
        .arg(dpi.to_string())
        .arg(pdf)
        .arg(&output)
        .status()?;
    if !status.success() {
        bail!("pdftoppm failed for {}", pdf.display());
    }

    let mut pages = Vec::new();
    for entry in walkdir::WalkDir::new(out_dir).min_depth(1).max_depth(1) {
        let entry = entry?;
        if entry.path().extension().and_then(|s| s.to_str()) == Some("png") {
            pages.push(entry.into_path());
        }
    }
    pages.sort();
    if pages.is_empty() {
        bail!("No PNG pages rendered for {}", pdf.display());
    }
    Ok(pages)
}

pub fn page_count(path: &Path) -> Result<usize> {
    let output = Command::new("pdfinfo")
        .arg(path)
        .output()
        .context("invoking pdfinfo")?;
    if !output.status.success() {
        bail!("pdfinfo failed for {}", path.display());
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let mut page_count = None;
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("Pages:") {
            page_count = rest.trim().parse::<usize>().ok();
            break;
        }
    }
    page_count.ok_or_else(|| anyhow!("pdfinfo missing page count"))
}
