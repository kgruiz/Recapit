use crate::core::{Asset, Provider};

pub struct GeminiProvider {
    api_key: String,
    model: String,
    http: reqwest::Client,
}

impl GeminiProvider {
    pub fn new(api_key: String, model: String) -> Self {
        let http = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(600))
            .build()
            .expect("client");
        Self {
            api_key,
            model,
            http,
        }
    }
}

impl Provider for GeminiProvider {
    fn supports(&self, capability: &str) -> bool {
        matches!(capability, "text" | "image" | "video" | "pdf")
    }

    fn transcribe(
        &self,
        instruction: &str,
        assets: &[Asset],
        modality: &str,
        _meta: &serde_json::Value,
    ) -> anyhow::Result<String> {
        let _ = (
            &self.api_key,
            &self.model,
            &self.http,
            instruction,
            assets,
            modality,
        );
        anyhow::bail!("Gemini HTTP wiring TODO: implement generateContent + Files upload");
    }
}
