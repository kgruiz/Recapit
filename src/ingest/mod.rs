mod drive;
mod local;
mod normalize;
mod url;
mod youtube;

pub use drive::DriveIngestor;
pub use local::LocalIngestor;
pub use normalize::CompositeNormalizer;
pub use url::UrlIngestor;
pub use youtube::YouTubeIngestor;

use ::url::Url;
use anyhow::Result;

use crate::core::{Asset, Ingestor, Job};

pub struct CompositeIngestor {
    local: LocalIngestor,
    url: UrlIngestor,
    youtube: YouTubeIngestor,
    drive: DriveIngestor,
}

impl CompositeIngestor {
    pub fn new() -> Result<Self> {
        Ok(Self {
            local: LocalIngestor,
            url: UrlIngestor::new(None)?,
            youtube: YouTubeIngestor::default(),
            drive: DriveIngestor::new(None)?,
        })
    }
}

impl Default for CompositeIngestor {
    fn default() -> Self {
        Self::new().expect("failed to construct CompositeIngestor")
    }
}

impl Ingestor for CompositeIngestor {
    fn discover(&self, job: &Job) -> Result<Vec<Asset>> {
        let parsed = Url::parse(&job.source);
        if let Ok(url) = parsed {
            match url.scheme() {
                "http" | "https" => {
                    if self.youtube.supports(&url) {
                        return self.youtube.discover(job);
                    }
                    return self.url.discover(job);
                }
                "yt" | "youtube" => return self.youtube.discover(job),
                "drive" | "gdrive" => return self.drive.discover(job),
                _ => {}
            }
        } else if job.source.starts_with("drive://") || job.source.starts_with("gdrive://") {
            return self.drive.discover(job);
        }
        self.local.discover(job)
    }
}
