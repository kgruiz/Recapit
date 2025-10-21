mod cli;
mod config;
mod constants;
mod conversion;
mod core;
mod cost;
mod engine;
mod ingest;
mod pdf;
mod prompts;
mod providers;
mod quota;
mod render;
mod telemetry;
mod templates;
mod tui;
mod utils;
mod video;

use anyhow::{anyhow, bail, Context};
use clap::Parser;
use conversion::{collect_tex_files, LatexConverter};
use core::{Ingestor, Job, Kind, Normalizer, PdfMode};
use crossterm::style::Stylize;
use engine::{Engine, Progress, ProgressKind};
use ingest::{CompositeIngestor, CompositeNormalizer};
use providers::gemini::GeminiProvider;
use quota::{QuotaConfig, QuotaMonitor};
use render::writer::LatexWriter;
use serde_json::{json, Map, Value};
use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use telemetry::RequestEvent;
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;
use tokio::sync::mpsc;
use walkdir::WalkDir;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let cli = cli::Cli::parse();
    match cli.cmd {
        cli::Command::Summarize {
            source,
            output_dir,
            kind,
            pdf_mode,
            model,
            recursive,
            no_recursive,
            skip_existing,
            export,
            preset,
            media_resolution,
            config,
        } => {
            let cfg = config::AppConfig::load(config.as_deref())?;
            let presets = cfg.merged_presets();
            let available: BTreeSet<_> = presets.keys().cloned().collect();
            let preset_key = preset.to_lowercase();
            let preset_config = presets.get(&preset_key).ok_or_else(|| {
                let summary = if available.is_empty() {
                    "<none>".to_string()
                } else {
                    available.iter().cloned().collect::<Vec<_>>().join(", ")
                };
                anyhow::anyhow!("Unknown preset '{preset}'. Available presets: {summary}")
            })?;

            let effective_model = model
                .clone()
                .or_else(|| preset_string(preset_config, "model"))
                .unwrap_or_else(|| cfg.default_model.clone());

            let mut effective_kind = parse_kind(&kind);
            if effective_kind.is_none() {
                if let Some(preset_kind) = preset_string(preset_config, "kind") {
                    if let Some(parsed) = parse_kind(&preset_kind) {
                        effective_kind = Some(parsed);
                    } else {
                        bail!("Preset '{preset}' specified invalid kind '{preset_kind}'");
                    }
                }
            }

            let mut effective_pdf_mode = parse_pdf_mode(&pdf_mode);
            if matches!(effective_pdf_mode, PdfMode::Auto) {
                if let Some(preset_pdf) = preset_string(preset_config, "pdf_mode") {
                    match preset_pdf.to_lowercase().as_str() {
                        "auto" => effective_pdf_mode = PdfMode::Auto,
                        "pdf" => effective_pdf_mode = PdfMode::Pdf,
                        "images" => effective_pdf_mode = PdfMode::Images,
                        other => bail!("Preset '{preset}' specified invalid pdf_mode '{other}'"),
                    }
                }
            }

            let recursive_override = preset_bool(preset_config, "recursive");
            let cli_recursive = if recursive {
                Some(true)
            } else if no_recursive {
                Some(false)
            } else {
                None
            };
            let effective_recursive = cli_recursive.or(recursive_override).unwrap_or(false);

            let mut effective_exports = if !export.is_empty() {
                sanitize_exports(&export)
            } else if let Some(values) = preset_string_list(preset_config, "exports") {
                sanitize_exports(&values)
            } else {
                sanitize_exports(&cfg.exports)
            };

            if effective_exports.is_empty() {
                effective_exports = sanitize_exports(&cfg.exports);
            }

            let media_candidate = media_resolution
                .clone()
                .or_else(|| preset_string(preset_config, "media_resolution"))
                .unwrap_or_else(|| cfg.media_resolution.clone());
            let effective_media_resolution = normalize_media_resolution(&media_candidate)?;

            let (tx, rx) = mpsc::unbounded_channel::<Progress>();

            let tui_handle = tokio::spawn(tui::run_tui(rx));

            let monitor = telemetry::RunMonitor::new();
            let request_limits = crate::constants::rate_limits_per_minute()
                .into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .collect();
            let token_limits = crate::constants::token_limits_per_minute()
                .into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .collect();
            let quota = QuotaMonitor::new(QuotaConfig::new(request_limits, token_limits));

            let capability_table = crate::constants::model_capabilities();
            let model_key = effective_model.clone();
            let capability_checker = move |capability: &str| {
                capability_table
                    .get(model_key.as_str())
                    .or_else(|| capability_table.get(crate::constants::DEFAULT_MODEL))
                    .map(|caps| caps.iter().any(|c| *c == capability))
                    .unwrap_or(true)
            };

            let provider = GeminiProvider::new(
                cfg.api_key.clone(),
                effective_model.clone(),
                monitor.clone(),
                Some(quota.clone()),
            );
            let normalizer = CompositeNormalizer::new(
                None,
                None,
                cfg.video_encoder_preference,
                Some(cfg.video_max_chunk_seconds),
                Some(cfg.video_max_chunk_bytes),
                cfg.video_token_limit,
                Some(cfg.video_tokens_per_second),
                Some(Box::new(capability_checker)),
            )?;
            let ingestor = CompositeIngestor::new()?;
            let cost = cost::CostEstimator::from_path(
                cfg.pricing_file.as_deref(),
                cfg.pricing_defaults.clone(),
            )?;
            let mut engine = Engine::new(
                Box::new(ingestor),
                Box::new(normalizer),
                Box::new(provider),
                Box::new(LatexWriter::new()),
                tx.clone(),
                monitor.clone(),
                cost,
                &cfg,
            )?;

            tx.send(Progress {
                task: "bootstrap".into(),
                kind: ProgressKind::Discover,
                current: 1,
                total: 3,
                status: "init".into(),
            })
            .ok();

            let job = Job {
                source: source.clone(),
                recursive: effective_recursive,
                kind: effective_kind,
                pdf_mode: effective_pdf_mode,
                output_dir: output_dir.clone().or_else(|| cfg.output_dir.clone()),
                model: effective_model.clone(),
                preset: Some(preset_key.clone()),
                export: effective_exports,
                skip_existing,
                media_resolution: Some(effective_media_resolution.clone()),
            };

            let result = engine.run(&job).await?;
            if let Some(path) = result {
                tx.send(Progress {
                    task: "done".into(),
                    kind: ProgressKind::Write,
                    current: 1,
                    total: 1,
                    status: path.display().to_string(),
                })
                .ok();
            }

            tui_handle.await??;
        }
        cli::Command::Plan {
            source,
            kind,
            pdf_mode,
            model,
            recursive,
            json,
            config,
        } => {
            handle_planner_plan(
                &source,
                recursive,
                &kind,
                &pdf_mode,
                model.as_deref(),
                config.as_deref(),
                json,
            )?;
        }
        cli::Command::Convert { command } => match command {
            cli::ConvertCommand::LatexToMd {
                source,
                output_dir,
                file_pattern,
                skip_existing,
                model,
                recursive,
            } => run_latex_conversion(
                source,
                output_dir,
                file_pattern,
                skip_existing,
                model,
                recursive,
                ConversionKind::Markdown,
            )?,
            cli::ConvertCommand::LatexToJson {
                source,
                output_dir,
                file_pattern,
                skip_existing,
                model,
                recursive,
            } => run_latex_conversion(
                source,
                output_dir,
                file_pattern,
                skip_existing,
                model,
                recursive,
                ConversionKind::Json,
            )?,
        },
        cli::Command::Init { path, force } => {
            handle_init(&path, force)?;
        }
        cli::Command::Planner { command } => match command {
            cli::PlannerCommand::Plan {
                source,
                recursive,
                kind,
                pdf_mode,
                model,
                json,
                config,
            } => {
                handle_planner_plan(
                    &source,
                    recursive,
                    &kind,
                    &pdf_mode,
                    model.as_deref(),
                    config.as_deref(),
                    json,
                )?;
            }
            cli::PlannerCommand::Ingest {
                source,
                recursive,
                json,
                config,
            } => {
                handle_planner_ingest(&source, recursive, config.as_deref(), json)?;
            }
        },
        cli::Command::Report { command } => match command {
            cli::ReportCommand::Cost {
                summary,
                events,
                pricing,
                json,
            } => {
                handle_report_cost(&summary, &events, pricing.as_deref(), json)?;
            }
        },
        cli::Command::Cleanup { command } => match command {
            cli::CleanupCommand::Caches { dry_run } => handle_cleanup_caches(dry_run)?,
            cli::CleanupCommand::Artifacts { root, dry_run } => {
                handle_cleanup_artifacts(&root, dry_run)?;
            }
        },
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

fn preset_string(map: &Map<String, Value>, key: &str) -> Option<String> {
    map.get(key).and_then(|value| match value {
        Value::String(s) => {
            let trimmed = s.trim();
            if trimmed.is_empty() {
                None
            } else {
                Some(trimmed.to_string())
            }
        }
        Value::Number(num) => Some(num.to_string()),
        Value::Bool(flag) => Some(flag.to_string()),
        _ => None,
    })
}

fn preset_bool(map: &Map<String, Value>, key: &str) -> Option<bool> {
    map.get(key).and_then(|value| match value {
        Value::Bool(flag) => Some(*flag),
        Value::String(s) => match s.trim().to_lowercase().as_str() {
            "true" | "1" | "yes" | "on" => Some(true),
            "false" | "0" | "no" | "off" => Some(false),
            _ => None,
        },
        _ => None,
    })
}

fn preset_string_list(map: &Map<String, Value>, key: &str) -> Option<Vec<String>> {
    map.get(key).and_then(|value| match value {
        Value::String(s) => {
            let trimmed = s.trim();
            if trimmed.is_empty() {
                Some(Vec::new())
            } else {
                Some(vec![trimmed.to_string()])
            }
        }
        Value::Array(items) => {
            let mut result = Vec::new();
            for item in items {
                if let Some(text) = item.as_str() {
                    let trimmed = text.trim();
                    if !trimmed.is_empty() {
                        result.push(trimmed.to_string());
                    }
                }
            }
            Some(result)
        }
        _ => None,
    })
}

fn sanitize_exports(values: &[String]) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut result = Vec::new();
    for value in values {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            continue;
        }
        let normalized = trimmed.to_lowercase();
        if seen.insert(normalized.clone()) {
            result.push(normalized);
        }
    }
    result
}

fn normalize_media_resolution(value: &str) -> anyhow::Result<String> {
    let normalized = value.trim().to_lowercase();
    match normalized.as_str() {
        "default" | "low" => Ok(normalized),
        other => bail!("invalid media resolution '{other}'"),
    }
}

enum ConversionKind {
    Markdown,
    Json,
}

fn run_latex_conversion(
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
    let default_model =
        model_override.unwrap_or_else(|| constants::GEMINI_2_FLASH_THINKING_EXP.to_string());

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

    let files = collect_tex_files(&source, &file_pattern, recursive)?;
    if files.is_empty() {
        println!("No files matched pattern {}", file_pattern);
        return Ok(());
    }

    let prompt_markdown = loader.latex_to_md_prompt();
    let prompt_json = loader.latex_to_json_prompt();

    for tex_file in files {
        let latex = fs::read_to_string(&tex_file)
            .with_context(|| format!("reading {}", tex_file.display()))?;

        let mut metadata = Map::new();
        metadata.insert(
            "source".into(),
            Value::String(tex_file.to_string_lossy().to_string()),
        );

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
                    &latex,
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
                let text =
                    converter.latex_to_json(&default_model, &prompt_json, &latex, metadata)?;
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

fn handle_init(path: &Path, force: bool) -> anyhow::Result<()> {
    let target = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };
    if target.exists() && !force {
        bail!(
            "{} already exists; rerun with --force to overwrite",
            target.display()
        );
    }
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("creating directory {}", parent.display()))?;
    }
    fs::write(&target, DEFAULT_CONFIG_TEMPLATE)
        .with_context(|| format!("writing {}", target.display()))?;
    println!("Wrote {}", target.display());
    Ok(())
}

fn handle_planner_plan(
    source: &str,
    recursive: bool,
    kind: &str,
    pdf_mode: &str,
    model: Option<&str>,
    config: Option<&Path>,
    json_output: bool,
) -> anyhow::Result<()> {
    let cfg = config::AppConfig::load(config)?;
    let effective_kind = parse_kind(kind);
    let pdf_mode_value = parse_pdf_mode(pdf_mode);
    let effective_model = model
        .map(|m| m.to_string())
        .unwrap_or_else(|| cfg.default_model.clone());
    let job = Job {
        source: source.to_string(),
        recursive,
        kind: effective_kind,
        pdf_mode: pdf_mode_value,
        output_dir: cfg.output_dir.clone(),
        model: effective_model.clone(),
        preset: None,
        export: cfg.exports.clone(),
        skip_existing: true,
        media_resolution: Some(cfg.media_resolution.clone()),
    };

    let ingestor = CompositeIngestor::new()?;
    let mut normalizer = CompositeNormalizer::new(
        None,
        None,
        cfg.video_encoder_preference,
        Some(cfg.video_max_chunk_seconds),
        Some(cfg.video_max_chunk_bytes),
        cfg.video_token_limit,
        Some(cfg.video_tokens_per_second),
        Some(Box::new(|_| true)),
    )?;
    normalizer.prepare(&job)?;
    let assets = ingestor.discover(&job)?;
    let modality = determine_modality(&assets).unwrap_or_else(|| "unknown".to_string());
    let normalized = normalizer.normalize(&assets, pdf_mode_value)?;
    let chunk_info = normalizer.chunk_descriptors();
    let artifact_paths = normalizer.artifact_paths();

    if json_output {
        let assets_json: Vec<Value> = assets
            .iter()
            .map(|asset| {
                json!({
                    "path": asset.path.to_string_lossy(),
                    "media": asset.media,
                    "source_kind": format!("{:?}", asset.source_kind),
                    "page_index": asset.page_index,
                })
            })
            .collect();
        let payload = json!({
            "job": {
                "source": job.source,
                "recursive": job.recursive,
                "kind": job.kind.map(|k| k.as_str()),
                "pdf_mode": format_pdf_mode(pdf_mode_value),
                "model": job.model,
                "media_resolution": job.media_resolution,
            },
            "asset_count": assets.len(),
            "assets": assets_json,
            "normalized_assets": normalized.len(),
            "chunk_count": chunk_info.len(),
            "chunks": chunk_info,
            "modality": modality,
            "artifacts": artifact_paths
                .iter()
                .map(|path| path.to_string_lossy().to_string())
                .collect::<Vec<_>>(),
        });
        println!("{}", serde_json::to_string_pretty(&payload)?);
        return Ok(());
    }

    println!("{}", "Planner report".bold());
    println!("Source: {}", job.source);
    println!(
        "Kind: {}",
        job.kind
            .map(|k| k.as_str().to_string())
            .unwrap_or_else(|| "auto".into())
    );
    println!("PDF mode: {}", format_pdf_mode(pdf_mode_value));
    println!("Model: {}", job.model);
    println!("Recursive: {}", if job.recursive { "yes" } else { "no" });
    println!("Modality: {}", modality);
    println!("Assets discovered: {}", assets.len());
    for asset in assets.iter().take(10) {
        println!("  - {} {}", asset.media, asset.path.display());
    }
    if assets.len() > 10 {
        println!("  ... {} more", assets.len() - 10);
    }
    println!("Normalized assets: {}", normalized.len());
    println!("Chunks planned: {}", chunk_info.len());
    if let Some(manifest) = artifact_paths.first() {
        println!("Manifest: {}", manifest.display());
    }
    Ok(())
}

fn handle_planner_ingest(
    source: &str,
    recursive: bool,
    config: Option<&Path>,
    json_output: bool,
) -> anyhow::Result<()> {
    let cfg = config::AppConfig::load(config)?;
    let job = Job {
        source: source.to_string(),
        recursive,
        kind: None,
        pdf_mode: PdfMode::Auto,
        output_dir: cfg.output_dir.clone(),
        model: cfg.default_model.clone(),
        preset: None,
        export: cfg.exports.clone(),
        skip_existing: true,
        media_resolution: Some(cfg.media_resolution.clone()),
    };
    let ingestor = CompositeIngestor::new()?;
    let assets = ingestor.discover(&job)?;
    if json_output {
        let assets_json: Vec<Value> = assets
            .iter()
            .map(|asset| {
                json!({
                    "path": asset.path.to_string_lossy(),
                    "media": asset.media,
                    "source_kind": format!("{:?}", asset.source_kind),
                    "page_index": asset.page_index,
                })
            })
            .collect();
        let payload = json!({
            "job": {
                "source": job.source,
                "recursive": job.recursive,
            },
            "asset_count": assets.len(),
            "assets": assets_json,
        });
        println!("{}", serde_json::to_string_pretty(&payload)?);
        return Ok(());
    }

    println!("{}", "Ingestion preview".bold());
    println!("Source: {}", job.source);
    println!("Recursive: {}", if job.recursive { "yes" } else { "no" });
    println!("Assets discovered: {}", assets.len());
    for asset in assets.iter().take(15) {
        println!("  - {} {}", asset.media, asset.path.display());
    }
    if assets.len() > 15 {
        println!("  ... {} more", assets.len() - 15);
    }
    Ok(())
}

fn handle_report_cost(
    summary_path: &Path,
    events_path: &Path,
    pricing_path: Option<&Path>,
    json_output: bool,
) -> anyhow::Result<()> {
    let events = load_request_events(events_path)?;
    if events.is_empty() {
        println!("No events found in {}", events_path.display());
        return Ok(());
    }

    let defaults = crate::constants::default_model_pricing()
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .collect();
    let estimator = cost::CostEstimator::from_path(pricing_path, defaults)?;
    let summary = estimator.estimate(&events);
    let total_input_tokens: u64 = summary
        .per_model
        .values()
        .map(|bucket| bucket.input_tokens)
        .sum();
    let total_output_tokens: u64 = summary
        .per_model
        .values()
        .map(|bucket| bucket.output_tokens)
        .sum();

    let summary_metadata = fs::read_to_string(summary_path)
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok());

    if json_output {
        let per_model: Vec<Value> = summary
            .per_model
            .iter()
            .map(|(model, breakdown)| {
                json!({
                    "model": model,
                    "input_cost_usd": breakdown.input_cost,
                    "output_cost_usd": breakdown.output_cost,
                    "total_cost_usd": breakdown.total_cost,
                    "input_tokens": breakdown.input_tokens,
                    "output_tokens": breakdown.output_tokens,
                })
            })
            .collect();
        let payload = json!({
            "summary": summary_metadata,
            "cost": {
                "events": events.len(),
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "input_cost_usd": summary.total_input_cost,
                "output_cost_usd": summary.total_output_cost,
                "total_cost_usd": summary.total_cost,
                "estimated": summary.estimated,
                "per_model": per_model,
            }
        });
        println!("{}", serde_json::to_string_pretty(&payload)?);
        return Ok(());
    }

    println!("{}", "Cost report".bold());
    if let Some(meta) = summary_metadata.as_ref() {
        if let Some(job) = meta.get("job").and_then(|v| v.as_object()) {
            if let Some(source) = job.get("source").and_then(|v| v.as_str()) {
                println!("Source: {}", source);
            }
            if let Some(model) = job.get("model").and_then(|v| v.as_str()) {
                println!("Model: {}", model);
            }
        }
    }
    println!("Events: {}", events.len());
    println!("Input tokens: {}", total_input_tokens);
    println!("Output tokens: {}", total_output_tokens);
    println!(
        "Total cost: {}",
        format!("${:.6}", summary.total_cost).green()
    );
    if summary.estimated {
        println!("{}", "Note: totals include estimated token counts".yellow());
    }

    let mut per_model: Vec<_> = summary.per_model.iter().collect();
    per_model.sort_by(|a, b| a.0.cmp(b.0));
    for (model, breakdown) in per_model {
        println!(
            "  {} -> in ${:.6} / out ${:.6} (tokens in: {}, out: {})",
            model.as_str().bold(),
            breakdown.input_cost,
            breakdown.output_cost,
            breakdown.input_tokens,
            breakdown.output_tokens
        );
    }
    Ok(())
}

fn load_request_events(path: &Path) -> anyhow::Result<Vec<RequestEvent>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let file = fs::File::open(path).with_context(|| format!("opening {}", path.display()))?;
    let reader = BufReader::new(file);
    let mut events = Vec::new();
    for line in reader.lines() {
        let line = line?;
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let value: Value = serde_json::from_str(trimmed)
            .with_context(|| format!("parsing event from {}", path.display()))?;
        events.push(request_event_from_value(value)?);
    }
    Ok(events)
}

fn request_event_from_value(value: Value) -> anyhow::Result<RequestEvent> {
    let model = value
        .get("model")
        .and_then(|v| v.as_str())
        .ok_or_else(|| anyhow!("event missing model"))?
        .to_string();
    let modality = value
        .get("modality")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();
    let started_at = value
        .get("start_utc")
        .and_then(|v| v.as_str())
        .and_then(|s| OffsetDateTime::parse(s, &Rfc3339).ok())
        .unwrap_or(OffsetDateTime::UNIX_EPOCH);
    let finished_at = value
        .get("end_utc")
        .and_then(|v| v.as_str())
        .and_then(|s| OffsetDateTime::parse(s, &Rfc3339).ok())
        .unwrap_or(started_at);
    let input_tokens = value
        .get("tokens_in")
        .and_then(|v| v.as_u64())
        .map(|v| v as u32);
    let output_tokens = value
        .get("tokens_out")
        .and_then(|v| v.as_u64())
        .map(|v| v as u32);
    let total_tokens = match (input_tokens, output_tokens) {
        (Some(input), Some(output)) => Some(input.saturating_add(output)),
        _ => None,
    };

    let mut metadata = HashMap::new();
    for key in [
        "chunk_index",
        "manifest_path",
        "response_path",
        "file_uri",
        "video_start",
        "video_end",
    ] {
        if let Some(item) = value.get(key) {
            metadata.insert(key.to_string(), item.clone());
        }
    }

    Ok(RequestEvent {
        model,
        modality,
        started_at,
        finished_at,
        input_tokens,
        output_tokens,
        total_tokens,
        metadata,
    })
}

fn handle_cleanup_caches(dry_run: bool) -> anyhow::Result<()> {
    let temp_dir = std::env::temp_dir();
    let mut targets = Vec::new();
    if let Ok(entries) = fs::read_dir(&temp_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            if let Some(name) = path.file_name().and_then(|s| s.to_str()) {
                if name.starts_with("recapit-pdf-pages") || name.starts_with("recapit-video") {
                    targets.push(path);
                }
            }
        }
    }

    if targets.is_empty() {
        println!("No cached directories found in {}", temp_dir.display());
        return Ok(());
    }

    for path in targets {
        if dry_run {
            println!("Would remove {}", path.display());
        } else {
            fs::remove_dir_all(&path).with_context(|| format!("removing {}", path.display()))?;
            println!("Removed {}", path.display());
        }
    }
    Ok(())
}

fn handle_cleanup_artifacts(root: &Path, dry_run: bool) -> anyhow::Result<()> {
    let mut targets = Vec::new();
    for entry in WalkDir::new(root).into_iter().flatten() {
        if entry.file_type().is_dir() {
            let name = entry.file_name().to_string_lossy();
            if matches!(
                name.as_ref(),
                "full-response" | "page-images" | "pickles" | "video-chunks" | "manifests"
            ) {
                targets.push(entry.into_path());
            }
        }
    }

    if targets.is_empty() {
        println!("No artifacts found under {}", root.display());
        return Ok(());
    }

    targets.sort();
    targets.dedup();
    for path in targets {
        if dry_run {
            println!("Would remove {}", path.display());
        } else {
            fs::remove_dir_all(&path).with_context(|| format!("removing {}", path.display()))?;
            println!("Removed {}", path.display());
        }
    }
    Ok(())
}

fn determine_modality(assets: &[core::Asset]) -> Option<String> {
    assets.first().map(|asset| match asset.media.as_str() {
        "video" | "audio" => "video".to_string(),
        "pdf" => "pdf".to_string(),
        _ => "image".to_string(),
    })
}

fn format_pdf_mode(mode: PdfMode) -> &'static str {
    match mode {
        PdfMode::Auto => "auto",
        PdfMode::Images => "images",
        PdfMode::Pdf => "pdf",
    }
}

const DEFAULT_CONFIG_TEMPLATE: &str = "# Recapit configuration\n\
# Adjust defaults for the summarize command.\n\
# Available presets live under presets.<name>.\n\
\n\
defaults:\n\
  model: \"gemini-2.0-flash\"\n\
  output_dir: \"output\"\n\
  exports: [\"srt\"]\n\
\n\
save:\n\
  full_response: false\n\
  intermediates: true\n\
\n\
video:\n\
  token_limit: 300000\n\
  tokens_per_second: 300\n\
  max_chunk_seconds: 7200\n\
  max_chunk_bytes: 524288000\n\
  encoder: \"auto\"\n\
  media_resolution: \"default\"\n\
\n\
presets:\n\
  speed:\n\
    pdf_mode: \"images\"\n\
  quality:\n\
    pdf_mode: \"pdf\"\n";
