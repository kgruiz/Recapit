use anyhow::{anyhow, bail, Context, Result};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::selection::IndexSelection;

#[derive(Debug, Clone)]
pub struct PdfPage {
    pub path: PathBuf,
    pub page_number: u32,
}

pub fn pdf_to_png(
    pdf: &Path,
    out_dir: &Path,
    prefix: Option<&str>,
    dpi: u32,
    selection: Option<&IndexSelection>,
) -> Result<Vec<PdfPage>> {
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

    let ranges = if let Some(selection) = selection {
        let total_pages = page_count(pdf)? as u32;
        Some(selection.merged_ranges(total_pages)?)
    } else {
        None
    };

    if let Some(ranges) = ranges {
        for (start, end) in ranges {
            let status = Command::new(&pdftoppm)
                .arg("-png")
                .arg("-r")
                .arg(dpi.to_string())
                .arg("-f")
                .arg(start.to_string())
                .arg("-l")
                .arg(end.to_string())
                .arg(pdf)
                .arg(&output)
                .status()?;
            if !status.success() {
                bail!(
                    "pdftoppm failed for {} (pages {start}-{end})",
                    pdf.display()
                );
            }
        }
    } else {
        let status = Command::new(&pdftoppm)
            .arg("-png")
            .arg("-r")
            .arg(dpi.to_string())
            .arg(pdf)
            .arg(&output)
            .status()?;
        if !status.success() {
            bail!("pdftoppm failed for {}", pdf.display());
        }
    }

    let mut pages: Vec<PdfPage> = Vec::new();
    for entry in walkdir::WalkDir::new(out_dir).min_depth(1).max_depth(1) {
        let entry = entry?;
        if entry.path().extension().and_then(|s| s.to_str()) == Some("png") {
            let path = entry.into_path();
            let page_number = parse_pdftoppm_page_number(&path).ok_or_else(|| {
                anyhow!("unable to infer PDF page number from {}", path.display())
            })?;
            pages.push(PdfPage { path, page_number });
        }
    }
    pages.sort_by_key(|page| page.page_number);
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

fn parse_pdftoppm_page_number(path: &Path) -> Option<u32> {
    let stem = path.file_stem()?.to_string_lossy();
    let (_, suffix) = stem.rsplit_once('-')?;
    suffix.parse::<u32>().ok()
}
