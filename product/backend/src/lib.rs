pub mod adapters;
pub mod api;
pub mod models;
pub mod pipeline;
pub mod store;

use adapters::{AudioAdapter, DetectionAdapter, ReviewAdapter};
use std::sync::Arc;
use store::JobStore;

#[derive(Clone)]
pub struct AppState {
    pub store: JobStore,
    pub detection: Arc<dyn DetectionAdapter>,
    pub audio: Arc<dyn AudioAdapter>,
    /// Optional second-pass reviewer (Muse Spark via Detection service).
    pub review: Option<Arc<dyn ReviewAdapter>>,
}
