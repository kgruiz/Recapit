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

use anyhow::{anyhow, Context};
use clap::Parser;
use conversion::{collect_tex_files, LatexConverter};
use core::{Job, Kind, PdfMode};
use engine::{Engine, Progress, ProgressKind};
use ingest::{CompositeIngestor, CompositeNormalizer};
use providers::gemini::GeminiProvider;
use quota::{QuotaConfig, QuotaMonitor};
use render::writer::LatexWriter;
use serde_json::{Map, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use tokio::sync::mpsc;

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
            config,
            media_resolution,
        } => {
            let cfg = config::AppConfig::load(config.as_deref())?;
            let presets = merged_presets(&cfg);
            let preset_key = preset.to_lowercase();
            let preset_config = presets.get(&preset_key).ok_or_else(|| {
                anyhow!(
                    "Unknown preset '{}'. Available presets: {}",
                    preset,
                    presets.keys().cloned().collect::<Vec<_>>().join(", ")
                )
            })?;

            let cli_kind = parse_kind(&kind);
            let effective_kind = if cli_kind.is_some() {
                cli_kind
            } else {
                preset_config
                    .get("kind")
                    .and_then(|value| value.as_str())
                    .and_then(parse_kind)
            };

            let mut effective_pdf_mode = parse_pdf_mode(&pdf_mode);
            if matches!(effective_pdf_mode, PdfMode::Auto) {
                if let Some(preset_pdf) = preset_config
                    .get("pdf_mode")
                    .and_then(|value| value.as_str())
                {
                    effective_pdf_mode = parse_pdf_mode(preset_pdf);
                }
            }

            let effective_model = model
                .or_else(|| {
                    preset_config
                        .get("model")
                        .and_then(|value| value.as_str())
                        .map(|s| s.to_string())
                })
                .unwrap_or_else(|| cfg.default_model.clone());

            let cli_recursive = if no_recursive {
                Some(false)
            } else if recursive {
                Some(true)
            } else {
                None
            };
            let effective_recursive = cli_recursive
                .or_else(|| {
                    preset_config
                        .get("recursive")
                        .and_then(|value| value.as_bool())
                })
                .unwrap_or(false);

            let mut exports = if export.is_empty() {
                cfg.exports.clone()
            } else {
                export.clone()
            };
            if let Some(preset_exports) = preset_config
                .get("exports")
                .and_then(|value| value.as_array())
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

            let media_candidate = media_resolution
                .clone()
                .or_else(|| {
                    preset_config
                        .get("media_resolution")
                        .and_then(|value| value.as_str())
                        .map(|s| s.to_string())
                })
                .unwrap_or_else(|| cfg.media_resolution.clone());
            let (media_label, media_enum) =
                resolve_media_resolution(Some(media_candidate.as_str()))?;

            let mut tokens_per_second = cfg.video_tokens_per_second;
            if media_label == "low" && tokens_per_second > 100.0 {
                tokens_per_second = 100.0;
            }

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
                Some(tokens_per_second),
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
                output_dir,
                model: effective_model,
                preset: Some(preset_key.clone()),
                export: exports,
                skip_existing,
                media_resolution: media_enum,
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
        } => {
            println!("Source: {source}");
            println!("Kind:   {kind}");
            println!("PDF:    {pdf_mode}");
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
    }

    Ok(())
}

fn merged_presets(cfg: &config::AppConfig) -> HashMap<String, HashMap<String, Value>> {
    let mut merged = builtin_presets();
    for (name, values) in &cfg.presets {
        let mut converted = HashMap::new();
        for (key, value) in values {
            if let Ok(json_value) = serde_json::to_value(value.clone()) {
                converted.insert(key.to_lowercase(), json_value);
            }
        }
        merged.insert(name.to_lowercase(), converted);
    }
    merged
}

fn builtin_presets() -> HashMap<String, HashMap<String, Value>> {
    let mut map: HashMap<String, HashMap<String, Value>> = HashMap::new();
    map.insert("basic".into(), HashMap::new());

    let mut speed = HashMap::new();
    speed.insert("pdf_mode".into(), Value::String("images".into()));
    map.insert("speed".into(), speed);

    let mut quality = HashMap::new();
    quality.insert("pdf_mode".into(), Value::String("pdf".into()));
    map.insert("quality".into(), quality);

    map
}

fn resolve_media_resolution(value: Option<&str>) -> anyhow::Result<(String, Option<String>)> {
    let raw_value = value.unwrap_or("default");
    let trimmed = raw_value.trim();
    let normalized = if trimmed.is_empty() {
        "default".to_string()
    } else {
        trimmed.to_lowercase()
    };
    let canonical = match normalized.as_str() {
        "media_resolution_default" => "default",
        "media_resolution_low" => "low",
        "media_resolution_medium" => "medium",
        "media_resolution_high" => "high",
        "media_resolution_unspecified" => "unspecified",
        other => other,
    };
    let (label, enum_value) = match canonical {
        "default" => ("default", None),
        "low" => ("low", Some("MEDIA_RESOLUTION_LOW".to_string())),
        "medium" => ("medium", Some("MEDIA_RESOLUTION_MEDIUM".to_string())),
        "high" => ("high", Some("MEDIA_RESOLUTION_HIGH".to_string())),
        "unspecified" => (
            "unspecified",
            Some("MEDIA_RESOLUTION_UNSPECIFIED".to_string()),
        ),
        other => {
            return Err(anyhow!(
                "invalid media resolution '{}'",
                if trimmed.is_empty() { other } else { trimmed }
            ));
        }
    };
    Ok((label.to_string(), enum_value))
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
