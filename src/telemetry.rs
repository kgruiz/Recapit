use std::time::{Duration, Instant};

pub struct RunMonitor {
    start: Instant,
}

impl RunMonitor {
    pub fn new() -> Self {
        Self {
            start: Instant::now(),
        }
    }

    #[allow(dead_code)]
    pub fn elapsed(&self) -> Duration {
        self.start.elapsed()
    }

    #[allow(dead_code)]
    pub fn record(&self, stage: &str, message: &str) {
        tracing::info!(%stage, %message, elapsed = ?self.elapsed());
    }
}
