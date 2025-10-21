use anyhow::Context;
use serde::Deserialize;
use std::{
    env,
    fs::File,
    path::{Path, PathBuf},
};

#[derive(Debug, Deserialize, Clone, Default)]
pub struct Defaults {
    pub model: Option<String>,
    pub output_dir: Option<PathBuf>,
    pub exports: Option<Vec<String>>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize, Clone, Default)]
pub struct Save {
    pub full_response: Option<bool>,
    pub intermediates: Option<bool>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize, Clone, Default)]
pub struct VideoCfg {
    pub token_limit: Option<u64>,
    pub tokens_per_second: Option<f64>,
    pub max_chunk_seconds: Option<f64>,
    pub max_chunk_bytes: Option<u64>,
    pub encoder: Option<String>,
    pub media_resolution: Option<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize, Clone, Default)]
pub struct Root {
    pub defaults: Option<Defaults>,
    pub save: Option<Save>,
    pub video: Option<VideoCfg>,
    pub presets: Option<serde_yaml::Value>,
    pub templates_dir: Option<PathBuf>,
    pub pricing_file: Option<PathBuf>,
}

#[allow(dead_code)]
#[derive(Debug, Clone)]
pub struct AppConfig {
    pub api_key: String,
    pub default_model: String,
    pub output_dir: Option<PathBuf>,
    pub exports: Vec<String>,
    pub templates_dir: Option<PathBuf>,
    pub pricing_file: Option<PathBuf>,
    pub video_media_resolution: Option<String>,
}

impl AppConfig {
    pub fn load(path: Option<&str>) -> anyhow::Result<Self> {
        let api_key =
            env::var("GEMINI_API_KEY").map_err(|_| anyhow::anyhow!("GEMINI_API_KEY not set"))?;
        let root = match path {
            Some(p) => Some(Self::read_yaml(Path::new(p))?),
            None => {
                for candidate in ["recapit.yaml", "recapit.yml"] {
                    let path = Path::new(candidate);
                    if path.exists() {
                        return Self::_from_yaml(Some(Self::read_yaml(path)?), api_key.clone());
                    }
                }
                None
            }
        };
        Self::_from_yaml(root, api_key)
    }

    fn read_yaml(path: &Path) -> anyhow::Result<Root> {
        let file = File::open(path).with_context(|| format!("opening {}", path.display()))?;
        Ok(serde_yaml::from_reader(file)?)
    }

    fn _from_yaml(root: Option<Root>, api_key: String) -> anyhow::Result<Self> {
        let r = root.unwrap_or_default();
        let defaults = r.defaults.unwrap_or_default();
        let model = env::var("RECAPIT_DEFAULT_MODEL")
            .ok()
            .or(defaults.model)
            .unwrap_or_else(|| "gemini-2.5-flash-lite".to_string());

        let output_dir = env::var("RECAPIT_OUTPUT_DIR")
            .ok()
            .map(PathBuf::from)
            .or(defaults.output_dir);

        let exports = defaults.exports.unwrap_or_default();
        let video_media_resolution = r.video.as_ref().and_then(|v| v.media_resolution.clone());

        Ok(Self {
            api_key,
            default_model: model,
            output_dir,
            exports,
            templates_dir: r.templates_dir,
            pricing_file: r.pricing_file,
            video_media_resolution,
        })
    }
}
