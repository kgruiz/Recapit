use anyhow::Result;
use std::path::{Path, PathBuf};
use std::{fs, io};

pub fn ensure_dir(path: &Path) -> io::Result<()> {
    fs::create_dir_all(path)
}

pub fn resolve_path_with_prompt(path: &Path, is_dir: bool) -> Result<Option<PathBuf>> {
    if !path.exists() {
        return Ok(Some(path.to_path_buf()));
    }

    println!(
        "{} already exists. [o]verwrite, [n]ew name, [s]top? ",
        path.display()
    );
    loop {
        let mut input = String::new();
        io::stdin().read_line(&mut input)?;
        let choice = input.trim().to_lowercase();
        match choice.as_str() {
            "o" | "overwrite" => return Ok(Some(path.to_path_buf())),
            "s" | "q" | "stop" | "cancel" => return Ok(None),
            "n" | "new" => {
                let mut idx = 1;
                loop {
                    let candidate = if is_dir {
                        let name = path
                            .file_name()
                            .map(|s| s.to_string_lossy().to_string())
                            .unwrap_or_else(|| "output".into());
                        let parent = path.parent().unwrap_or_else(|| Path::new("."));
                        parent.join(format!("{name}-{idx}"))
                    } else {
                        let stem = path
                            .file_stem()
                            .map(|s| s.to_string_lossy().to_string())
                            .unwrap_or_else(|| "output".into());
                        let ext = path
                            .extension()
                            .map(|s| format!(".{}", s.to_string_lossy()))
                            .unwrap_or_default();
                        let parent = path.parent().unwrap_or_else(|| Path::new("."));
                        parent.join(format!("{stem}-{idx}{ext}"))
                    };
                    if !candidate.exists() {
                        println!("using {}", candidate.display());
                        return Ok(Some(candidate));
                    }
                    idx += 1;
                }
            }
            _ => {
                println!("Choose [o]verwrite, [n]ew name, or [s]top: ");
            }
        }
    }
}

pub fn slugify<S: AsRef<str>>(input: S) -> String {
    input
        .as_ref()
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || matches!(c, '-' | '_' | '.') {
                c
            } else {
                '-'
            }
        })
        .collect::<String>()
        .trim_matches('-')
        .to_string()
}
