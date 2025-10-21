use anyhow::Result;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::env;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::str;
use url::Url;
use which::which;

use crate::core::{Asset, Job, SourceKind};
use crate::utils::ensure_dir;
use crate::video::sha256sum;
use thiserror::Error;

#[derive(Debug, Error)]
enum YouTubeDownloadError {
    #[error(
        "YouTube downloads require yt-dlp. Install it (e.g. `pip install yt-dlp` or `brew install yt-dlp`)."
    )]
    MissingYtDlp,
    #[error(
        "YouTube downloads require FFmpeg. Install `ffmpeg` so yt-dlp can merge audio/video streams."
    )]
    MissingFfmpeg,
    #[error("{0}")]
    Other(String),
}

pub struct YouTubeIngestor {
    hosts: HashSet<&'static str>,
    cache_dir: PathBuf,
}

impl Default for YouTubeIngestor {
    fn default() -> Self {
        let base_cache = dirs::cache_dir()
            .unwrap_or_else(|| env::temp_dir())
            .join("recapit")
            .join("youtube");
        ensure_dir(&base_cache).expect("failed to create youtube cache directory");
        Self {
            hosts: HashSet::from([
                "youtu.be",
                "youtube.com",
                "www.youtube.com",
                "m.youtube.com",
            ]),
            cache_dir: base_cache,
        }
    }
}

impl YouTubeIngestor {
    pub fn supports(&self, url: &Url) -> bool {
        if matches!(url.scheme(), "yt" | "youtube") {
            return true;
        }
        self.hosts.contains(url.host_str().unwrap_or_default())
    }

    pub fn discover(&self, job: &Job) -> Result<Vec<Asset>> {
        let parsed = match Url::parse(&job.source) {
            Ok(url) => url,
            Err(_) => Url::parse(&format!("https://{}", job.source))?,
        };
        if !self.supports(&parsed) {
            return Ok(vec![]);
        }
        let url = parsed.to_string();
        match download_with_ytdlp(&url, &self.cache_dir) {
            Ok(download) => {
                let download_path = download.path.clone();
                let meta = json!({
                    "source_url": url,
                    "downloaded": true,
                    "youtube_id": download.video_id,
                    "duration_seconds": download.duration,
                    "size_bytes": download.size_bytes,
                    "size_hash": download.size_hash,
                    "title": download.title,
                    "cache_path": download_path,
                    "pass_through": false,
                });
                Ok(vec![Asset {
                    path: download_path,
                    media: "video".into(),
                    page_index: None,
                    source_kind: SourceKind::Youtube,
                    mime: Some(download.mime),
                    meta,
                }])
            }
            Err(err) => {
                tracing::warn!("{err}");
                let meta = json!({
                    "source_url": url,
                    "downloaded": false,
                    "pass_through": true,
                    "warning": err.to_string(),
                });
                Ok(vec![Asset {
                    path: PathBuf::from(url.clone()),
                    media: "video".into(),
                    page_index: None,
                    source_kind: SourceKind::Youtube,
                    mime: Some("video/*".into()),
                    meta,
                }])
            }
        }
    }
}

struct DownloadedVideo {
    path: PathBuf,
    video_id: String,
    duration: Option<f64>,
    size_bytes: Option<u64>,
    title: Option<String>,
    mime: String,
    size_hash: Option<String>,
}

fn download_with_ytdlp(
    url: &str,
    cache_dir: &Path,
) -> Result<DownloadedVideo, YouTubeDownloadError> {
    ensure_dir(cache_dir).map_err(|err| {
        YouTubeDownloadError::Other(format!(
            "failed to prepare YouTube cache directory {}: {err}",
            cache_dir.display()
        ))
    })?;

    let ytdlp_path = which("yt-dlp").map_err(|_| YouTubeDownloadError::MissingYtDlp)?;
    let ffmpeg_path = which("ffmpeg").map_err(|_| YouTubeDownloadError::MissingFfmpeg)?;

    let metadata_output = Command::new(&ytdlp_path)
        .arg("--dump-json")
        .arg("--skip-download")
        .arg("--no-warnings")
        .arg("--no-progress")
        .arg(url)
        .output()
        .map_err(|err| YouTubeDownloadError::Other(format!("failed to execute yt-dlp: {err}")))?;

    if !metadata_output.status.success() {
        let stderr = String::from_utf8_lossy(&metadata_output.stderr);
        return Err(YouTubeDownloadError::Other(format!(
            "yt-dlp metadata probe failed (status {}): {}",
            metadata_output.status,
            stderr.trim()
        )));
    }

    let metadata: Value = serde_json::from_slice(&metadata_output.stdout).map_err(|err| {
        YouTubeDownloadError::Other(format!("unable to parse yt-dlp metadata JSON: {err}"))
    })?;

    let video_id = metadata
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| YouTubeDownloadError::Other("yt-dlp metadata missing video id".to_string()))?
        .to_string();
    let ext = metadata.get("ext").and_then(Value::as_str).unwrap_or("mp4");
    let title = metadata
        .get("title")
        .and_then(Value::as_str)
        .map(|s| s.to_string());
    let duration = metadata
        .get("duration")
        .and_then(Value::as_f64)
        .or_else(|| {
            metadata
                .get("duration")
                .and_then(Value::as_i64)
                .map(|v| v as f64)
        });

    let output_template = cache_dir.join(format!("{video_id}.%(ext)s"));

    if !cache_dir.join(format!("{video_id}.{ext}")).exists() {
        let download_status = Command::new(&ytdlp_path)
            .arg("-o")
            .arg(output_template.to_string_lossy().to_string())
            .arg("--merge-output-format")
            .arg("mp4")
            .arg("--ffmpeg-location")
            .arg(ffmpeg_path.to_string_lossy().to_string())
            .arg("--no-warnings")
            .arg("--no-progress")
            .arg("--quiet")
            .arg(url)
            .status()
            .map_err(|err| {
                YouTubeDownloadError::Other(format!("failed to execute yt-dlp download: {err}"))
            })?;

        if !download_status.success() {
            return Err(YouTubeDownloadError::Other(format!(
                "yt-dlp download failed with status {}",
                download_status
            )));
        }
    }

    let final_path = cache_dir.join(format!("{video_id}.mp4"));
    let mut actual_path = final_path.clone();
    if !actual_path.exists() {
        // fallback to the reported extension
        actual_path = cache_dir.join(format!("{video_id}.{ext}"));
    }

    if !actual_path.exists() {
        return Err(YouTubeDownloadError::Other(format!(
            "yt-dlp reported success but no output file found for video {video_id}"
        )));
    }

    let size_bytes = actual_path.metadata().ok().map(|meta| meta.len());
    let size_hash = match sha256sum(&actual_path) {
        Ok(hash) => Some(format!("sha256:{hash}")),
        Err(err) => {
            tracing::warn!(
                "failed to hash downloaded video {}: {err}",
                actual_path.display()
            );
            None
        }
    };
    Ok(DownloadedVideo {
        path: actual_path,
        video_id,
        duration,
        size_bytes,
        title,
        mime: format!("video/{ext}"),
        size_hash,
    })
}
