use clap::{ArgAction, Parser, Subcommand};
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
    /// Unified engine
    Summarize {
        source: String,
        #[arg(short = 'o', long)]
        output_dir: Option<PathBuf>,
        #[arg(long, default_value = "auto")]
        kind: String,
        #[arg(long, default_value = "auto")]
        pdf_mode: String,
        #[arg(long)]
        model: Option<String>,
        #[arg(long, action = ArgAction::SetTrue)]
        recursive: bool,
        #[arg(long = "no-recursive", action = ArgAction::SetTrue)]
        no_recursive: bool,
        #[arg(long, default_value_t = true)]
        skip_existing: bool,
        #[arg(long)]
        export: Vec<String>,
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
    /// Convert LaTeX tables or structured content to JSON using Gemini
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
