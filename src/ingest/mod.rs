use crate::core::{Asset, Ingestor, Job, SourceKind};
use std::path::PathBuf;

pub struct CompositeIngestor;

impl Ingestor for CompositeIngestor {
    fn discover(&self, job: &Job) -> anyhow::Result<Vec<Asset>> {
        let path = std::path::Path::new(&job.source);
        if path.exists() && path.is_file() {
            let extension = path
                .extension()
                .and_then(|ext| ext.to_str())
                .unwrap_or("")
                .to_lowercase();
            let media = match extension.as_str() {
                "pdf" => Some("pdf"),
                "png" | "jpg" | "jpeg" | "gif" | "bmp" | "tif" | "tiff" => Some("image"),
                "mp4" | "mov" | "mkv" => Some("video"),
                "mp3" | "wav" | "m4a" => Some("audio"),
                _ => None,
            };

            if let Some(media) = media {
                return Ok(vec![Asset {
                    path: PathBuf::from(path),
                    media: media.to_string(),
                    page_index: None,
                    source_kind: SourceKind::Local,
                    mime: None,
                    meta: serde_json::json!({}),
                }]);
            }
        }

        // URL/YT/Drive omitted for brevity
        Ok(vec![])
    }
}
