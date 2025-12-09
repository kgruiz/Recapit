use anyhow::{Context, Result};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::env;
use std::path::{Path, PathBuf};
use std::process::Command;
use thiserror::Error;
use url::Url;
use which::which;

use crate::core::{Asset, Job, SourceKind};
use crate::utils::ensure_dir;
use crate::video::sha256sum;

const YOUTUBE_HOSTS: [&str; 4] = [
    "youtu.be",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
];

pub struct YouTubeIngestor {
    hosts: HashSet<&'static str>,
}

impl Default for YouTubeIngestor {
    fn default() -> Self {
        Self {
            hosts: HashSet::from(YOUTUBE_HOSTS),
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
        let parsed = parse_url(&job.source)?;
        if !self.supports(&parsed) {
            return Ok(vec![]);
        }
        let url = parsed.to_string();
        let meta = json!({
            "source_url": url,
            "pass_through": false,
            "downloaded": false,
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

#[derive(Debug, Clone)]
pub struct YouTubeDownloader {
    cache_dir: PathBuf,
}

#[derive(Debug, Clone)]
pub struct YouTubeDownload {
    pub path: PathBuf,
    pub metadata: Value,
    pub mime: String,
    pub cached: bool,
    pub sha256: Option<String>,
    pub size_bytes: Option<u64>,
}

#[derive(Debug, Error)]
pub enum YouTubeDownloadError {
    #[error("yt-dlp executable not found. Install it (e.g. `brew install yt-dlp`) to download YouTube sources.")]
    MissingYtDlp,
    #[error("ffmpeg executable not found. Install it (e.g. `brew install ffmpeg`) so yt-dlp can merge audio/video streams.")]
    MissingFfmpeg,
    #[error("yt-dlp metadata probe failed: {0}")]
    Metadata(String),
    #[error("yt-dlp download failed: {0}")]
    Download(String),
    #[error("{0}")]
    Other(String),
}

impl YouTubeDownloader {
    pub fn new(cache_dir: Option<PathBuf>) -> Result<Self> {
        let base = cache_dir.unwrap_or_else(|| {
            dirs::cache_dir()
                .unwrap_or_else(env::temp_dir)
                .join("recapit")
                .join("youtube")
        });
        ensure_dir(&base)?;
        Ok(Self { cache_dir: base })
    }

    pub fn download(
        &self,
        url: &str,
        target_dir: Option<&Path>,
    ) -> std::result::Result<YouTubeDownload, YouTubeDownloadError> {
        let ytdlp = which("yt-dlp").map_err(|_| YouTubeDownloadError::MissingYtDlp)?;
        let ffmpeg = which("ffmpeg").map_err(|_| YouTubeDownloadError::MissingFfmpeg)?;

        let base_dir = target_dir
            .map(PathBuf::from)
            .unwrap_or_else(|| self.cache_dir.clone());
        ensure_dir(&base_dir).map_err(|err| YouTubeDownloadError::Other(err.to_string()))?;

        let metadata_output = Command::new(&ytdlp)
            .arg("--dump-json")
            .arg("--skip-download")
            .arg("--no-warnings")
            .arg("--no-progress")
            .arg(url)
            .output()
            .map_err(|err| {
                YouTubeDownloadError::Other(format!("failed to execute yt-dlp: {err}"))
            })?;

        if !metadata_output.status.success() {
            let stderr = String::from_utf8_lossy(&metadata_output.stderr);
            return Err(YouTubeDownloadError::Metadata(stderr.trim().to_string()));
        }

        let metadata: Value = serde_json::from_slice(&metadata_output.stdout).map_err(|err| {
            YouTubeDownloadError::Other(format!("unable to parse yt-dlp metadata JSON: {err}"))
        })?;

        let video_id = metadata
            .get("id")
            .and_then(|value| value.as_str())
            .map(|value| value.to_string())
            .ok_or_else(|| {
                YouTubeDownloadError::Metadata("yt-dlp metadata missing video id".into())
            })?;
        let ext = metadata
            .get("ext")
            .and_then(|value| value.as_str())
            .unwrap_or("mp4");

        let expected_mp4 = base_dir.join(format!("{video_id}.mp4"));
        let expected_ext = base_dir.join(format!("{video_id}.{ext}"));

        let (path, cached) = if expected_mp4.exists() {
            (expected_mp4.clone(), true)
        } else if expected_ext.exists() {
            (expected_ext.clone(), true)
        } else {
            let template = base_dir.join(format!("{video_id}.%(ext)s"));
            let status = Command::new(&ytdlp)
                .arg("--quiet")
                .arg("--no-warnings")
                .arg("--no-progress")
                .arg("--merge-output-format")
                .arg("mp4")
                .arg("--ffmpeg-location")
                .arg(ffmpeg.to_string_lossy().to_string())
                .arg("-o")
                .arg(template.to_string_lossy().to_string())
                .arg(url)
                .status()
                .map_err(|err| {
                    YouTubeDownloadError::Other(format!("failed to execute yt-dlp: {err}"))
                })?;

            if !status.success() {
                return Err(YouTubeDownloadError::Download(format!(
                    "yt-dlp exit status {status}"
                )));
            }

            if expected_mp4.exists() {
                (expected_mp4.clone(), false)
            } else if expected_ext.exists() {
                (expected_ext.clone(), false)
            } else {
                return Err(YouTubeDownloadError::Download(
                    "yt-dlp reported success but no output file was produced".into(),
                ));
            }
        };

        let size_bytes = path.metadata().ok().map(|meta| meta.len());
        let sha = sha256sum(&path).ok();
        let mime = format!("video/{}", ext.trim_start_matches('.'));

        Ok(YouTubeDownload {
            path,
            metadata,
            mime,
            cached,
            sha256: sha,
            size_bytes,
        })
    }
}

fn parse_url(input: &str) -> Result<Url> {
    match Url::parse(input) {
        Ok(url) => Ok(url),
        Err(_) => Url::parse(&format!("https://{input}")).context("unable to parse YouTube URL"),
    }
}
