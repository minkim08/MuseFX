pub mod adapters;
pub mod api;
pub mod models;
pub mod pipeline;
pub mod store;

use std::sync::Arc;
use adapters::{AudioAdapter, DetectionAdapter};
use store::JobStore;

#[derive(Clone)]
pub struct AppState {
    pub store: JobStore,
    pub detection: Arc<dyn DetectionAdapter>,
    pub audio: Arc<dyn AudioAdapter>,
}
