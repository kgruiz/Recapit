use anyhow::Context;
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};

#[allow(dead_code)]
pub fn pdf_to_png(
    pdf: &Path,
    out_dir: &Path,
    prefix: Option<&str>,
) -> anyhow::Result<Vec<PathBuf>> {
    if out_dir.exists() {
        fs::remove_dir_all(out_dir).ok();
    }
    fs::create_dir_all(out_dir)?;

    let pdftoppm = which::which("pdftoppm").context("pdftoppm not found; install poppler utils")?;
    let stem = prefix
        .map(|s| s.to_string())
        .or_else(|| {
            pdf.file_stem()
                .and_then(|s| s.to_str())
                .map(|s| s.to_string())
        })
        .unwrap_or_else(|| "page".to_string());
    let out_base = out_dir.join(stem);
    let status = Command::new(pdftoppm)
        .arg("-png")
        .arg(pdf)
        .arg(&out_base)
        .status()?;
    if !status.success() {
        anyhow::bail!("pdftoppm failed");
    }

    let mut pages = vec![];
    for entry in walkdir::WalkDir::new(out_dir).min_depth(1).max_depth(1) {
        let entry = entry?;
        if entry.file_type().is_file()
            && entry
                .path()
                .extension()
                .and_then(|s| s.to_str())
                .map(|ext| ext.eq_ignore_ascii_case("png"))
                .unwrap_or(false)
        {
            pages.push(entry.into_path());
        }
    }
    pages.sort();
    Ok(pages)
}
