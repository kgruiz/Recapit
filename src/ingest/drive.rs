use std::fs::{self, File};
use std::io::copy;
use std::path::{Path, PathBuf};

use anyhow::{anyhow, bail, Context, Result};
use jsonwebtoken::{encode, Algorithm, EncodingKey, Header};
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use time::{Duration, OffsetDateTime};

use crate::core::{Asset, Job, SourceKind};
use crate::utils::ensure_dir;
use crate::video::sha256sum;

const SCOPE: &str = "https://www.googleapis.com/auth/drive.readonly";

#[derive(Debug, Clone)]
pub struct DriveIngestor {
    cache_dir: PathBuf,
    client: Client,
}

impl DriveIngestor {
    pub fn new(cache_dir: Option<PathBuf>) -> Result<Self> {
        let cache = cache_dir.unwrap_or_else(|| std::env::temp_dir().join("recapit-drive-cache"));
        ensure_dir(&cache)?;
        Ok(Self {
            cache_dir: cache,
            client: Client::builder()
                .timeout(std::time::Duration::from_secs(120))
                .build()?,
        })
    }

    pub fn discover(&self, job: &Job) -> Result<Vec<Asset>> {
        let source = job.source.trim();
        let file_id = if let Some(rest) = source.strip_prefix("drive://") {
            rest
        } else if let Some(rest) = source.strip_prefix("gdrive://") {
            rest
        } else {
            return Ok(vec![]);
        }
        .trim();
        if file_id.is_empty() {
            bail!("Drive URI missing file identifier");
        }
        let destination = self.cache_dir.join(file_id);
        if !destination.exists() {
            let creds = ServiceAccountCredentials::load_from_env()?;
            let token = creds.fetch_token(&self.client)?;
            self.download_file(file_id, &destination, &token)?;
        }

        let media = infer_media(&destination);
        let mime = guess_mime(&destination);
        let meta = serde_json::json!({
            "drive_file_id": file_id,
            "sha256": sha256sum(&destination)?,
            "size_bytes": destination.metadata().ok().map(|m| m.len()),
        });
        Ok(vec![Asset {
            path: destination,
            media: media.into(),
            page_index: None,
            source_kind: SourceKind::Drive,
            mime: Some(mime.into()),
            meta,
        }])
    }

    fn download_file(&self, file_id: &str, destination: &Path, token: &str) -> Result<()> {
        ensure_dir(destination.parent().unwrap_or_else(|| Path::new(".")))?;
        let url = format!("https://www.googleapis.com/drive/v3/files/{file_id}?alt=media");
        let mut response = self
            .client
            .get(url)
            .bearer_auth(token)
            .send()
            .with_context(|| format!("Downloading Drive file {file_id}"))?;
        if !response.status().is_success() {
            bail!("Drive download failed with status {}", response.status());
        }
        let temp = destination.with_extension("part");
        let mut file = File::create(&temp)?;
        copy(&mut response, &mut file)?;
        fs::rename(temp, destination)?;
        Ok(())
    }
}

#[derive(Debug, Deserialize)]
struct ServiceAccountCredentials {
    client_email: String,
    private_key: String,
}

impl ServiceAccountCredentials {
    fn load_from_env() -> Result<Self> {
        let path = std::env::var("GOOGLE_APPLICATION_CREDENTIALS")
            .map_err(|_| anyhow!("Set GOOGLE_APPLICATION_CREDENTIALS to use Drive ingestion"))?;
        let text = std::fs::read_to_string(&path)
            .with_context(|| format!("Reading service account credentials {path}"))?;
        let creds: ServiceAccountCredentials = serde_json::from_str(&text)?;
        Ok(creds)
    }

    fn fetch_token(&self, client: &Client) -> Result<String> {
        let now = OffsetDateTime::now_utc();
        let claim = ServiceAccountClaim {
            iss: &self.client_email,
            scope: SCOPE,
            aud: "https://oauth2.googleapis.com/token",
            exp: now.saturating_add(Duration::minutes(55)).unix_timestamp(),
            iat: now.unix_timestamp(),
        };
        let jwt = encode(
            &Header::new(Algorithm::RS256),
            &claim,
            &EncodingKey::from_rsa_pem(self.private_key.as_bytes())?,
        )?;
        let params = [
            ("grant_type", "urn:ietf:params:oauth:grant-type:jwt-bearer"),
            ("assertion", jwt.as_str()),
        ];
        let resp = client
            .post("https://oauth2.googleapis.com/token")
            .form(&params)
            .send()?;
        if !resp.status().is_success() {
            bail!("OAuth token exchange failed with status {}", resp.status());
        }
        let payload: TokenResponse = resp.json()?;
        Ok(payload.access_token)
    }
}

#[derive(Serialize)]
struct ServiceAccountClaim<'a> {
    iss: &'a str,
    scope: &'a str,
    aud: &'a str,
    exp: i64,
    iat: i64,
}

#[derive(Debug, Deserialize)]
struct TokenResponse {
    access_token: String,
}

fn infer_media(path: &Path) -> &'static str {
    let ext = path
        .extension()
        .map(|ext| ext.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    match ext.as_str() {
        "pdf" => "pdf",
        "png" | "jpg" | "jpeg" | "gif" => "image",
        "mp4" | "mov" | "mkv" => "video",
        "mp3" | "wav" | "m4a" => "audio",
        _ => "pdf",
    }
}

fn guess_mime(path: &Path) -> &'static str {
    let ext = path
        .extension()
        .map(|ext| ext.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    match ext.as_str() {
        "pdf" => "application/pdf",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "mp4" | "mov" | "mkv" => "video/mp4",
        "mp3" | "wav" | "m4a" => "audio/mpeg",
        _ => "application/octet-stream",
    }
}
