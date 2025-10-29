use anyhow::{anyhow, bail, Context, Result};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

pub fn pdf_to_png(pdf: &Path, out_dir: &Path, prefix: Option<&str>) -> Result<Vec<PathBuf>> {
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

pub fn guess_pdf_kind(pdf_path: &Path) -> String {
    let name = pdf_path
        .file_stem()
        .map(|s| s.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    if ["slide", "deck", "presentation", "keynote", "pitch"]
        .iter()
        .any(|needle| name.contains(needle))
    {
        return "slides".into();
    }
    if ["lecture", "lesson", "class", "seminar", "notes"]
        .iter()
        .any(|needle| name.contains(needle))
    {
        return "lecture".into();
    }

    match first_page_report(pdf_path) {
        Ok(report) => {
            if report.aspect_ratio >= 1.3 || (report.page_count <= 5 && report.aspect_ratio >= 1.2)
            {
                "slides".into()
            } else if ["hw", "assignment", "worksheet", "problem"]
                .iter()
                .any(|needle| name.contains(needle))
            {
                "lecture".into()
            } else {
                "document".into()
            }
        }
        Err(_) => "document".into(),
    }
}

struct PageReport {
    aspect_ratio: f64,
    page_count: usize,
}

fn first_page_report(path: &Path) -> Result<PageReport> {
    let output = Command::new("pdfinfo")
        .arg(path)
        .output()
        .context("invoking pdfinfo")?;
    if !output.status.success() {
        bail!("pdfinfo failed for {}", path.display());
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let mut page_count = 0usize;
    let mut width = None;
    let mut height = None;
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("Pages:") {
            page_count = rest.trim().parse::<usize>().unwrap_or(0);
        }
        if let Some(rest) = line.strip_prefix("Page size:") {
            let parts = rest.split_whitespace().collect::<Vec<_>>();
            if parts.len() >= 2 {
                width = parts[0].parse::<f64>().ok();
                height = parts[1].parse::<f64>().ok();
            }
        }
    }
    let width = width.ok_or_else(|| anyhow!("pdfinfo missing page width"))?;
    let height = height.ok_or_else(|| anyhow!("pdfinfo missing page height"))?;
    Ok(PageReport {
        aspect_ratio: if height > 0.0 { width / height } else { 1.0 },
        page_count,
    })
}
