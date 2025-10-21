use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum Kind {
    Slides,
    Lecture,
    Document,
    Image,
    Video,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum PdfMode {
    Auto,
    Images,
    Pdf,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
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
    pub meta: serde_json::Value,
}

#[allow(dead_code)]
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
    fn prepare(&self, _job: &Job) -> anyhow::Result<()> {
        Ok(())
    }

    fn normalize(&self, assets: &[Asset], pdf_mode: PdfMode) -> anyhow::Result<Vec<Asset>>;

    #[allow(dead_code)]
    fn chunk_descriptors(&self) -> Vec<serde_json::Value> {
        vec![]
    }

    #[allow(dead_code)]
    fn artifact_paths(&self) -> Vec<PathBuf> {
        vec![]
    }
}

pub trait PromptStrategy: Send + Sync {
    fn preamble(&self) -> String;
    fn instruction(&self, preamble: &str) -> String;
}

pub trait Provider: Send + Sync {
    #[allow(dead_code)]
    fn supports(&self, capability: &str) -> bool;

    fn transcribe(
        &self,
        instruction: &str,
        assets: &[Asset],
        modality: &str,
        meta: &serde_json::Value,
    ) -> anyhow::Result<String>;
}

pub trait Writer: Send + Sync {
    fn write_latex(
        &self,
        base: &std::path::Path,
        name: &str,
        preamble: &str,
        body: &str,
    ) -> anyhow::Result<PathBuf>;
}
