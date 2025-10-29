use std::fs;
use std::path::{Path, PathBuf};

use anyhow::Result;

use crate::core::{Asset, Job, SourceKind};
use crate::utils::slugify;

const MEDIA_BY_SUFFIX: &[(&str, &str)] = &[
    (".pdf", "pdf"),
    (".png", "image"),
    (".jpg", "image"),
    (".jpeg", "image"),
    (".tif", "image"),
    (".tiff", "image"),
    (".bmp", "image"),
    (".gif", "image"),
    (".mp4", "video"),
    (".mov", "video"),
    (".mkv", "video"),
    (".mp3", "audio"),
    (".wav", "audio"),
    (".m4a", "audio"),
];

pub struct LocalIngestor;

impl Default for LocalIngestor {
    fn default() -> Self {
        Self
    }
}

impl LocalIngestor {
    pub fn discover(&self, job: &Job) -> Result<Vec<Asset>> {
        let root = Path::new(&job.source).expand();
        if !root.exists() {
            return Ok(vec![]);
        }
        if root.is_file() {
            if let Some(asset) = self.asset_from_path(&root) {
                return Ok(vec![asset]);
            }
            return Ok(vec![]);
        }
        let mut assets = Vec::new();
        let iterator: Box<dyn Iterator<Item = Result<PathBuf, std::io::Error>>> = if job.recursive {
            Box::new(
                walkdir::WalkDir::new(&root)
                    .into_iter()
                    .filter_map(|entry| entry.ok())
                    .filter(|entry| entry.file_type().is_file())
                    .map(|entry| Ok(entry.into_path())),
            )
        } else {
            Box::new(
                fs::read_dir(&root)?
                    .map(|res| res.map(|entry| entry.path()))
                    .collect::<Result<Vec<_>, _>>()?
                    .into_iter()
                    .map(Ok),
            )
        };

        for path in iterator {
            let path = path?;
            if let Some(asset) = self.asset_from_path(&path) {
                assets.push(asset);
            }
        }
        Ok(assets)
    }

    fn asset_from_path(&self, path: &Path) -> Option<Asset> {
        let extension = path.extension()?.to_string_lossy().to_lowercase();
        let media = MEDIA_BY_SUFFIX
            .iter()
            .find(|(suffix, _)| {
                suffix
                    .trim_start_matches('.')
                    .eq_ignore_ascii_case(&extension)
            })
            .map(|(_, media)| *media)?;
        Some(Asset {
            path: path.to_path_buf(),
            media: media.to_string(),
            page_index: None,
            source_kind: SourceKind::Local,
            mime: None,
            meta: serde_json::json!({
                "slug": slugify(path.file_stem().unwrap_or_default().to_string_lossy()),
            }),
        })
    }
}

trait ExpandPath {
    fn expand(self) -> PathBuf;
}

impl ExpandPath for &Path {
    fn expand(self) -> PathBuf {
        if self.starts_with("~") {
            if let Some(home) = dirs::home_dir() {
                return home.join(self.strip_prefix("~").unwrap());
            }
        }
        self.to_path_buf()
    }
}
