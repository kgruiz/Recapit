use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{anyhow, Result};
use serde_json::{json, Map, Value};
use tokio::sync::mpsc::UnboundedSender;

use crate::config::AppConfig;
use crate::conversion::LatexConverter;
use crate::core::{
    Asset, Ingestor, Job, Kind, Normalizer, OutputFormat, PromptStrategy, Provider, Writer,
};
use crate::cost::CostEstimator;
use crate::pdf;
use crate::progress::{Progress, ProgressScope, ProgressStage};
use crate::prompts::TemplatePromptStrategy;
use crate::render::subtitles::SubtitleExporter;
use crate::telemetry::RunMonitor;
use crate::templates::TemplateLoader;
use crate::utils::ensure_dir;

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

        let job_label = job.job_label.clone();
        let job_id = job.job_id.clone();

        // Run-level start (single job today, but keep structure for future multi-job runs).
        self.emit(Progress {
            scope: ProgressScope::Run,
            stage: ProgressStage::Discover,
            current: 0,
            total: 1,
            status: job_label.clone(),
            finished: false,
        });

        // Discover
        let assets = self.ingestor.discover(job)?;
        if assets.is_empty() {
            self.monitor
                .note_event("discover.empty", json!({"source": job.source.clone()}));
            return Ok(None);
        }
        let discover_total = assets.len() as u64;
        let media_summary = media_summary(&assets);

        // For single-job single-chunk scenario we drive the single bar through phases; for chunked we keep run bar static until job completion.
        self.emit(Progress {
            scope: ProgressScope::Job {
                id: job_id.clone(),
                label: job_label.clone(),
            },
            stage: ProgressStage::Discover,
            current: discover_total,
            total: discover_total,
            status: format!("{discover_total} items · {media_summary}"),
            finished: false,
        });

        let kind = job.kind.unwrap_or_else(|| infer_kind(&assets));

        // Normalize
        self.emit(Progress {
            scope: ProgressScope::Job {
                id: job_id.clone(),
                label: job_label.clone(),
            },
            stage: ProgressStage::Normalize,
            current: 0,
            total: assets.len() as u64,
            status: "queue".into(),
            finished: false,
        });
        let normalized = self.normalizer.normalize(&assets, job.pdf_mode)?;
        let normalize_total = normalized.len() as u64;
        let page_total = estimate_page_total(&normalized);
        self.emit(Progress {
            scope: ProgressScope::Job {
                id: job_id.clone(),
                label: job_label.clone(),
            },
            stage: ProgressStage::Normalize,
            current: normalize_total,
            total: normalize_total,
            status: format!("{} ready", counts_summary(normalize_total, page_total)),
            finished: false,
        });

        if normalize_total > 1 {
            self.emit(Progress {
                scope: ProgressScope::ChunkProgress {
                    job_id: job_id.clone(),
                    total: normalize_total,
                },
                stage: ProgressStage::Transcribe,
                current: 0,
                total: normalize_total,
                status: format!("{job_label}: 0/{normalize_total} chunks"),
                finished: false,
            });
        }
        let modality = modality_for(&normalized);

        let output_format = job.format;

        let mut output_name = format!(
            "{}-transcribed",
            Path::new(&job.source)
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("output")
        );
        let mut base_dir = if job.save_metadata {
            job.output_dir
                .clone()
                .unwrap_or_else(|| PathBuf::from(&output_name))
        } else {
            job.output_dir.clone().unwrap_or_else(|| PathBuf::from("."))
        };

        if job.save_metadata {
            let resolved = crate::utils::resolve_path_with_prompt(&base_dir, true)?
                .ok_or_else(|| anyhow!("operation cancelled for {}", base_dir.display()))?;
            base_dir = resolved;
            ensure_dir(&base_dir)?;
        } else {
            let target = match output_format {
                OutputFormat::Markdown => base_dir.join(format!("{output_name}.md")),
                OutputFormat::Latex => base_dir.join(format!("{output_name}.tex")),
            };
            if let Some(resolved) = crate::utils::resolve_path_with_prompt(&target, false)? {
                let parent = resolved.parent().unwrap_or(Path::new(".")).to_path_buf();
                output_name = resolved
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or(&output_name)
                    .to_string();
                base_dir = parent;
            } else {
                return Ok(None);
            }
        }

        let prompt = self.prompts.get(&kind).expect("prompt strategy missing");
        let preamble = prompt.preamble(output_format);
        let instruction = prompt.instruction(output_format, &preamble);

        let segment_total = normalized.len() as u64;
        self.emit(Progress {
            scope: ProgressScope::Job {
                id: job_id.clone(),
                label: job_label.clone(),
            },
            stage: ProgressStage::Transcribe,
            current: 0,
            total: segment_total,
            status: format!(
                "{} · mode {modality}",
                counts_summary(segment_total, page_total)
            ),
            finished: false,
        });
        let base_dir_str = base_dir.to_string_lossy().to_string();
        let meta = serde_json::json!({
            "kind": kind.as_str(),
            "source": job.source,
            "skip_existing": job.skip_existing,
            "media_resolution": job.media_resolution,
            "format": output_format.as_str(),
            "output_base": base_dir_str,
            "output_name": output_name,
            "save_full_response": job.save_full_response,
            "save_intermediates": job.save_intermediates,
            "save_metadata": job.save_metadata,
            "max_workers": job.max_workers,
            "max_video_workers": job.max_video_workers,
            "pdf_dpi": job.pdf_dpi,
            "job_id": job_id,
            "job_label": job_label,
        });
        let text = self
            .provider
            .transcribe(&instruction, &normalized, modality, &meta)?;
        self.emit(Progress {
            scope: ProgressScope::Job {
                id: meta["job_id"].as_str().unwrap_or_default().to_string(),
                label: meta["job_label"].as_str().unwrap_or_default().to_string(),
            },
            stage: ProgressStage::Transcribe,
            current: normalize_total,
            total: normalize_total,
            status: format!("{} processed", counts_summary(normalize_total, page_total)),
            finished: false,
        });

        self.emit(Progress {
            scope: ProgressScope::Job {
                id: meta["job_id"].as_str().unwrap_or_default().to_string(),
                label: meta["job_label"].as_str().unwrap_or_default().to_string(),
            },
            stage: ProgressStage::Write,
            current: 0,
            total: 1,
            status: output_format.as_str().into(),
            finished: false,
        });
        let output_path =
            self.writer
                .write(output_format, &base_dir, &output_name, &preamble, &text)?;
        self.emit(Progress {
            scope: ProgressScope::Job {
                id: meta["job_id"].as_str().unwrap_or_default().to_string(),
                label: meta["job_label"].as_str().unwrap_or_default().to_string(),
            },
            stage: ProgressStage::Write,
            current: 1,
            total: 1,
            status: "done".into(),
            finished: true,
        });

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

        match output_format {
            OutputFormat::Markdown => {
                let mut markdown_source: Option<String> = None;
                for fmt in &job.export {
                    let normalized = fmt.trim().to_lowercase();
                    match normalized.as_str() {
                        "markdown" | "md" => {
                            continue;
                        }
                        "json" => {
                            let target = base_dir.join(format!("{output_name}.json"));
                            if job.skip_existing && target.exists() {
                                continue;
                            }
                            fs::create_dir_all(&base_dir)?;
                            if let Some(converter) = &self.converter {
                                if markdown_source.is_none() {
                                    markdown_source = Some(fs::read_to_string(&output_path)?);
                                }
                                let markdown_text = markdown_source.as_ref().unwrap();
                                let mut metadata = Map::new();
                                metadata.insert(
                                    "source".into(),
                                    Value::String(output_path.to_string_lossy().to_string()),
                                );
                                metadata.insert("export".into(), Value::String("json".into()));
                                let prompt = self.templates.markdown_to_json_prompt();
                                let rendered = converter.markdown_to_json(
                                    &job.model,
                                    &prompt,
                                    markdown_text,
                                    metadata,
                                )?;
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
            }
            OutputFormat::Latex => {
                let mut latex_source: Option<String> = None;
                for fmt in &job.export {
                    let normalized = fmt.trim().to_lowercase();
                    match normalized.as_str() {
                        "latex" | "tex" => {
                            continue;
                        }
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
                                let rendered = converter
                                    .latex_to_json(&job.model, &prompt, latex_text, metadata)?;
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
            }
        }

        let artifacts = self.normalizer.artifact_paths();
        let mut files = vec![output_path.clone()];
        files.extend(artifacts.clone());
        files.extend(extra_files.clone());

        self.provider.cleanup()?;

        self.emit(Progress {
            scope: ProgressScope::Run,
            stage: ProgressStage::Write,
            current: 1,
            total: 1,
            status: output_path.display().to_string(),
            finished: false,
        });
        if job.save_metadata {
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
        }

        Ok(Some(output_path))
    }

    fn emit(&self, progress: Progress) {
        let _ = self.progress.send(progress);
    }
}

fn media_summary(assets: &[Asset]) -> String {
    let mut counts: HashMap<String, u64> = HashMap::new();
    for asset in assets {
        *counts.entry(asset.media.clone()).or_insert(0) += 1;
    }
    if counts.is_empty() {
        return "unknown".into();
    }
    let mut items: Vec<String> = counts
        .into_iter()
        .map(|(media, count)| format!("{count} {}{}", media, if count == 1 { "" } else { "s" }))
        .collect();
    items.sort();
    items.join(" · ")
}

fn counts_summary(chunks: u64, pages: Option<u64>) -> String {
    let mut parts = vec![format!("{chunks} {}", pluralize(chunks, "chunk"))];
    if let Some(total_pages) = pages {
        parts.push(format!("{total_pages} {}", pluralize(total_pages, "page")));
    }
    parts.join(" · ")
}

fn pluralize(count: u64, singular: &str) -> String {
    if count == 1 {
        singular.to_string()
    } else {
        format!("{singular}s")
    }
}

fn estimate_page_total(assets: &[Asset]) -> Option<u64> {
    let meta_max = assets
        .iter()
        .filter_map(|asset| {
            asset
                .meta
                .get("page_total")
                .and_then(|value| value.as_u64())
        })
        .max();
    if meta_max.is_some() {
        return meta_max;
    }

    let mut page_indexes: HashSet<u32> = HashSet::new();
    for asset in assets {
        if let Some(idx) = asset.page_index {
            page_indexes.insert(idx);
        }
    }
    if !page_indexes.is_empty() {
        return Some(page_indexes.len() as u64);
    }

    let mut seen: HashSet<PathBuf> = HashSet::new();
    let mut max_pages = None;
    for asset in assets {
        if asset.media == "pdf" && seen.insert(asset.path.clone()) {
            if let Ok(count) = pdf::page_count(&asset.path) {
                let count = count as u64;
                if max_pages.map_or(true, |current| count > current) {
                    max_pages = Some(count);
                }
            }
        }
    }
    max_pages
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
