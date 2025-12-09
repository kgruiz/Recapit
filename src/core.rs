use anyhow::Result;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Hash)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Slides,
    Lecture,
    Document,
    Image,
    Video,
}

impl Kind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Kind::Slides => "slides",
            Kind::Lecture => "lecture",
            Kind::Document => "document",
            Kind::Image => "image",
            Kind::Video => "video",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OutputFormat {
    Markdown,
    Latex,
}

impl OutputFormat {
    pub fn from_str(value: &str) -> Option<Self> {
        match value.to_lowercase().as_str() {
            "markdown" | "md" => Some(Self::Markdown),
            "latex" | "tex" => Some(Self::Latex),
            _ => None,
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            OutputFormat::Markdown => "markdown",
            OutputFormat::Latex => "latex",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PdfMode {
    Auto,
    Images,
    Pdf,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SourceKind {
    Local,
    Url,
    Youtube,
    Drive,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Asset {
    pub path: PathBuf,
    pub media: String,
    pub page_index: Option<u32>,
    pub source_kind: SourceKind,
    pub mime: Option<String>,
    #[serde(default)]
    pub meta: Value,
}

#[derive(Debug, Clone)]
pub struct Job {
    pub source: String,
    pub job_label: String,
    pub job_id: String,
    #[allow(dead_code)]
    pub job_index: usize,
    #[allow(dead_code)]
    pub job_total: usize,
    pub recursive: bool,
    pub kind: Option<Kind>,
    pub pdf_mode: PdfMode,
    pub output_dir: Option<PathBuf>,
    pub model: String,
    pub preset: Option<String>,
    pub export: Vec<String>,
    pub format: OutputFormat,
    pub skip_existing: bool,
    pub media_resolution: Option<String>,
    pub save_full_response: bool,
    pub save_intermediates: bool,
    pub save_metadata: bool,
    pub max_workers: usize,
    pub max_video_workers: usize,
    pub pdf_dpi: u32,
}

pub trait Ingestor: Send + Sync {
    fn discover(&self, job: &Job) -> anyhow::Result<Vec<Asset>>;
}

pub trait Normalizer: Send + Sync {
    fn prepare(&mut self, _job: &Job) -> anyhow::Result<()> {
        Ok(())
    }

    fn normalize(&mut self, assets: &[Asset], pdf_mode: PdfMode) -> anyhow::Result<Vec<Asset>>;

    fn chunk_descriptors(&self) -> Vec<Value> {
        Vec::new()
    }

    fn artifact_paths(&self) -> Vec<PathBuf> {
        Vec::new()
    }
}

pub trait PromptStrategy: Send + Sync {
    fn preamble(&self, format: OutputFormat) -> String;
    fn instruction(&self, format: OutputFormat, preamble: &str) -> String;
}

pub trait Provider: Send + Sync {
    fn transcribe(
        &self,
        instruction: &str,
        assets: &[Asset],
        modality: &str,
        meta: &Value,
    ) -> anyhow::Result<String>;

    fn cleanup(&self) -> Result<()> {
        Ok(())
    }
}

pub trait Writer: Send + Sync {
    fn write(
        &self,
        format: OutputFormat,
        base: &Path,
        name: &str,
        preamble: &str,
        body: &str,
    ) -> anyhow::Result<PathBuf>;
}
