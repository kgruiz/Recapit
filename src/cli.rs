use clap::{ArgAction, Parser, Subcommand, ValueEnum};
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(
    name = "recapit",
    version,
    about = "Rust rewrite with ratatui progress"
)]
pub struct Cli {
    #[command(subcommand)]
    pub cmd: Command,
}

#[derive(Subcommand, Debug)]
pub enum Command {
    /// Transcribe and summarize sources
    Transcribe {
        source: String,
        #[arg(short = 'o', long)]
        output_dir: Option<PathBuf>,
        #[arg(long, default_value = "auto")]
        kind: String,
        #[arg(long, default_value = "auto")]
        pdf_mode: String,
        #[arg(long)]
        model: Option<String>,
        #[arg(long)]
        format: Option<OutputFormatArg>,
        #[arg(long, action = ArgAction::SetTrue)]
        recursive: bool,
        #[arg(long = "no-recursive", action = ArgAction::SetTrue)]
        no_recursive: bool,
        #[arg(long, default_value_t = true)]
        skip_existing: bool,
        #[arg(long)]
        export: Vec<String>,
        #[arg(long = "to")]
        to: Option<ConversionTarget>,
        #[arg(long = "file-pattern", default_value = "*.tex")]
        _conversion_pattern: String,
        #[arg(
            long,
            default_value = "basic",
            help = "Preset profile (basic, speed [pdf_mode=images], quality [pdf_mode=pdf], plus entries from recapit.yaml)"
        )]
        preset: String,
        #[arg(long)]
        config: Option<PathBuf>,
        #[arg(long)]
        media_resolution: Option<String>,
    },
    /// Plan only
    Plan {
        source: String,
        #[arg(long, default_value = "auto")]
        kind: String,
        #[arg(long, default_value = "auto")]
        pdf_mode: String,
    },
    /// Conversion utilities
    Convert {
        #[command(subcommand)]
        command: ConvertCommand,
    },
    /// Planner utilities
    Planner {
        #[command(subcommand)]
        command: PlannerCommand,
    },
    /// Initialize configuration
    Init {
        #[arg(short = 'p', long, default_value = "recapit.yaml")]
        path: PathBuf,
        #[arg(long, action = ArgAction::SetTrue)]
        force: bool,
    },
    /// Cost and telemetry reports
    Report {
        #[command(subcommand)]
        command: ReportCommand,
    },
    /// Cleanup helpers
    Cleanup {
        #[command(subcommand)]
        command: CleanupCommand,
    },
}

#[derive(Clone, Debug, ValueEnum)]
pub enum ConversionTarget {
    Markdown,
    Json,
}

#[derive(Clone, Debug, ValueEnum)]
pub enum OutputFormatArg {
    Markdown,
    Latex,
}

#[derive(Subcommand, Debug)]
pub enum ConvertCommand {
    /// Convert LaTeX sources to Markdown using Gemini
    LatexToMd {
        source: PathBuf,
        #[arg(short = 'o', long)]
        output_dir: Option<PathBuf>,
        #[arg(long, default_value = "*.tex")]
        file_pattern: String,
        #[arg(long, default_value_t = true)]
        skip_existing: bool,
        #[arg(long)]
        model: Option<String>,
        #[arg(long, default_value_t = false)]
        recursive: bool,
    },
    /// Convert LaTeX or Markdown tables/structured content to JSON using Gemini
    LatexToJson {
        source: PathBuf,
        #[arg(short = 'o', long)]
        output_dir: Option<PathBuf>,
        #[arg(long, default_value = "*.tex")]
        file_pattern: String,
        #[arg(long, default_value_t = true)]
        skip_existing: bool,
        #[arg(long)]
        model: Option<String>,
        #[arg(long, default_value_t = false)]
        recursive: bool,
    },
}

#[derive(Subcommand, Debug)]
pub enum PlannerCommand {
    /// Plan ingestion and normalization without running transcription
    Plan {
        source: String,
        #[arg(long, default_value = "auto")]
        kind: String,
        #[arg(long, default_value = "auto")]
        pdf_mode: String,
        #[arg(long)]
        model: Option<String>,
        #[arg(long, default_value_t = false)]
        recursive: bool,
        #[arg(long)]
        config: Option<PathBuf>,
        #[arg(long = "json", action = ArgAction::SetTrue)]
        json: bool,
    },
    /// Inspect raw assets discovered from a source
    Ingest {
        source: String,
        #[arg(long, default_value_t = false)]
        recursive: bool,
        #[arg(long)]
        config: Option<PathBuf>,
        #[arg(long = "json", action = ArgAction::SetTrue)]
        json: bool,
    },
}

#[derive(Subcommand, Debug)]
pub enum ReportCommand {
    /// Summarize run costs from run-summary.json
    Cost {
        #[arg(short = 'i', long, default_value = "run-summary.json")]
        input: PathBuf,
        #[arg(long = "json", action = ArgAction::SetTrue)]
        json: bool,
    },
}

#[derive(Subcommand, Debug)]
pub enum CleanupCommand {
    /// Remove the global recapit cache directory
    Cache {
        #[arg(long = "dry-run", action = ArgAction::SetTrue)]
        dry_run: bool,
        #[arg(long = "yes", action = ArgAction::SetTrue)]
        yes: bool,
    },
    /// Prune job-local downloads (e.g., normalized videos)
    Downloads {
        #[arg(short = 'p', long)]
        path: PathBuf,
        #[arg(long = "dry-run", action = ArgAction::SetTrue)]
        dry_run: bool,
        #[arg(long = "yes", action = ArgAction::SetTrue)]
        yes: bool,
    },
}
