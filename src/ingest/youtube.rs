use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use url::Url;

use crate::core::{Asset, Job, SourceKind};

pub struct YouTubeIngestor {
    hosts: HashSet<&'static str>,
}

impl Default for YouTubeIngestor {
    fn default() -> Self {
        Self {
            hosts: HashSet::from([
                "youtu.be",
                "youtube.com",
                "www.youtube.com",
                "m.youtube.com",
            ]),
        }
    }
}

impl YouTubeIngestor {
    pub fn supports(&self, url: &Url) -> bool {
        if matches!(url.scheme(), "yt" | "youtube") {
            return true;
        }
        self.hosts.contains(url.host_str().unwrap_or_default())
    }

    pub fn discover(&self, job: &Job) -> Result<Vec<Asset>> {
        let parsed = match Url::parse(&job.source) {
            Ok(url) => url,
            Err(_) => Url::parse(&format!("https://{}", job.source))?,
        };
        if !self.supports(&parsed) {
            return Ok(vec![]);
        }
        let url = parsed.to_string();
        Ok(vec![Asset {
            path: PathBuf::from(url.clone()),
            media: "video".into(),
            page_index: None,
            source_kind: SourceKind::Youtube,
            mime: Some("video/*".into()),
            meta: serde_json::json!({
                "source_url": url,
                "pass_through": true,
            }),
        }])
    }
}
