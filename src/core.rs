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
    pub fn from_str(value: &str) -> Option<Self> {
        match value.to_lowercase().as_str() {
            "slides" => Some(Self::Slides),
            "lecture" => Some(Self::Lecture),
            "document" => Some(Self::Document),
            "image" => Some(Self::Image),
            "video" => Some(Self::Video),
            _ => None,
        }
    }

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
pub enum PdfMode {
    Auto,
    Images,
    Pdf,
}

impl PdfMode {
    pub fn from_str(value: &str) -> Option<Self> {
        match value.to_lowercase().as_str() {
            "auto" => Some(Self::Auto),
            "images" => Some(Self::Images),
            "pdf" => Some(Self::Pdf),
            _ => None,
        }
    }
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

impl Asset {
    pub fn with_meta(mut self, meta: Value) -> Self {
        self.meta = meta;
        self
    }
}

#[derive(Debug, Clone)]
pub struct Job {
    pub source: String,
    pub recursive: bool,
    pub kind: Option<Kind>,
    pub pdf_mode: PdfMode,
    pub output_dir: Option<PathBuf>,
    pub model: String,
    pub preset: Option<String>,
    pub export: Vec<String>,
    pub skip_existing: bool,
    pub media_resolution: Option<String>,
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
    fn preamble(&self) -> String;
    fn instruction(&self, preamble: &str) -> String;
}

pub trait Provider: Send + Sync {
    fn supports(&self, capability: &str) -> bool;

    fn transcribe(
        &self,
        instruction: &str,
        assets: &[Asset],
        modality: &str,
        meta: &Value,
    ) -> Result<String>;

    fn cleanup(&self) -> Result<()> {
        Ok(())
    }
}

pub trait Writer: Send + Sync {
    fn write_latex(
        &self,
        base: &Path,
        name: &str,
        preamble: &str,
        body: &str,
    ) -> anyhow::Result<PathBuf>;
}
