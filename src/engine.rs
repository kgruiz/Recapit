use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde_json::{json, Value};
use tokio::sync::mpsc::UnboundedSender;

use crate::config::AppConfig;
use crate::core::{Asset, Ingestor, Job, Kind, Normalizer, PromptStrategy, Provider, Writer};
use crate::cost::CostEstimator;
use crate::prompts::TemplatePromptStrategy;
use crate::render::subtitles::SubtitleExporter;
use crate::telemetry::RunMonitor;
use crate::templates::TemplateLoader;
use crate::utils::{ensure_dir, slugify};

#[derive(Debug, Clone)]
pub enum ProgressKind {
    Discover,
    Normalize,
    Upload,
    Transcribe,
    Write,
}

#[derive(Debug, Clone)]
pub struct Progress {
    pub task: String,
    pub kind: ProgressKind,
    pub current: u64,
    pub total: u64,
    pub status: String,
}

pub struct Engine {
    pub ingestor: Box<dyn Ingestor>,
    pub normalizer: Box<dyn Normalizer>,
    pub prompts: HashMap<Kind, Box<dyn PromptStrategy>>,
    pub provider: Box<dyn Provider>,
    pub writer: Box<dyn Writer>,
    pub monitor: RunMonitor,
    pub cost: CostEstimator,
    pub subtitles: Option<SubtitleExporter>,
    pub progress: UnboundedSender<Progress>,
    save_full_response: bool,
    save_intermediates: bool,
    max_workers: usize,
    max_video_workers: usize,
}

impl Engine {
    pub fn new(
        ingestor: Box<dyn Ingestor>,
        normalizer: Box<dyn Normalizer>,
        provider: Box<dyn Provider>,
        writer: Box<dyn Writer>,
        progress: UnboundedSender<Progress>,
        monitor: RunMonitor,
        cost: CostEstimator,
        config: &AppConfig,
    ) -> Result<Self> {
        let loader = TemplateLoader::new(config.templates_dir.clone());
        let mut prompts = HashMap::new();
        for kind in [
            Kind::Slides,
            Kind::Lecture,
            Kind::Document,
            Kind::Image,
            Kind::Video,
        ] {
            prompts.insert(
                kind,
                Box::new(TemplatePromptStrategy::new(loader.clone(), kind)) as _,
            );
        }
        Ok(Self {
            ingestor,
            normalizer,
            prompts,
            provider,
            writer,
            monitor,
            cost,
            subtitles: Some(SubtitleExporter::default()),
            progress,
            save_full_response: config.save_full_response,
            save_intermediates: config.save_intermediates,
            max_workers: config.max_workers,
            max_video_workers: config.max_video_workers,
        })
    }

    pub async fn run(&mut self, job: &Job) -> Result<Option<PathBuf>> {
        self.normalizer.prepare(job)?;
        self.emit("discover", ProgressKind::Discover, 0, 1, "start");
        let assets = self.ingestor.discover(job)?;
        if assets.is_empty() {
            self.monitor
                .note_event("discover.empty", json!({"source": job.source.clone()}));
            return Ok(None);
        }
        let discover_total = assets.len() as u64;
        self.emit(
            "discover",
            ProgressKind::Discover,
            discover_total,
            discover_total,
            "done",
        );

        let kind = job.kind.unwrap_or_else(|| infer_kind(&assets));
        self.emit(
            "normalize",
            ProgressKind::Normalize,
            0,
            assets.len() as u64,
            "queue",
        );
        let normalized = self.normalizer.normalize(&assets, job.pdf_mode)?;
        let normalize_total = normalized.len() as u64;
        self.emit(
            "normalize",
            ProgressKind::Normalize,
            normalize_total,
            normalize_total,
            "done",
        );
        let modality = modality_for(&normalized);
        let chunk_descriptors = self.normalizer.chunk_descriptors();

        let base = job
            .output_dir
            .clone()
            .unwrap_or_else(|| Path::new("output").to_path_buf());
        let source_slug = slugify(if job.source.contains("://") {
            "remote"
        } else {
            Path::new(&job.source)
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("source")
        });
        let base_dir = base.join(&source_slug);
        let output_name = format!(
            "{}-transcribed",
            Path::new(&job.source)
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("output")
        );

        let prompt = self.prompts.get(&kind).expect("prompt strategy missing");
        let preamble = prompt.preamble();
        let instruction = prompt.instruction(&preamble);

        self.emit(
            "transcribe",
            ProgressKind::Transcribe,
            0,
            normalized.len() as u64,
            modality.to_string(),
        );
        let base_dir_str = base_dir.to_string_lossy().to_string();
        let meta = serde_json::json!({
            "kind": kind.as_str(),
            "source": job.source,
            "skip_existing": job.skip_existing,
            "media_resolution": job.media_resolution,
            "output_base": base_dir_str,
            "output_name": output_name,
            "save_full_response": self.save_full_response,
            "save_intermediates": self.save_intermediates,
            "max_workers": self.max_workers as u64,
            "max_video_workers": self.max_video_workers as u64,
        });
        let text = self
            .provider
            .transcribe(&instruction, &normalized, modality, &meta)?;
        self.emit(
            "transcribe",
            ProgressKind::Transcribe,
            normalize_total,
            normalize_total,
            "done",
        );

        self.emit("write", ProgressKind::Write, 0, 1, "latex");
        let output_path = self
            .writer
            .write_latex(&base_dir, &output_name, &preamble, &text)?;
        self.emit("write", ProgressKind::Write, 1, 1, "done");

        let mut extra_files = Vec::new();
        if self.save_intermediates {
            extra_files.extend(self.persist_intermediates(
                &base_dir,
                &normalized,
                &chunk_descriptors,
            )?);
        }
        if self.save_full_response {
            let path = self.persist_full_response(&base_dir, &output_name, &text)?;
            extra_files.push(path);
        }
        for fmt in &job.export {
            match fmt.as_str() {
                "srt" | "vtt" => {
                    if let Some(subtitles) = &self.subtitles {
                        if let Some(path) = subtitles.write(
                            fmt,
                            &base_dir,
                            &output_name,
                            &text,
                            &chunk_descriptors,
                        )? {
                            extra_files.push(path);
                        }
                    } else {
                        self.monitor.note_event(
                            "export.unsupported",
                            json!({
                                "format": fmt,
                                "reason": "subtitles_disabled",
                            }),
                        );
                    }
                }
                "markdown" | "md" => {
                    match crate::render::exports::write_markdown(
                        &base_dir,
                        &output_name,
                        &preamble,
                        &text,
                    ) {
                        Ok(path) => extra_files.push(path),
                        Err(err) => {
                            self.monitor.note_event(
                                "export.failed",
                                json!({
                                    "format": fmt,
                                    "error": err.to_string(),
                                }),
                            );
                        }
                    }
                }
                "json" => match crate::render::exports::write_summary_json(
                    &base_dir,
                    &output_name,
                    &preamble,
                    &text,
                    &chunk_descriptors,
                ) {
                    Ok(path) => extra_files.push(path),
                    Err(err) => {
                        self.monitor.note_event(
                            "export.failed",
                            json!({
                                "format": fmt,
                                "error": err.to_string(),
                            }),
                        );
                    }
                },
                "text" | "txt" => match crate::render::exports::write_plaintext(
                    &base_dir,
                    &output_name,
                    &preamble,
                    &text,
                ) {
                    Ok(path) => extra_files.push(path),
                    Err(err) => {
                        self.monitor.note_event(
                            "export.failed",
                            json!({
                                "format": fmt,
                                "error": err.to_string(),
                            }),
                        );
                    }
                },
                other => {
                    self.monitor.note_event(
                        "export.unsupported",
                        json!({
                            "format": other,
                            "reason": "unknown_format",
                        }),
                    );
                }
            }
        }

        let artifacts = self.normalizer.artifact_paths();
        let mut files = vec![output_path.clone()];
        files.extend(artifacts.clone());
        files.extend(extra_files.clone());

        self.provider.cleanup()?;

        let limits = crate::constants::rate_limits_per_minute();
        let limit_map = limits
            .into_iter()
            .map(|(k, v)| (k, Some(v)))
            .collect::<HashMap<_, _>>();
        let events_path = base_dir.join("run-events.ndjson");
        self.monitor.flush_summary(
            &base_dir.join("run-summary.json"),
            &self.cost,
            job,
            &files,
            &limit_map,
            Some(&events_path),
        )?;

        Ok(Some(output_path))
    }

    fn emit(
        &self,
        task: &str,
        kind: ProgressKind,
        current: u64,
        total: u64,
        status: impl Into<String>,
    ) {
        let _ = self.progress.send(Progress {
            task: task.into(),
            kind,
            current,
            total,
            status: status.into(),
        });
    }

    fn persist_full_response(&self, base_dir: &Path, name: &str, text: &str) -> Result<PathBuf> {
        let dir = base_dir.join("full-response");
        ensure_dir(&dir).context("creating full-response directory")?;
        let path = dir.join(format!("{name}.txt"));
        let mut content = text.trim_end_matches('\n').to_string();
        content.push('\n');
        fs::write(&path, content).with_context(|| format!("writing {}", path.display()))?;
        Ok(path)
    }

    fn persist_intermediates(
        &self,
        base_dir: &Path,
        normalized: &[Asset],
        chunks: &[Value],
    ) -> Result<Vec<PathBuf>> {
        let mut files = Vec::new();
        let dir = base_dir.join("intermediates");
        ensure_dir(&dir).context("creating intermediates directory")?;

        let normalized_path = dir.join("normalized-assets.json");
        let normalized_payload =
            serde_json::to_string_pretty(normalized).context("serializing normalized assets")?;
        fs::write(&normalized_path, normalized_payload)
            .with_context(|| format!("writing {}", normalized_path.display()))?;
        files.push(normalized_path);

        if !chunks.is_empty() {
            let chunks_path = dir.join("chunks.json");
            let chunk_payload =
                serde_json::to_string_pretty(chunks).context("serializing chunk descriptors")?;
            fs::write(&chunks_path, chunk_payload)
                .with_context(|| format!("writing {}", chunks_path.display()))?;
            files.push(chunks_path);
        }

        Ok(files)
    }
}

fn infer_kind(assets: &[Asset]) -> Kind {
    if let Some(first) = assets.first() {
        match first.media.as_str() {
            "video" => return Kind::Lecture,
            "image" => return Kind::Slides,
            _ => {}
        }
    }
    Kind::Document
}

fn modality_for(assets: &[Asset]) -> &str {
    assets
        .first()
        .map(|asset| match asset.media.as_str() {
            "video" | "audio" => "video",
            "pdf" => "pdf",
            _ => "image",
        })
        .unwrap_or("image")
}
