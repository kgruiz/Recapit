use anyhow::{anyhow, bail, Context, Result};
use rayon::prelude::*;
use rayon::ThreadPoolBuilder;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Mutex, OnceLock};

use crate::utils::ensure_dir;

pub const DEFAULT_MAX_CHUNK_SECONDS: f64 = 7_200.0;
pub const DEFAULT_MAX_CHUNK_BYTES: u64 = 500 * 1024 * 1024;
pub const DEFAULT_TOKENS_PER_SECOND: f64 = 300.0;

static ENCODE_CACHE: OnceLock<Mutex<HashSet<String>>> = OnceLock::new();

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VideoMetadata {
    pub path: PathBuf,
    pub duration_seconds: f64,
    pub size_bytes: u64,
    pub fps: Option<f64>,
    pub width: Option<u32>,
    pub height: Option<u32>,
    pub video_codec: Option<String>,
    pub audio_codec: Option<String>,
    pub audio_sample_rate: Option<u32>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VideoEncoderPreference {
    Auto,
    Cpu,
    Nvenc,
    Videotoolbox,
    Qsv,
    Vaapi,
    Amf,
}

impl VideoEncoderPreference {
    pub fn parse(value: Option<&str>) -> Result<Self> {
        let normalized = value.unwrap_or("auto").trim().to_lowercase();
        match normalized.as_str() {
            "auto" => Ok(Self::Auto),
            "cpu" => Ok(Self::Cpu),
            "nvenc" => Ok(Self::Nvenc),
            "videotoolbox" => Ok(Self::Videotoolbox),
            "qsv" => Ok(Self::Qsv),
            "vaapi" => Ok(Self::Vaapi),
            "amf" => Ok(Self::Amf),
            "" => Ok(Self::Auto),
            other => bail!("Unknown video encoder preference '{}'", other),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EncoderSpec {
    pub preference: VideoEncoderPreference,
    pub codec: &'static str,
    pub args: &'static [&'static str],
    pub accelerated: bool,
}

#[derive(Debug, Clone)]
pub struct NormalizationResult {
    pub path: PathBuf,
}

#[derive(Debug, Clone)]
pub struct VideoChunk {
    pub index: usize,
    pub start_seconds: f64,
    pub end_seconds: f64,
    pub path: PathBuf,
}

#[derive(Debug, Clone)]
pub struct VideoChunkPlan {
    pub metadata: VideoMetadata,
    pub normalized_path: PathBuf,
    pub chunks: Vec<VideoChunk>,
}

static ENCODER_SPECS: &[EncoderSpec] = &[
    EncoderSpec {
        preference: VideoEncoderPreference::Cpu,
        codec: "libx264",
        args: &[
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-profile:v",
            "high",
            "-bf",
            "2",
        ],
        accelerated: false,
    },
    EncoderSpec {
        preference: VideoEncoderPreference::Nvenc,
        codec: "h264_nvenc",
        args: &["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq"],
        accelerated: true,
    },
    EncoderSpec {
        preference: VideoEncoderPreference::Videotoolbox,
        codec: "h264_videotoolbox",
        args: &["-c:v", "h264_videotoolbox"],
        accelerated: true,
    },
    EncoderSpec {
        preference: VideoEncoderPreference::Qsv,
        codec: "h264_qsv",
        args: &["-c:v", "h264_qsv"],
        accelerated: true,
    },
    EncoderSpec {
        preference: VideoEncoderPreference::Vaapi,
        codec: "h264_vaapi",
        args: &["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi"],
        accelerated: true,
    },
    EncoderSpec {
        preference: VideoEncoderPreference::Amf,
        codec: "h264_amf",
        args: &["-c:v", "h264_amf"],
        accelerated: true,
    },
];

pub fn select_encoder_chain(preference: VideoEncoderPreference) -> Vec<&'static EncoderSpec> {
    let supported = ffmpeg_encoder_names();
    let mut chain = Vec::new();
    let cpu = encoder_spec(VideoEncoderPreference::Cpu);
    let maybe_push = |chain: &mut Vec<&EncoderSpec>, spec: &'static EncoderSpec| {
        if supported.contains(spec.codec) {
            chain.push(spec);
        }
    };
    match preference {
        VideoEncoderPreference::Auto => {
            for candidate in auto_preference_order() {
                if let Some(spec) = encoder_spec(candidate) {
                    maybe_push(&mut chain, spec);
                }
            }
            if let Some(cpu_spec) = cpu {
                if !chain.contains(&cpu_spec) {
                    chain.push(cpu_spec);
                }
            }
        }
        other => {
            if let Some(spec) = encoder_spec(other) {
                maybe_push(&mut chain, spec);
            }
            if let Some(cpu_spec) = cpu {
                if !chain.contains(&cpu_spec) {
                    chain.push(cpu_spec);
                }
            }
        }
    }
    if chain.is_empty() {
        if let Some(cpu_spec) = cpu {
            chain.push(cpu_spec);
        }
    }
    chain
}

fn encoder_spec(preference: VideoEncoderPreference) -> Option<&'static EncoderSpec> {
    ENCODER_SPECS
        .iter()
        .find(|spec| spec.preference == preference)
}

fn auto_preference_order() -> Vec<VideoEncoderPreference> {
    if cfg!(target_os = "macos") {
        vec![
            VideoEncoderPreference::Videotoolbox,
            VideoEncoderPreference::Nvenc,
            VideoEncoderPreference::Qsv,
            VideoEncoderPreference::Amf,
        ]
    } else if cfg!(target_os = "windows") {
        vec![
            VideoEncoderPreference::Nvenc,
            VideoEncoderPreference::Amf,
            VideoEncoderPreference::Qsv,
            VideoEncoderPreference::Videotoolbox,
        ]
    } else {
        vec![
            VideoEncoderPreference::Nvenc,
            VideoEncoderPreference::Qsv,
            VideoEncoderPreference::Vaapi,
            VideoEncoderPreference::Videotoolbox,
        ]
    }
}

pub fn ffmpeg_encoder_names() -> HashSet<String> {
    let cache = ENCODE_CACHE.get_or_init(|| Mutex::new(HashSet::new()));
    {
        let locked = cache.lock().unwrap();
        if !locked.is_empty() {
            return locked.clone();
        }
    }

    let output = Command::new("ffmpeg")
        .args(["-hide_banner", "-encoders"])
        .output();
    let mut names = HashSet::new();
    if let Ok(out) = output {
        let text = String::from_utf8_lossy(&out.stdout);
        let re = Regex::new(r"^\s*[A-Z\.]{6}\s+(\S+)").unwrap();
        for line in text.lines() {
            if let Some(capt) = re.captures(line) {
                names.insert(capt[1].to_string());
            }
        }
    }
    let mut locked = cache.lock().unwrap();
    *locked = names.clone();
    names
}

pub fn normalize_video(
    path: &Path,
    output_dir: &Path,
    encoder_chain: &[&EncoderSpec],
) -> Result<NormalizationResult> {
    ensure_dir(output_dir)?;
    let source = PathBuf::from(path);
    let normalized = output_dir.join(format!(
        "{}-normalized.mp4",
        source.file_stem().unwrap_or_default().to_string_lossy()
    ));

    if normalized.exists() && normalized.metadata()?.modified()? >= path.metadata()?.modified()? {
        probe_video(&normalized)?;
        return Ok(NormalizationResult { path: normalized });
    }

    let chain = if encoder_chain.is_empty() {
        vec![encoder_spec(VideoEncoderPreference::Cpu)
            .ok_or_else(|| anyhow!("No CPU encoder spec available"))?]
    } else {
        encoder_chain.to_vec()
    };

    let mut last_err: Option<anyhow::Error> = None;
    for spec in chain {
        let mut cmd = Command::new("ffmpeg");
        cmd.args(["-y", "-i", path.to_str().unwrap()]);
        cmd.args(spec.args);
        cmd.args([
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]);
        cmd.arg(normalized.to_str().unwrap());
        match cmd.output() {
            Ok(output) if output.status.success() => {
                return Ok(NormalizationResult { path: normalized });
            }
            Ok(output) => {
                let stderr = String::from_utf8_lossy(&output.stderr);
                last_err = Some(anyhow!("ffmpeg failed ({}) {}", spec.codec, stderr));
            }
            Err(err) => {
                last_err = Some(anyhow!(err));
            }
        }
    }
    Err(last_err.unwrap_or_else(|| anyhow!("ffmpeg failed for {}", path.display())))
}

pub fn probe_video(path: &Path) -> Result<VideoMetadata> {
    let output = Command::new("ffprobe")
        .args([
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            path.to_str().unwrap(),
        ])
        .output()
        .context("ffprobe invocation failed")?;
    if !output.status.success() {
        bail!("ffprobe failed with status {}", output.status);
    }
    let parsed: Value = serde_json::from_slice(&output.stdout)?;
    let format = parsed.get("format").cloned().unwrap_or_default();
    let streams = parsed
        .get("streams")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let duration = format
        .get("duration")
        .and_then(|v| v.as_str())
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(0.0);
    let size_bytes = format
        .get("size")
        .and_then(|v| v.as_str())
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(0);

    let mut meta = VideoMetadata {
        path: path.to_path_buf(),
        duration_seconds: duration.max(0.0),
        size_bytes,
        fps: None,
        width: None,
        height: None,
        video_codec: None,
        audio_codec: None,
        audio_sample_rate: None,
    };

    for stream in streams {
        if let Some(codec_type) = stream.get("codec_type").and_then(|v| v.as_str()) {
            match codec_type {
                "video" => {
                    meta.video_codec = stream
                        .get("codec_name")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string());
                    meta.width = stream
                        .get("width")
                        .and_then(|v| v.as_u64())
                        .map(|v| v as u32);
                    meta.height = stream
                        .get("height")
                        .and_then(|v| v.as_u64())
                        .map(|v| v as u32);
                    meta.fps = stream
                        .get("avg_frame_rate")
                        .and_then(|v| v.as_str())
                        .and_then(parse_rate);
                }
                "audio" => {
                    meta.audio_codec = stream
                        .get("codec_name")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string());
                    meta.audio_sample_rate = stream
                        .get("sample_rate")
                        .and_then(|v| v.as_str())
                        .and_then(|s| s.parse::<u32>().ok());
                }
                _ => {}
            }
        }
    }

    Ok(meta)
}

fn parse_rate(rate: &str) -> Option<f64> {
    if let Some((num, denom)) = rate.split_once('/') {
        let n: f64 = num.parse().ok()?;
        let d: f64 = denom.parse().ok()?;
        if d > 0.0 {
            return Some(n / d);
        }
        return None;
    }
    rate.parse().ok()
}

pub fn plan_video_chunks(
    metadata: &VideoMetadata,
    normalized_path: &Path,
    max_seconds: f64,
    max_bytes: u64,
    token_limit: Option<u32>,
    tokens_per_second: f64,
    chunk_dir: &Path,
    max_workers: usize,
) -> Result<VideoChunkPlan> {
    let bounds = compute_chunk_boundaries(
        metadata,
        max_seconds,
        max_bytes,
        token_limit,
        tokens_per_second,
    );
    if bounds.len() == 1 {
        return Ok(VideoChunkPlan {
            metadata: metadata.clone(),
            normalized_path: normalized_path.to_path_buf(),
            chunks: vec![VideoChunk {
                index: 0,
                start_seconds: bounds[0].0,
                end_seconds: bounds[0].1,
                path: normalized_path.to_path_buf(),
            }],
        });
    }

    ensure_dir(chunk_dir)?;
    let worker_count = bounds.len().min(max_workers.max(1));
    let stem = normalized_path
        .file_stem()
        .unwrap_or_default()
        .to_string_lossy()
        .to_string();

    let chunks: Vec<VideoChunk> = if worker_count <= 1 {
        bounds
            .iter()
            .enumerate()
            .map(|(idx, (start, end))| {
                let chunk_path = chunk_dir.join(format!("{stem}-chunk{idx:02}.mp4"));
                extract_segment(normalized_path, &chunk_path, *start, *end)?;
                Ok(VideoChunk {
                    index: idx,
                    start_seconds: *start,
                    end_seconds: *end,
                    path: chunk_path,
                })
            })
            .collect::<Result<Vec<_>>>()?
    } else {
        let pool = ThreadPoolBuilder::new().num_threads(worker_count).build()?;
        pool.install(|| {
            bounds
                .par_iter()
                .enumerate()
                .map(|(idx, (start, end))| {
                    let chunk_path = chunk_dir.join(format!("{stem}-chunk{idx:02}.mp4"));
                    extract_segment(normalized_path, &chunk_path, *start, *end)?;
                    Ok(VideoChunk {
                        index: idx,
                        start_seconds: *start,
                        end_seconds: *end,
                        path: chunk_path,
                    })
                })
                .collect::<Result<Vec<_>>>()
        })?
    };

    Ok(VideoChunkPlan {
        metadata: metadata.clone(),
        normalized_path: normalized_path.to_path_buf(),
        chunks,
    })
}

fn compute_chunk_boundaries(
    metadata: &VideoMetadata,
    max_seconds: f64,
    max_bytes: u64,
    token_limit: Option<u32>,
    tokens_per_second: f64,
) -> Vec<(f64, f64)> {
    let duration = metadata.duration_seconds.max(0.0);
    if duration <= f64::EPSILON {
        return vec![(0.0, 0.0)];
    }
    let bytes_per_second = if duration > 0.0 {
        metadata.size_bytes as f64 / duration
    } else {
        metadata.size_bytes as f64
    };
    let mut effective = max_seconds;
    if max_bytes > 0 && bytes_per_second > 0.0 {
        effective = effective.min(max_bytes as f64 / bytes_per_second);
    }
    if let Some(limit) = token_limit {
        if tokens_per_second > 0.0 {
            let by_tokens = limit as f64 / tokens_per_second;
            if by_tokens.is_finite() && by_tokens > 0.0 {
                effective = effective.min(by_tokens);
            }
        }
    }
    if !effective.is_finite() || effective <= 0.0 {
        effective = 1.0;
    }

    let mut start = 0.0;
    let mut bounds = Vec::new();
    while start < duration {
        let end = (start + effective).min(duration);
        bounds.push((start, end));
        start = end;
    }
    if let Some(last) = bounds.last_mut() {
        last.1 = duration;
    }
    bounds
}

fn extract_segment(source: &Path, dest: &Path, start: f64, end: f64) -> Result<()> {
    if dest.exists()
        && dest.metadata()?.modified()? >= source.metadata()?.modified()?
        && dest.metadata()?.len() > 0
    {
        return Ok(());
    }
    ensure_dir(dest.parent().unwrap())?;
    let status = Command::new("ffmpeg")
        .args([
            "-y",
            "-i",
            source.to_str().unwrap(),
            "-ss",
            &format!("{start:.3}"),
            "-to",
            &format!("{end:.3}"),
            "-c",
            "copy",
            dest.to_str().unwrap(),
        ])
        .status()?;
    if !status.success() {
        bail!("ffmpeg failed while extracting segment");
    }
    Ok(())
}

pub fn sha256sum(path: &Path) -> Result<String> {
    use sha2::{Digest, Sha256};
    let mut file = std::fs::File::open(path)?;
    let mut hasher = Sha256::new();
    std::io::copy(&mut file, &mut hasher)?;
    Ok(hex::encode(hasher.finalize()))
}

pub fn seconds_to_iso(value: f64) -> String {
    let total_seconds = value.max(0.0).round() as i64;
    let hours = total_seconds / 3600;
    let minutes = (total_seconds % 3600) / 60;
    let seconds = total_seconds % 60;
    format!("PT{}H{}M{}S", hours, minutes, seconds)
}
