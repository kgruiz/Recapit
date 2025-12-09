mod cli;
mod config;
mod constants;
mod conversion;
mod core;
mod cost;
mod engine;
mod ingest;
mod pdf;
mod progress;
mod prompts;
mod providers;
mod quota;
mod render;
mod telemetry;
mod templates;
mod tui;
mod utils;
mod video;

use anyhow::{anyhow, Context};
use clap::Parser;
use cli::{ConversionTarget, OutputFormatArg};
use conversion::{collect_tex_files, LatexConverter};
use core::{Asset, Ingestor, Job, Kind, Normalizer, OutputFormat, PdfMode};
use crossterm::style::Stylize;
use engine::Engine;
use ingest::{CompositeIngestor, CompositeNormalizer};
use progress::{Progress, ProgressScope, ProgressStage};
use providers::gemini::GeminiProvider;
use quota::{QuotaConfig, QuotaMonitor};
use render::writer::CompositeWriter;
use serde_json::{json, Map, Value};
use serde_yaml::Value as YamlValue;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use tokio::sync::mpsc;
use utils::slugify;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let cli = cli::Cli::parse();

    match &cli.cmd {
        Some(cli::Command::Report { command }) => match command {
            cli::ReportCommand::Cost { input, json } => run_report_cost(input, *json)?,
        },
        Some(cli::Command::Cleanup { command }) => match command {
            cli::CleanupCommand::Cache { dry_run, yes } => run_cleanup_cache(*dry_run, *yes)?,
            cli::CleanupCommand::Downloads { path, dry_run, yes } => {
                run_cleanup_downloads(path, *dry_run, *yes)?
            }
        },
        None => run_primary(cli).await?,
    }

    Ok(())
}

fn merged_presets(cfg: &config::AppConfig) -> HashMap<String, HashMap<String, YamlValue>> {
    let mut presets: HashMap<String, HashMap<String, YamlValue>> = HashMap::new();
    presets.insert("basic".into(), HashMap::new());
    presets.insert(
        "speed".into(),
        HashMap::from([("pdf_mode".into(), YamlValue::String("images".into()))]),
    );
    presets.insert(
        "quality".into(),
        HashMap::from([("pdf_mode".into(), YamlValue::String("pdf".into()))]),
    );

    for (key, value) in cfg.presets.clone() {
        presets.insert(key.to_lowercase(), value);
    }

    presets
}

fn resolve_media_resolution(value: Option<&str>) -> anyhow::Result<(String, Option<String>)> {
    let default_value = "default".to_string();
    let Some(raw) = value else {
        return Ok((default_value.clone(), Some(default_value)));
    };

    let normalized = raw.to_lowercase();
    let allowed = ["default", "low", "medium", "high", "unspecified", ""];
    if !allowed.contains(&normalized.as_str()) {
        return Err(anyhow!(format!("Invalid media resolution '{}'.", raw)));
    }

    if normalized.is_empty() {
        return Ok((default_value.clone(), Some(default_value)));
    }

    Ok((normalized.clone(), Some(normalized)))
}

async fn run_primary(cli: cli::Cli) -> anyhow::Result<()> {
    let sources = if cli.source.is_empty() {
        return Err(anyhow!(
            "A source path or URL is required unless using a subcommand"
        ));
    } else {
        cli.source.clone()
    };

    // Handle conversion-first flow (single source only)
    if let Some(target) = cli.to {
        let source = sources
            .get(0)
            .ok_or_else(|| anyhow!("A source path is required for conversion"))?;
        let default_pattern = match cli.from {
            cli::ConversionSource::Latex => "*.tex".to_string(),
            cli::ConversionSource::Markdown => "*.md".to_string(),
            cli::ConversionSource::Auto => cli.file_pattern.clone(),
        };
        let pattern = if cli.file_pattern != "*.tex" {
            cli.file_pattern.clone()
        } else {
            default_pattern
        };
        let kind = match target {
            ConversionTarget::Markdown => ConversionKind::Markdown,
            ConversionTarget::Json => ConversionKind::Json,
        };
        return run_conversion(
            PathBuf::from(source),
            cli.output_dir.clone(),
            pattern,
            cli.skip_existing,
            cli.model.clone(),
            if cli.no_recursive {
                false
            } else {
                cli.recursive
            },
            kind,
        );
    }

    let cfg = config::AppConfig::load(cli.config.as_deref())?;
    let presets = merged_presets(&cfg);
    let preset_key = cli.preset.to_lowercase();
    let preset_config = presets.get(&preset_key).ok_or_else(|| {
        anyhow!(
            "Unknown preset '{}'. Available presets: {}",
            cli.preset,
            presets.keys().cloned().collect::<Vec<_>>().join(", ")
        )
    })?;

    let cli_recursive = if cli.no_recursive {
        Some(false)
    } else if cli.recursive {
        Some(true)
    } else {
        None
    };

    let mut exports = if cli.export.is_empty() {
        cfg.exports.clone()
    } else {
        cli.export.clone()
    };
    if let Some(preset_exports) = preset_config
        .get("exports")
        .and_then(|value| value.as_sequence())
    {
        for value in preset_exports {
            if let Some(item) = value.as_str() {
                exports.push(item.to_string());
            }
        }
    }
    exports.retain(|value| !value.trim().is_empty());
    exports.sort();
    exports.dedup();

    let mut save_full_response = cfg.save_full_response;
    if let Some(value) = preset_config
        .get("save_full_response")
        .and_then(|v| v.as_bool())
    {
        save_full_response = value;
    }
    let mut save_intermediates = cfg.save_intermediates;
    if let Some(value) = preset_config
        .get("save_intermediates")
        .and_then(|v| v.as_bool())
    {
        save_intermediates = value;
    }

    if cli.dry_run {
        let source = sources.first().unwrap();
        let job = Job {
            source: source.clone(),
            job_label: source.clone(),
            job_id: slugify(source),
            job_index: 0,
            job_total: 1,
            recursive: cli_recursive
                .or_else(|| {
                    preset_config
                        .get("recursive")
                        .and_then(|value| value.as_bool())
                })
                .unwrap_or(false),
            kind: parse_kind(&cli.kind).or_else(|| {
                preset_config
                    .get("kind")
                    .and_then(|value| value.as_str())
                    .and_then(parse_kind)
            }),
            pdf_mode: parse_pdf_mode(&cli.pdf_mode),
            output_dir: cli.output_dir.clone(),
            model: cli
                .model
                .clone()
                .unwrap_or_else(|| cfg.default_model.clone()),
            preset: Some(preset_key.clone()),
            export: exports.clone(),
            format: cli
                .format
                .map(|v| match v {
                    OutputFormatArg::Markdown => OutputFormat::Markdown,
                    OutputFormatArg::Latex => OutputFormat::Latex,
                })
                .unwrap_or(cfg.default_format),
            skip_existing: cli.skip_existing,
            media_resolution: resolve_media_resolution(Some(cfg.media_resolution.as_str()))?.1,
            save_full_response,
            save_intermediates,
            save_metadata: cli.save_metadata,
            max_workers: cfg.max_workers,
            max_video_workers: cfg.max_video_workers,
            pdf_dpi: cfg.pdf_dpi,
        };
        return run_plan(&cfg, job, cli.json);
    }

    let (tx, rx) = mpsc::unbounded_channel::<Progress>();
    let (cancel_tx, mut cancel_rx) = mpsc::unbounded_channel::<()>();
    let tui_handle = if cli.quiet {
        None
    } else {
        Some(tokio::spawn(tui::run_tui(rx, cancel_tx.clone())))
    };

    let request_limits = crate::constants::rate_limits_per_minute()
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .collect();
    let token_limits = crate::constants::token_limits_per_minute()
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .collect();
    let quota = QuotaMonitor::new(QuotaConfig::new(request_limits, token_limits));

    let cost =
        cost::CostEstimator::from_path(cfg.pricing_file.as_deref(), cfg.pricing_defaults.clone())?;
    let summary_cost =
        cost::CostEstimator::from_path(cfg.pricing_file.as_deref(), cfg.pricing_defaults.clone())?;

    let media_candidate = cli
        .media_resolution
        .clone()
        .or_else(|| {
            preset_config
                .get("media_resolution")
                .and_then(|value| value.as_str())
                .map(|s| s.to_string())
        })
        .unwrap_or_else(|| cfg.media_resolution.clone());
    let (media_label, media_enum) = resolve_media_resolution(Some(media_candidate.as_str()))?;
    let cli_format_arg = cli.format.clone();

    let mut tokens_per_second = cfg.video_tokens_per_second;
    if media_label == "low" && tokens_per_second > 100.0 {
        tokens_per_second = 100.0;
    }

    let total_jobs = sources.len();
    tx.send(Progress {
        scope: ProgressScope::Run,
        stage: ProgressStage::Discover,
        current: 0,
        total: total_jobs as u64,
        status: "start".into(),
        finished: false,
    })
    .ok();

    let mut summaries = Vec::new();

    for (idx, source) in sources.iter().enumerate() {
        let job_label = source.clone();
        let job_id = slugify(&job_label);

        let cli_kind = parse_kind(&cli.kind);
        let effective_kind = if cli_kind.is_some() {
            cli_kind
        } else {
            preset_config
                .get("kind")
                .and_then(|value| value.as_str())
                .and_then(parse_kind)
        };

        let mut effective_pdf_mode = parse_pdf_mode(&cli.pdf_mode);
        if matches!(effective_pdf_mode, PdfMode::Auto) {
            if let Some(preset_pdf) = preset_config
                .get("pdf_mode")
                .and_then(|value| value.as_str())
            {
                effective_pdf_mode = parse_pdf_mode(preset_pdf);
            }
        }

        let mut effective_pdf_dpi = cfg.pdf_dpi;
        if let Some(value) = preset_config
            .get("pdf_dpi")
            .and_then(|value| value.as_u64())
            .and_then(|value| u32::try_from(value).ok())
        {
            if value > 0 {
                effective_pdf_dpi = value;
            }
        }
        if let Some(value) = cli.pdf_dpi {
            if value > 0 {
                effective_pdf_dpi = value;
            }
        }

        let effective_model = cli
            .model
            .clone()
            .or_else(|| {
                preset_config
                    .get("model")
                    .and_then(|value| value.as_str())
                    .map(|s| s.to_string())
            })
            .unwrap_or_else(|| cfg.default_model.clone());

        let cli_format = cli_format_arg.clone().map(|value| match value {
            OutputFormatArg::Markdown => OutputFormat::Markdown,
            OutputFormatArg::Latex => OutputFormat::Latex,
        });
        let preset_format = preset_config
            .get("format")
            .and_then(|value| value.as_str())
            .and_then(OutputFormat::from_str);
        let effective_format = cli_format.or(preset_format).unwrap_or(cfg.default_format);

        let effective_recursive = cli_recursive
            .or_else(|| {
                preset_config
                    .get("recursive")
                    .and_then(|value| value.as_bool())
            })
            .unwrap_or(false);

        let mut max_workers = cfg.max_workers;
        if let Some(value) = preset_config.get("max_workers").and_then(|v| v.as_u64()) {
            if value > 0 {
                max_workers = value as usize;
            }
        }
        let mut max_video_workers = cfg.max_video_workers;
        if let Some(value) = preset_config
            .get("max_video_workers")
            .and_then(|v| v.as_u64())
        {
            if value > 0 {
                max_video_workers = value as usize;
            }
        }

        let job = Job {
            source: source.clone(),
            job_label: job_label.clone(),
            job_id: job_id.clone(),
            job_index: idx,
            job_total: total_jobs,
            recursive: effective_recursive,
            kind: effective_kind,
            pdf_mode: effective_pdf_mode,
            output_dir: cli.output_dir.clone(),
            model: effective_model.clone(),
            preset: Some(preset_key.clone()),
            export: exports.clone(),
            format: effective_format,
            skip_existing: cli.skip_existing,
            media_resolution: media_enum.clone(),
            save_full_response,
            save_intermediates,
            save_metadata: cli.save_metadata,
            max_workers,
            max_video_workers,
            pdf_dpi: effective_pdf_dpi,
        };

        let capability_table = crate::constants::model_capabilities();
        let model_key = job.model.clone();
        let capability_checker = move |capability: &str| {
            capability_table
                .get(model_key.as_str())
                .or_else(|| capability_table.get(crate::constants::DEFAULT_MODEL))
                .map(|caps| caps.iter().any(|c| *c == capability))
                .unwrap_or(true)
        };

        let monitor = telemetry::RunMonitor::new();
        let provider = GeminiProvider::new(
            cfg.api_key.clone(),
            job.model.clone(),
            monitor.clone(),
            Some(quota.clone()),
        )
        .with_progress(tx.clone());
        let normalizer = CompositeNormalizer::new(
            None,
            cfg.video_encoder_preference,
            Some(cfg.video_max_chunk_seconds),
            Some(cfg.video_max_chunk_bytes),
            cfg.video_token_limit,
            Some(tokens_per_second),
            Some(job.pdf_dpi),
            Some(Box::new(capability_checker)),
        )?;
        let ingestor = CompositeIngestor::new()?;
        let converter =
            LatexConverter::new(cfg.api_key.clone(), monitor.clone(), Some(quota.clone()))?;
        let mut engine = Engine::new(
            Box::new(ingestor),
            Box::new(normalizer),
            Box::new(provider),
            Box::new(CompositeWriter::new()),
            tx.clone(),
            monitor.clone(),
            cost.clone(),
            Some(converter),
            &cfg,
        )?;

        tx.send(Progress {
            scope: ProgressScope::Run,
            stage: ProgressStage::Discover,
            current: idx as u64,
            total: total_jobs as u64,
            status: job_label.clone(),
            finished: false,
        })
        .ok();

        let result = tokio::select! {
            res = engine.run(&job) => res,
            _ = cancel_rx.recv() => {
                println!("run cancelled by user (Ctrl+C)");
                break;
            }
            _ = tokio::signal::ctrl_c() => {
                println!("run cancelled by Ctrl+C");
                break;
            }
        };
        let result = match result {
            Ok(r) => r,
            Err(e) => return Err(e),
        };

        tx.send(Progress {
            scope: ProgressScope::Run,
            stage: ProgressStage::Write,
            current: (idx + 1) as u64,
            total: total_jobs as u64,
            status: job_label.clone(),
            finished: idx + 1 == total_jobs,
        })
        .ok();

        drop(engine);

        let summary = monitor.summarize();
        let costs = summary_cost.estimate(&monitor.events());
        summaries.push((job_label, result.clone(), summary, costs));
    }

    drop(tx);

    if let Some(handle) = tui_handle {
        handle.await??;
    }

    if !cli.quiet {
        let mut total_in = 0;
        let mut total_out = 0;
        let mut total_tokens = 0;
        let mut total_cost = 0.0;
        let mut total_time = 0.0;

        for (label, output, summary, costs) in &summaries {
            total_in += summary.total_input_tokens;
            total_out += summary.total_output_tokens;
            total_tokens += summary.total_tokens;
            total_cost += costs.total_cost;
            total_time += summary.total_duration_seconds;
            println!(
                "job {}: tokens in {} out {} total {} · est cost ${:.6} · elapsed {:.2}s{}",
                label,
                summary.total_input_tokens,
                summary.total_output_tokens,
                summary.total_tokens,
                costs.total_cost,
                summary.total_duration_seconds,
                output
                    .as_ref()
                    .map(|p| format!(" · output {}", p.display()))
                    .unwrap_or_default()
            );
        }
        if summaries.len() > 1 {
            println!(
                "total: tokens in {} out {} total {} · est cost ${:.6} · elapsed {:.2}s",
                total_in, total_out, total_tokens, total_cost, total_time
            );
        }
    }

    Ok(())
}

fn parse_kind(input: &str) -> Option<Kind> {
    match input.to_lowercase().as_str() {
        "slides" => Some(Kind::Slides),
        "lecture" => Some(Kind::Lecture),
        "document" => Some(Kind::Document),
        "image" => Some(Kind::Image),
        "video" => Some(Kind::Video),
        _ => None,
    }
}

fn parse_pdf_mode(input: &str) -> PdfMode {
    match input.to_lowercase().as_str() {
        "images" => PdfMode::Images,
        "pdf" => PdfMode::Pdf,
        _ => PdfMode::Auto,
    }
}

enum ConversionKind {
    Markdown,
    Json,
}

fn run_conversion(
    source: PathBuf,
    output_dir: Option<PathBuf>,
    file_pattern: String,
    skip_existing: bool,
    model_override: Option<String>,
    recursive: bool,
    kind: ConversionKind,
) -> anyhow::Result<()> {
    use std::fs;

    let cfg = config::AppConfig::load(None)?;
    let loader = templates::TemplateLoader::new(cfg.templates_dir.clone());
    let default_model = model_override.unwrap_or_else(|| constants::DEFAULT_MODEL.to_string());

    let request_limits = constants::rate_limits_per_minute()
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .collect();
    let token_limits = constants::token_limits_per_minute()
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .collect();
    let quota = QuotaMonitor::new(QuotaConfig::new(request_limits, token_limits));
    let monitor = telemetry::RunMonitor::new();
    let converter = LatexConverter::new(cfg.api_key.clone(), monitor, Some(quota))?;

    let mut files = collect_tex_files(&source, &file_pattern, recursive)?;
    if files.is_empty() && matches!(kind, ConversionKind::Json) && file_pattern == "*.tex" {
        files = collect_tex_files(&source, "*.md", recursive)?;
    }
    if files.is_empty() {
        println!("No files matched pattern {}", file_pattern);
        return Ok(());
    }

    let prompt_markdown = loader.latex_to_md_prompt();
    let prompt_json = loader.latex_to_json_prompt();
    let prompt_markdown_json = loader.markdown_to_json_prompt();

    for tex_file in files {
        let content = fs::read_to_string(&tex_file)
            .with_context(|| format!("reading {}", tex_file.display()))?;
        let extension = tex_file
            .extension()
            .and_then(|ext| ext.to_str())
            .unwrap_or_default()
            .to_lowercase();

        let mut metadata = Map::new();
        metadata.insert(
            "source".into(),
            Value::String(tex_file.to_string_lossy().to_string()),
        );
        metadata.insert("input_extension".into(), Value::String(extension.clone()));

        let output_root = output_dir
            .clone()
            .or_else(|| cfg.output_dir.clone())
            .unwrap_or_else(|| tex_file.parent().unwrap_or(Path::new(".")).to_path_buf());
        fs::create_dir_all(&output_root)?;

        match kind {
            ConversionKind::Markdown => {
                let metadata = metadata.clone();
                let out_path = output_root.join(format!(
                    "{}.md",
                    tex_file.file_stem().unwrap_or_default().to_string_lossy()
                ));
                if skip_existing && out_path.exists() {
                    continue;
                }
                let text = converter.latex_to_markdown(
                    &default_model,
                    &prompt_markdown,
                    &content,
                    metadata,
                )?;
                let mut value = text;
                if !value.ends_with('\n') {
                    value.push('\n');
                }
                fs::write(out_path, value)?;
            }
            ConversionKind::Json => {
                let metadata = metadata.clone();
                let out_path = output_root.join(format!(
                    "{}.json",
                    tex_file.file_stem().unwrap_or_default().to_string_lossy()
                ));
                if skip_existing && out_path.exists() {
                    continue;
                }
                let operation = extension.as_str();
                let text = match operation {
                    "tex" | "ltx" => {
                        converter.latex_to_json(&default_model, &prompt_json, &content, metadata)?
                    }
                    "md" | "markdown" | "mdown" => converter.markdown_to_json(
                        &default_model,
                        &prompt_markdown_json,
                        &content,
                        metadata,
                    )?,
                    _ => {
                        println!(
                            "Skipping {} (unsupported extension {})",
                            tex_file.display(),
                            extension
                        );
                        continue;
                    }
                };
                let mut value = text;
                if !value.ends_with('\n') {
                    value.push('\n');
                }
                fs::write(out_path, value)?;
            }
        }
    }

    Ok(())
}

fn run_plan(cfg: &config::AppConfig, job: Job, json_output: bool) -> anyhow::Result<()> {
    let (ingestor, mut normalizer) = build_ingestion_stack(cfg, &job.model, job.pdf_dpi)?;

    normalizer.prepare(&job)?;
    let assets = ingestor.discover(&job)?;
    let normalized = normalizer.normalize(&assets, job.pdf_mode)?;
    let final_kind = job.kind.unwrap_or_else(|| infer_kind_from_assets(&assets));
    let modality = modality_for_assets(&normalized);
    let chunks = normalizer.chunk_descriptors();

    let report = json!({
        "job": {
            "source": job.source,
            "recursive": job.recursive,
            "kind": final_kind.as_str(),
            "pdf_mode": pdf_mode_to_str(job.pdf_mode),
            "model": job.model,
            "preset": job.preset,
            "export": job.export,
            "skip_existing": job.skip_existing,
            "media_resolution": job.media_resolution,
            "format": job.format.as_str(),
            "pdf_dpi": job.pdf_dpi,
        },
        "kind": final_kind.as_str(),
        "modality": modality,
        "assets": assets.iter().map(asset_to_value).collect::<Vec<_>>(),
        "normalized": normalized
            .iter()
            .map(asset_to_value)
            .collect::<Vec<_>>(),
        "chunks": chunks,
    });

    if json_output {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else {
        print_plan_human(&report)?;
    }
    Ok(())
}

fn build_ingestion_stack(
    cfg: &config::AppConfig,
    model: &str,
    pdf_dpi: u32,
) -> anyhow::Result<(CompositeIngestor, CompositeNormalizer)> {
    let capability_table = constants::model_capabilities();
    let model_key = model.to_string();
    let capability_checker = move |capability: &str| {
        capability_table
            .get(model_key.as_str())
            .or_else(|| capability_table.get(constants::DEFAULT_MODEL))
            .map(|caps| caps.iter().any(|c| *c == capability))
            .unwrap_or(true)
    };

    let normalizer = CompositeNormalizer::new(
        None,
        cfg.video_encoder_preference,
        Some(cfg.video_max_chunk_seconds),
        Some(cfg.video_max_chunk_bytes),
        cfg.video_token_limit,
        Some(cfg.video_tokens_per_second),
        Some(pdf_dpi),
        Some(Box::new(capability_checker)),
    )?;
    let ingestor = CompositeIngestor::new()?;
    Ok((ingestor, normalizer))
}

fn asset_to_value(asset: &Asset) -> Value {
    let mut meta = Value::Null;
    if !asset.meta.is_null() {
        meta = asset.meta.clone();
    }
    json!({
        "path": asset.path.to_string_lossy(),
        "media": asset.media,
        "page_index": asset.page_index,
        "source_kind": format!("{:?}", asset.source_kind),
        "mime": asset.mime,
        "meta": meta,
    })
}

fn print_plan_human(report: &Value) -> anyhow::Result<()> {
    let job = report
        .get("job")
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default();
    let source = job
        .get("source")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    let kind = report
        .get("kind")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    let modality = report
        .get("modality")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    let assets = report
        .get("assets")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let chunks_len = report
        .get("chunks")
        .and_then(|v| v.as_array())
        .map(|arr| arr.len())
        .unwrap_or(0);

    println!("Source: {}", source);
    println!("Kind:   {}", kind);
    println!("Modality: {}", modality);
    println!("Assets: {}", assets.len());
    for asset in assets.iter().take(10) {
        let path = asset
            .get("path")
            .and_then(|v| v.as_str())
            .unwrap_or("<unknown>");
        let media = asset.get("media").and_then(|v| v.as_str()).unwrap_or("?");
        println!("  - {} ({})", path, media);
    }
    if assets.len() > 10 {
        println!("  ... {} more", assets.len() - 10);
    }
    println!("Chunks planned: {}", chunks_len);
    Ok(())
}

fn infer_kind_from_assets(assets: &[Asset]) -> Kind {
    if let Some(first) = assets.first() {
        match first.media.as_str() {
            "video" => Kind::Lecture,
            "image" => Kind::Slides,
            _ => Kind::Document,
        }
    } else {
        Kind::Document
    }
}

fn modality_for_assets(assets: &[Asset]) -> Option<String> {
    assets.first().map(|asset| match asset.media.as_str() {
        "video" | "audio" => "video".to_string(),
        "pdf" => "pdf".to_string(),
        _ => "image".to_string(),
    })
}

fn pdf_mode_to_str(mode: PdfMode) -> &'static str {
    match mode {
        PdfMode::Auto => "auto",
        PdfMode::Images => "images",
        PdfMode::Pdf => "pdf",
    }
}

fn expand_tilde(path: &Path) -> PathBuf {
    if let Some(raw) = path.to_str() {
        if let Some(stripped) = raw.strip_prefix("~/") {
            if let Some(home) = dirs::home_dir() {
                return home.join(stripped);
            }
        } else if raw == "~" {
            if let Some(home) = dirs::home_dir() {
                return home;
            }
        }
    }
    path.to_path_buf()
}

fn run_report_cost(path: &Path, json_output: bool) -> anyhow::Result<()> {
    let text = fs::read_to_string(path).with_context(|| format!("reading {}", path.display()))?;
    if json_output {
        println!("{}", text);
        return Ok(());
    }
    let summary: Value =
        serde_json::from_str(&text).with_context(|| format!("parsing {}", path.display()))?;

    let job = summary
        .get("job")
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default();
    let source = job
        .get("source")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    let model = job
        .get("model")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    let kind = job
        .get("kind")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");

    println!("{}", "Recapit Cost Report".bold());
    println!("Source: {}", source.cyan());
    println!("Kind:   {}", kind.cyan());
    println!("Model:  {}", model.cyan());

    let totals = summary
        .get("totals")
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default();
    let total_cost = totals
        .get("est_cost_usd")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
    let total_requests = totals.get("requests").and_then(|v| v.as_u64()).unwrap_or(0);
    let total_input_tokens = totals
        .get("input_tokens")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let total_output_tokens = totals
        .get("output_tokens")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);

    println!(
        "Total cost: {}",
        format!("${:.4}", total_cost).green().bold()
    );
    println!("Requests: {}", total_requests);
    println!(
        "Tokens: input {} | output {}",
        total_input_tokens, total_output_tokens
    );

    if let Some(by_model) = summary.get("by_model").and_then(|v| v.as_object()) {
        if !by_model.is_empty() {
            println!("\n{}", "Per-model usage:".bold());
            for (name, data) in by_model {
                let requests = data.get("requests").and_then(|v| v.as_u64()).unwrap_or(0);
                let tokens_in = data
                    .get("input_tokens")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0);
                let tokens_out = data
                    .get("output_tokens")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0);
                println!(
                    "  {} -> requests {}, tokens in {}, out {}",
                    name.as_str().magenta(),
                    requests,
                    tokens_in,
                    tokens_out
                );
            }
        }
    }

    if let Some(notes) = summary.get("notes").and_then(|v| v.as_array()) {
        println!("\n{}", "Notes:".bold());
        println!("  total: {}", notes.len());
        for note in notes.iter().take(5) {
            if let Some(name) = note.get("name").and_then(|v| v.as_str()) {
                println!("  - {}", name);
            }
        }
        if notes.len() > 5 {
            println!("  ... {} more", notes.len() - 5);
        }
    }

    Ok(())
}

fn run_cleanup_cache(dry_run: bool, yes: bool) -> anyhow::Result<()> {
    let Some(mut base) = dirs::cache_dir() else {
        println!("No cache directory available on this platform.");
        return Ok(());
    };
    base = base.join("recapit");
    if !base.exists() {
        println!("Cache directory not found: {}", base.display());
        return Ok(());
    }
    if !yes && !dry_run {
        anyhow::bail!(
            "Refusing to remove {}; pass --yes to confirm",
            base.display()
        );
    }
    if dry_run {
        println!("Would remove {}", base.display());
    } else {
        fs::remove_dir_all(&base)?;
        println!("Removed {}", base.display());
    }
    Ok(())
}

fn run_cleanup_downloads(path: &Path, dry_run: bool, yes: bool) -> anyhow::Result<()> {
    if !yes && !dry_run {
        anyhow::bail!("Refusing to remove downloads without --yes confirmation");
    }
    let expanded = expand_tilde(path);
    let targets = [expanded.join("downloads"), expanded.join("pickles")];
    let mut removed_any = false;
    for target in targets {
        if target.exists() {
            if dry_run {
                println!("Would remove {}", target.display());
            } else {
                fs::remove_dir_all(&target)?;
                println!("Removed {}", target.display());
            }
            removed_any = true;
        }
    }
    if !removed_any {
        println!("No cleanup targets found under {}", expanded.display());
    }
    Ok(())
}
