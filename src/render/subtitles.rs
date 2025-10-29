use anyhow::Result;
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Default, Clone)]
pub struct SubtitleExporter;

impl SubtitleExporter {
    pub fn write(
        &self,
        fmt: &str,
        base: &Path,
        name: &str,
        text: &str,
        chunks: &[Value],
    ) -> Result<Option<PathBuf>> {
        let fmt = fmt.trim().to_lowercase();
        if fmt != "srt" && fmt != "vtt" {
            return Ok(None);
        }
        fs::create_dir_all(base)?;
        let target = base.join(format!("{name}.{fmt}"));
        let segments = split_text(text, chunks.len());
        let mut lines = Vec::new();
        if fmt == "vtt" {
            lines.push("WEBVTT".to_string());
            lines.push(String::new());
        }
        for (idx, chunk) in chunks.iter().enumerate() {
            let segment = segments.get(idx).cloned().unwrap_or_default();
            let start = chunk
                .get("start_seconds")
                .and_then(Value::as_f64)
                .unwrap_or((idx * 5) as f64);
            let end = chunk
                .get("end_seconds")
                .and_then(Value::as_f64)
                .unwrap_or(start + 5.0);
            if fmt == "srt" {
                lines.push((idx + 1).to_string());
                lines.push(format!(
                    "{} --> {}",
                    format_timestamp(start, Format::Srt),
                    format_timestamp(end, Format::Srt)
                ));
                lines.push(if segment.is_empty() {
                    "[No content]".to_string()
                } else {
                    segment
                });
                lines.push(String::new());
            } else {
                lines.push(format!(
                    "{} --> {}",
                    format_timestamp(start, Format::Vtt),
                    format_timestamp(end, Format::Vtt)
                ));
                lines.push(if segment.is_empty() {
                    "[No content]".to_string()
                } else {
                    segment
                });
                lines.push(String::new());
            }
        }
        if chunks.is_empty() {
            let start = 0.0;
            let end = 5.0;
            if fmt == "srt" {
                lines.push("1".into());
                lines.push(format!(
                    "{} --> {}",
                    format_timestamp(start, Format::Srt),
                    format_timestamp(end, Format::Srt)
                ));
                lines.push(text.trim().to_string());
            } else {
                lines.push(format!(
                    "{} --> {}",
                    format_timestamp(start, Format::Vtt),
                    format_timestamp(end, Format::Vtt)
                ));
                lines.push(text.trim().to_string());
            }
        }
        std::fs::write(&target, lines.join("\n"))?;
        Ok(Some(target))
    }
}

fn split_text(text: &str, parts: usize) -> Vec<String> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return vec![String::new(); parts.max(1)];
    }
    let paragraphs = trimmed
        .split("\n\n")
        .map(|p| p.trim())
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>();
    if parts <= 1 || paragraphs.is_empty() {
        return vec![paragraphs.join("\n\n")];
    }
    let mut segments = vec![String::new(); parts];
    for (idx, para) in paragraphs.iter().enumerate() {
        let slot = idx % parts;
        if !segments[slot].is_empty() {
            segments[slot].push_str("\n\n");
        }
        segments[slot].push_str(para);
    }
    segments
}

#[derive(Copy, Clone)]
enum Format {
    Srt,
    Vtt,
}

fn format_timestamp(seconds: f64, fmt: Format) -> String {
    let total_ms = (seconds.max(0.0) * 1000.0).round() as i64;
    let hours = total_ms / 3_600_000;
    let minutes = (total_ms % 3_600_000) / 60_000;
    let secs = (total_ms % 60_000) / 1000;
    let millis = total_ms % 1000;
    match fmt {
        Format::Srt => format!("{hours:02}:{minutes:02}:{secs:02},{millis:03}"),
        Format::Vtt => format!("{hours:02}:{minutes:02}:{secs:02}.{millis:03}"),
    }
}
