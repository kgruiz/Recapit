use clap::{ArgAction, Parser, Subcommand, ValueEnum};
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(
    name = "recapit",
    version,
    about = "CLI for document and media transcription"
)]
pub struct Cli {
    /// Primary action: transcribe/convert the given source(s) unless a subcommand is used
    #[arg(required_unless_present = "cmd", num_args = 1.., value_name = "SOURCE")]
    pub source: Vec<String>,

    #[arg(short = 'o', long)]
    pub output_dir: Option<PathBuf>,
    #[arg(long, default_value = "auto")]
    pub kind: String,
    #[arg(long, default_value = "auto")]
    pub pdf_mode: String,
    #[arg(
        long,
        help = "Only process selected PDF pages (1-based). Examples: 1-3,5,10- or -2"
    )]
    pub pages: Vec<String>,
    #[arg(
        long = "pdf-dpi",
        help = "DPI for PDF -> PNG rasterization (default 200)"
    )]
    pub pdf_dpi: Option<u32>,
    #[arg(long)]
    pub model: Option<String>,
    #[arg(long)]
    pub format: Option<OutputFormatArg>,
    #[arg(long, action = ArgAction::SetTrue)]
    pub recursive: bool,
    #[arg(long = "no-recursive", action = ArgAction::SetTrue)]
    pub no_recursive: bool,
    #[arg(long, default_value_t = true)]
    pub skip_existing: bool,
    #[arg(long)]
    pub export: Vec<String>,
    #[arg(long = "to", help = "Convert instead of transcribe: markdown|json")]
    pub to: Option<ConversionTarget>,
    #[arg(
        long = "from",
        default_value = "auto",
        help = "Input format hint: auto|latex|markdown"
    )]
    pub from: ConversionSource,
    #[arg(long = "file-pattern", default_value = "*.tex")]
    pub file_pattern: String,
    #[arg(
        long,
        default_value = "basic",
        help = "Preset profile (basic, speed [pdf_mode=images], quality [pdf_mode=pdf], plus entries from recapit.yaml)"
    )]
    pub preset: String,
    #[arg(long)]
    pub config: Option<PathBuf>,
    #[arg(long)]
    pub media_resolution: Option<String>,
    #[arg(long, action = ArgAction::SetTrue, help = "Plan normalization only (no Gemini calls)")]
    pub dry_run: bool,
    #[arg(long = "json", action = ArgAction::SetTrue, help = "Machine-readable output for --dry-run")]
    pub json: bool,
    #[arg(long, action = ArgAction::SetTrue, help = "Suppress TUI/progress and final summary")]
    pub quiet: bool,
    #[arg(long, action = ArgAction::SetTrue, help = "Write run metadata (summary, events) alongside transcript in an output folder")]
    pub save_metadata: bool,

    #[command(subcommand)]
    pub cmd: Option<Command>,
}

#[derive(Subcommand, Debug)]
pub enum Command {
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
pub enum ConversionSource {
    Auto,
    Latex,
    Markdown,
}

#[derive(Clone, Debug, ValueEnum)]
pub enum OutputFormatArg {
    Markdown,
    Latex,
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
