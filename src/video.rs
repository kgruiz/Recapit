use anyhow::Context;
use serde::{Deserialize, Serialize};
use std::{
    path::{Path, PathBuf},
    process::Command,
};

#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct VideoMeta {
    pub path: PathBuf,
    pub duration_seconds: f64,
    pub size_bytes: u64,
    pub fps: Option<f64>,
    pub width: Option<u32>,
    pub height: Option<u32>,
    pub video_codec: Option<String>,
    pub audio_codec: Option<String>,
}

#[allow(dead_code)]
pub fn ffprobe(path: &Path) -> anyhow::Result<VideoMeta> {
    let ffprobe = which::which("ffprobe").context("ffprobe not found")?;
    let output = Command::new(ffprobe)
        .args([
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
        ])
        .arg(path)
        .output()?;
    if !output.status.success() {
        anyhow::bail!("ffprobe failed");
    }
    let value: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    let format = value.get("format").cloned().unwrap_or_default();
    let streams = value.get("streams").cloned().unwrap_or_default();

    let duration = format
        .get("duration")
        .and_then(|x| x.as_str())
        .and_then(|s| s.parse().ok())
        .unwrap_or(0.0);
    let size_bytes = format
        .get("size")
        .and_then(|x| x.as_str())
        .and_then(|s| s.parse().ok())
        .unwrap_or(0u64);

    let mut meta = VideoMeta {
        path: path.to_path_buf(),
        duration_seconds: duration,
        size_bytes,
        fps: None,
        width: None,
        height: None,
        video_codec: None,
        audio_codec: None,
    };

    if let Some(arr) = streams.as_array() {
        for stream in arr {
            match stream.get("codec_type").and_then(|x| x.as_str()) {
                Some("video") => {
                    meta.video_codec = stream
                        .get("codec_name")
                        .and_then(|x| x.as_str())
                        .map(|s| s.to_string());
                    meta.width = stream
                        .get("width")
                        .and_then(|x| x.as_u64())
                        .map(|x| x as u32);
                    meta.height = stream
                        .get("height")
                        .and_then(|x| x.as_u64())
                        .map(|x| x as u32);
                    let rate = stream
                        .get("avg_frame_rate")
                        .and_then(|x| x.as_str())
                        .unwrap_or("0/0");
                    meta.fps = parse_rate(rate);
                }
                Some("audio") => {
                    meta.audio_codec = stream
                        .get("codec_name")
                        .and_then(|x| x.as_str())
                        .map(|s| s.to_string());
                }
                _ => {}
            }
        }
    }

    Ok(meta)
}

#[allow(dead_code)]
fn parse_rate(rate: &str) -> Option<f64> {
    if let Some((numerator, denominator)) = rate.split_once('/') {
        let numerator: f64 = numerator.parse().ok()?;
        let denominator: f64 = denominator.parse().ok()?;
        if denominator > 0.0 {
            return Some(numerator / denominator);
        }
        return None;
    }
    rate.parse().ok()
}

#[allow(dead_code)]
pub fn ffmpeg_normalize(source: &Path, out_dir: &Path) -> anyhow::Result<PathBuf> {
    std::fs::create_dir_all(out_dir)?;
    let stem = source
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("normalized");
    let dst = out_dir.join(format!("{stem}-normalized.mp4"));
    let ffmpeg = which::which("ffmpeg").context("ffmpeg not found")?;
    let status = Command::new(ffmpeg)
        .args([
            "-y",
            "-i",
            source.to_str().unwrap(),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            dst.to_str().unwrap(),
        ])
        .status()?;
    if !status.success() {
        anyhow::bail!("ffmpeg failed");
    }
    Ok(dst)
}

#[allow(dead_code)]
pub fn plan_chunks(
    meta: &VideoMeta,
    max_seconds: f64,
    max_bytes: u64,
    tokens_per_second: f64,
    token_limit: Option<u64>,
) -> Vec<(f64, f64)> {
    let duration = meta.duration_seconds.max(0.0);
    if duration == 0.0 {
        return vec![(0.0, 0.0)];
    }

    let bytes_per_second = if duration > 0.0 {
        meta.size_bytes as f64 / duration
    } else {
        meta.size_bytes as f64
    };

    let mut effective = max_seconds;
    if max_bytes > 0 && bytes_per_second > 0.0 {
        effective = effective.min(max_bytes as f64 / bytes_per_second);
    }
    if let Some(limit) = token_limit {
        let seconds_by_tokens = (limit as f64 / tokens_per_second).max(1.0);
        effective = effective.min(seconds_by_tokens);
    }

    effective = effective.max(1.0);

    let mut out = Vec::new();
    let mut start = 0.0;
    while start < duration {
        let end = (start + effective).min(duration);
        out.push((start, end));
        start = end;
    }

    if let Some(last) = out.last_mut() {
        last.1 = duration;
    }

    out
}
