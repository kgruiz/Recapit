use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::Result;
use serde_json::{json, Map, Value};
use tokio::sync::mpsc::UnboundedSender;

use crate::config::AppConfig;
use crate::conversion::LatexConverter;
use crate::core::{Asset, Ingestor, Job, Kind, Normalizer, PromptStrategy, Provider, Writer};
use crate::cost::CostEstimator;
use crate::prompts::TemplatePromptStrategy;
use crate::render::subtitles::SubtitleExporter;
use crate::telemetry::RunMonitor;
use crate::templates::TemplateLoader;
use crate::utils::slugify;

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
    converter: Option<LatexConverter>,
    templates: TemplateLoader,
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
        converter: Option<LatexConverter>,
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
            converter,
            templates: loader,
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
            format!("{discover_total} items"),
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
            format!("{normalize_total}/{normalize_total} done"),
        );
        let modality = modality_for(&normalized);

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

        let segment_total = normalized.len() as u64;
        self.emit(
            "transcribe",
            ProgressKind::Transcribe,
            0,
            segment_total,
            format!("0/{segment_total} {modality}"),
        );
        let base_dir_str = base_dir.to_string_lossy().to_string();
        let meta = serde_json::json!({
            "kind": kind.as_str(),
            "source": job.source,
            "skip_existing": job.skip_existing,
            "media_resolution": job.media_resolution,
            "output_base": base_dir_str,
            "output_name": output_name,
            "save_full_response": job.save_full_response,
            "save_intermediates": job.save_intermediates,
            "max_workers": job.max_workers,
            "max_video_workers": job.max_video_workers,
        });
        let text = self
            .provider
            .transcribe(&instruction, &normalized, modality, &meta)?;
        self.emit(
            "transcribe",
            ProgressKind::Transcribe,
            normalize_total,
            normalize_total,
            format!("{normalize_total}/{normalize_total} done"),
        );

        self.emit("write", ProgressKind::Write, 0, 1, "latex");
        let output_path = self
            .writer
            .write_latex(&base_dir, &output_name, &preamble, &text)?;
        self.emit("write", ProgressKind::Write, 1, 1, "done");

        let mut extra_files = Vec::new();
        if job.save_full_response {
            let full_dir = base_dir.join("full-response");
            fs::create_dir_all(&full_dir)?;
            let full_path = full_dir.join(format!("{output_name}.txt"));
            let mut content = text.trim_end().to_string();
            content.push('\n');
            fs::write(&full_path, content)?;
            extra_files.push(full_path);
        }
        if let Some(subtitles) = &self.subtitles {
            if !job.export.is_empty() {
                let chunks = self.normalizer.chunk_descriptors();
                for fmt in &job.export {
                    if let Some(path) =
                        subtitles.write(fmt, &base_dir, &output_name, &text, &chunks)?
                    {
                        extra_files.push(path);
                    }
                }
            }
        }

        let mut latex_source: Option<String> = None;
        for fmt in &job.export {
            let normalized = fmt.trim().to_lowercase();
            match normalized.as_str() {
                "markdown" | "md" => {
                    let target = base_dir.join(format!("{output_name}.md"));
                    if job.skip_existing && target.exists() {
                        continue;
                    }
                    fs::create_dir_all(&base_dir)?;
                    if let Some(converter) = &self.converter {
                        if latex_source.is_none() {
                            latex_source = Some(fs::read_to_string(&output_path)?);
                        }
                        let latex_text = latex_source.as_ref().unwrap();
                        let mut metadata = Map::new();
                        metadata.insert(
                            "source".into(),
                            Value::String(output_path.to_string_lossy().to_string()),
                        );
                        metadata.insert("export".into(), Value::String("markdown".into()));
                        let prompt = self.templates.latex_to_md_prompt();
                        let rendered = converter
                            .latex_to_markdown(&job.model, &prompt, latex_text, metadata)?;
                        let mut value = rendered.trim_end().to_string();
                        value.push('\n');
                        fs::write(&target, value)?;
                    } else {
                        let mut content = text.trim().to_string();
                        content.push('\n');
                        fs::write(&target, content)?;
                    }
                    extra_files.push(target);
                }
                "json" => {
                    let target = base_dir.join(format!("{output_name}.json"));
                    if job.skip_existing && target.exists() {
                        continue;
                    }
                    fs::create_dir_all(&base_dir)?;
                    if let Some(converter) = &self.converter {
                        if latex_source.is_none() {
                            latex_source = Some(fs::read_to_string(&output_path)?);
                        }
                        let latex_text = latex_source.as_ref().unwrap();
                        let mut metadata = Map::new();
                        metadata.insert(
                            "source".into(),
                            Value::String(output_path.to_string_lossy().to_string()),
                        );
                        metadata.insert("export".into(), Value::String("json".into()));
                        let prompt = self.templates.latex_to_json_prompt();
                        let rendered =
                            converter.latex_to_json(&job.model, &prompt, latex_text, metadata)?;
                        let mut value = rendered.trim_end().to_string();
                        value.push('\n');
                        fs::write(&target, value)?;
                    } else {
                        let payload = json!({
                            "source": job.source,
                            "model": job.model,
                            "text": text,
                        });
                        fs::write(&target, serde_json::to_string_pretty(&payload)?)?;
                    }
                    extra_files.push(target);
                }
                _ => {}
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
