use crate::constants::{
    default_model_pricing, DEFAULT_MAX_VIDEO_WORKERS, DEFAULT_MAX_WORKERS, DEFAULT_MODEL,
    DEFAULT_VIDEO_TOKENS_PER_SECOND, DEFAULT_VIDEO_TOKEN_LIMIT,
};
use crate::video::{VideoEncoderPreference, DEFAULT_MAX_CHUNK_BYTES, DEFAULT_MAX_CHUNK_SECONDS};
use anyhow::{Context, Result};
use serde::Deserialize;
use serde_json::{Map as JsonMap, Value as JsonValue};
use serde_yaml::Value;
use std::collections::HashMap;
use std::env;
use std::fs::File;
use std::path::{Path, PathBuf};

fn get_env(names: &[&str]) -> Option<String> {
    for name in names {
        if let Ok(value) = env::var(name) {
            if !value.trim().is_empty() {
                return Some(value);
            }
        }
    }
    None
}

#[derive(Debug, Deserialize, Clone, Default)]
struct DefaultsConfig {
    model: Option<String>,
    output_dir: Option<PathBuf>,
    exports: Option<Vec<String>>,
}

#[derive(Debug, Deserialize, Clone, Default)]
struct SaveConfig {
    full_response: Option<bool>,
    intermediates: Option<bool>,
}

#[derive(Debug, Deserialize, Clone, Default)]
struct VideoConfig {
    token_limit: Option<u32>,
    tokens_per_second: Option<f64>,
    max_chunk_seconds: Option<f64>,
    max_chunk_bytes: Option<u64>,
    encoder: Option<String>,
    media_resolution: Option<String>,
}

#[derive(Debug, Deserialize, Clone, Default)]
struct RootConfig {
    defaults: Option<DefaultsConfig>,
    save: Option<SaveConfig>,
    video: Option<VideoConfig>,
    presets: Option<HashMap<String, HashMap<String, Value>>>,
    templates_dir: Option<PathBuf>,
    pricing_file: Option<PathBuf>,
}

#[derive(Debug, Clone)]
pub struct AppConfig {
    pub api_key: String,
    pub output_dir: Option<PathBuf>,
    pub templates_dir: PathBuf,
    pub default_model: String,
    pub save_full_response: bool,
    pub save_intermediates: bool,
    pub video_token_limit: Option<u32>,
    pub video_tokens_per_second: f64,
    pub video_max_chunk_seconds: f64,
    pub video_max_chunk_bytes: u64,
    pub media_resolution: String,
    pub max_workers: usize,
    pub max_video_workers: usize,
    pub video_encoder_preference: VideoEncoderPreference,
    pub presets: HashMap<String, HashMap<String, Value>>,
    pub exports: Vec<String>,
    pub config_path: Option<PathBuf>,
    pub pricing_file: Option<PathBuf>,
    pub pricing_defaults: HashMap<String, crate::constants::ModelPricing>,
}

impl AppConfig {
    pub fn load(explicit: Option<&Path>) -> Result<Self> {
        let api_key = env::var("GEMINI_API_KEY")
            .map_err(|_| anyhow::anyhow!("GEMINI_API_KEY environment variable not set"))?;

        let config_path = resolve_config_path(explicit)?;
        let root = if let Some(path) = &config_path {
            Some(read_config(path)?)
        } else {
            None
        };

        let defaults = root
            .as_ref()
            .and_then(|r| r.defaults.as_ref())
            .cloned()
            .unwrap_or_default();
        let save = root
            .as_ref()
            .and_then(|r| r.save.as_ref())
            .cloned()
            .unwrap_or_default();
        let video = root
            .as_ref()
            .and_then(|r| r.video.as_ref())
            .cloned()
            .unwrap_or_default();
        let presets = root
            .as_ref()
            .and_then(|r| r.presets.clone())
            .unwrap_or_default();

        let mut output_dir = defaults.output_dir.clone();
        let mut templates_dir = root
            .as_ref()
            .and_then(|r| r.templates_dir.clone())
            .unwrap_or_else(|| PathBuf::from("templates"));
        let mut default_model = defaults.model.unwrap_or_else(|| DEFAULT_MODEL.to_string());
        let mut exports = defaults
            .exports
            .clone()
            .unwrap_or_default()
            .into_iter()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>();

        let mut save_full_response = save.full_response.unwrap_or(false);
        let mut save_intermediates = save.intermediates.unwrap_or(false);

        let mut video_token_limit = video.token_limit.or(Some(DEFAULT_VIDEO_TOKEN_LIMIT));
        let mut video_tokens_per_second = video
            .tokens_per_second
            .unwrap_or(DEFAULT_VIDEO_TOKENS_PER_SECOND);
        let mut video_max_chunk_seconds =
            video.max_chunk_seconds.unwrap_or(DEFAULT_MAX_CHUNK_SECONDS);
        let mut video_max_chunk_bytes = video.max_chunk_bytes.unwrap_or(DEFAULT_MAX_CHUNK_BYTES);
        let mut media_resolution = video
            .media_resolution
            .unwrap_or_else(|| "default".to_string());

        let mut encoder_pref = video.encoder.clone();
        let pricing_file = root
            .as_ref()
            .and_then(|r| r.pricing_file.clone())
            .map(|p| p.expand());

        if media_resolution != "default" && media_resolution != "low" {
            media_resolution = "default".to_string();
        }

        if let Some(env_output) = get_env(&["RECAPIT_OUTPUT_DIR", "LECTURE_SUMMARIZER_OUTPUT_DIR"])
        {
            output_dir = Some(PathBuf::from(env_output).expand());
        }

        if let Some(env_templates) =
            get_env(&["RECAPIT_TEMPLATES_DIR", "LECTURE_SUMMARIZER_TEMPLATES_DIR"])
        {
            templates_dir = PathBuf::from(env_templates).expand();
        }

        if let Some(env_model) =
            get_env(&["RECAPIT_DEFAULT_MODEL", "LECTURE_SUMMARIZER_DEFAULT_MODEL"])
        {
            default_model = env_model;
        }

        if let Some(env_full) = get_env(&[
            "RECAPIT_SAVE_FULL_RESPONSE",
            "LECTURE_SUMMARIZER_SAVE_FULL_RESPONSE",
        ]) {
            save_full_response = parse_bool(&env_full);
        }
        if let Some(env_inter) = get_env(&[
            "RECAPIT_SAVE_INTERMEDIATES",
            "LECTURE_SUMMARIZER_SAVE_INTERMEDIATES",
        ]) {
            save_intermediates = parse_bool(&env_inter);
        }

        if let Some(video_limit) = get_env(&[
            "RECAPIT_VIDEO_TOKEN_LIMIT",
            "LECTURE_SUMMARIZER_VIDEO_TOKEN_LIMIT",
        ]) {
            video_token_limit = video_limit.parse::<u32>().ok();
        }

        let max_workers = parse_workers(
            &["RECAPIT_MAX_WORKERS", "LECTURE_SUMMARIZER_MAX_WORKERS"],
            DEFAULT_MAX_WORKERS,
        );
        let max_video_workers = parse_workers(
            &[
                "RECAPIT_MAX_VIDEO_WORKERS",
                "LECTURE_SUMMARIZER_MAX_VIDEO_WORKERS",
            ],
            DEFAULT_MAX_VIDEO_WORKERS,
        );

        if let Some(tokens_per_sec) = get_env(&[
            "RECAPIT_TOKENS_PER_SECOND",
            "LECTURE_SUMMARIZER_TOKENS_PER_SECOND",
        ]) {
            if let Ok(parsed) = tokens_per_sec.parse::<f64>() {
                video_tokens_per_second = parsed;
            }
        }

        if let Some(chunk_seconds) = get_env(&[
            "RECAPIT_VIDEO_MAX_CHUNK_SECONDS",
            "LECTURE_SUMMARIZER_VIDEO_MAX_CHUNK_SECONDS",
        ]) {
            if let Ok(parsed) = chunk_seconds.parse::<f64>() {
                video_max_chunk_seconds = parsed;
            }
        }

        if let Some(chunk_bytes) = get_env(&[
            "RECAPIT_VIDEO_MAX_CHUNK_BYTES",
            "LECTURE_SUMMARIZER_VIDEO_MAX_CHUNK_BYTES",
        ]) {
            if let Ok(parsed) = chunk_bytes.parse::<u64>() {
                video_max_chunk_bytes = parsed;
            }
        }

        if let Some(res_override) = get_env(&[
            "RECAPIT_VIDEO_MEDIA_RESOLUTION",
            "LECTURE_SUMMARIZER_VIDEO_MEDIA_RESOLUTION",
        ]) {
            let normalized = res_override.to_lowercase();
            media_resolution = if matches!(normalized.as_str(), "default" | "low") {
                normalized
            } else {
                "default".to_string()
            };
        }

        if let Some(encoder_override) =
            get_env(&["RECAPIT_VIDEO_ENCODER", "LECTURE_SUMMARIZER_VIDEO_ENCODER"])
        {
            encoder_pref = Some(encoder_override);
        }

        let video_encoder_preference = VideoEncoderPreference::parse(encoder_pref.as_deref())?;

        exports.sort();
        exports.dedup();

        Ok(Self {
            api_key,
            output_dir,
            templates_dir,
            default_model,
            save_full_response,
            save_intermediates,
            video_token_limit,
            video_tokens_per_second,
            video_max_chunk_seconds,
            video_max_chunk_bytes,
            media_resolution,
            max_workers,
            max_video_workers,
            video_encoder_preference,
            presets,
            exports,
            config_path,
            pricing_file,
            pricing_defaults: default_model_pricing()
                .into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .collect(),
        })
    }

    pub fn merged_presets(&self) -> HashMap<String, JsonMap<String, JsonValue>> {
        let mut merged = builtin_presets();
        for (name, values) in &self.presets {
            let mut map = JsonMap::new();
            for (key, value) in values {
                if let Ok(converted) = serde_json::to_value(value) {
                    map.insert(key.clone(), converted);
                }
            }
            merged.insert(name.to_lowercase(), map);
        }
        merged
    }
}

fn builtin_presets() -> HashMap<String, JsonMap<String, JsonValue>> {
    let mut presets = HashMap::new();
    presets.insert("basic".into(), JsonMap::new());

    let mut speed = JsonMap::new();
    speed.insert("pdf_mode".into(), JsonValue::String("images".into()));
    presets.insert("speed".into(), speed);

    let mut quality = JsonMap::new();
    quality.insert("pdf_mode".into(), JsonValue::String("pdf".into()));
    presets.insert("quality".into(), quality);

    presets
}

fn resolve_config_path(explicit: Option<&Path>) -> Result<Option<PathBuf>> {
    if let Some(path) = explicit {
        let expanded = path.expand();
        if expanded.exists() {
            return Ok(Some(expanded));
        }
        anyhow::bail!("Configuration file not found: {}", expanded.display());
    }

    if let Some(env_cfg) = get_env(&["RECAPIT_CONFIG", "LECTURE_SUMMARIZER_CONFIG"]) {
        let path = PathBuf::from(env_cfg).expand();
        if path.exists() {
            return Ok(Some(path));
        }
        anyhow::bail!("Configuration file not found: {}", path.display());
    }

    for candidate in ["recapit.yaml", "recapit.yml"] {
        let path = Path::new(candidate).expand();
        if path.exists() {
            return Ok(Some(path));
        }
    }
    Ok(None)
}

fn read_config(path: &Path) -> Result<RootConfig> {
    let file = File::open(path).with_context(|| format!("opening {}", path.display()))?;
    let root: RootConfig =
        serde_yaml::from_reader(file).with_context(|| format!("parsing {}", path.display()))?;
    Ok(root)
}

fn parse_bool(value: &str) -> bool {
    matches!(
        value.trim().to_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

fn parse_workers(vars: &[&str], default: usize) -> usize {
    for var in vars {
        if let Some(raw) = get_env(&[*var]) {
            if let Ok(parsed) = raw.parse::<usize>() {
                if parsed > 0 {
                    return parsed;
                }
            }
        }
    }
    default
}

trait PathExpand {
    fn expand(self) -> PathBuf;
}

impl PathExpand for &Path {
    fn expand(self) -> PathBuf {
        if self.starts_with("~") {
            if let Some(home) = dirs::home_dir() {
                return home.join(self.strip_prefix("~").unwrap());
            }
        }
        self.to_path_buf()
    }
}

impl PathExpand for PathBuf {
    fn expand(self) -> PathBuf {
        self.as_path().expand()
    }
}

impl PathExpand for String {
    fn expand(self) -> PathBuf {
        PathBuf::from(self).expand()
    }
}
