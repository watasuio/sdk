use thiserror::Error;

/// SDK result type.
pub type Result<T> = std::result::Result<T, Error>;

/// Error type returned by Watasu SDK operations.
#[derive(Debug, Error)]
pub enum Error {
    /// No API key was provided and `WATASU_API_KEY` was not set.
    #[error("WATASU_API_KEY is required")]
    MissingApiKey,
    /// Authentication or authorization failed.
    #[error("authentication failed: {0}")]
    Authentication(String),
    /// Requested resource was not found.
    #[error("not found: {0}")]
    NotFound(String),
    /// The request timed out.
    #[error("request timed out")]
    Timeout,
    /// The API rejected the request arguments.
    #[error("invalid argument: {0}")]
    InvalidArgument(String),
    /// The API rate limited the request.
    #[error("rate limit exceeded: {0}")]
    RateLimit(String),
    /// The sandbox or file operation ran out of space.
    #[error("not enough space: {0}")]
    NotEnoughSpace(String),
    /// A requested file path did not exist.
    #[error("file not found: {0}")]
    FileNotFound(String),
    /// A command exited with a non-zero code and preserved output.
    #[error("command exited with code {}", result.exit_code)]
    CommandExit {
        /// Captured command result.
        result: crate::CommandResult,
    },
    /// Generic sandbox or API error.
    #[error("{0}")]
    Sandbox(String),
    /// HTTP transport error.
    #[error(transparent)]
    Http(#[from] reqwest::Error),
    /// URL parsing error.
    #[error(transparent)]
    Url(#[from] url::ParseError),
    /// JSON serialization or decoding error.
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    /// WebSocket transport error.
    #[error(transparent)]
    WebSocket(#[from] tungstenite::Error),
    /// Local I/O error.
    #[error(transparent)]
    Io(#[from] std::io::Error),
    /// HTTP request construction error.
    #[error(transparent)]
    HttpBuild(#[from] http::Error),
}

impl Error {
    pub(crate) fn from_status(status: reqwest::StatusCode, payload: &serde_json::Value) -> Self {
        let code = payload
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or_default();
        let message = payload
            .get("message")
            .and_then(|v| v.as_str())
            .map(ToOwned::to_owned)
            .or_else(|| {
                payload
                    .get("errors")
                    .and_then(|v| v.as_array())
                    .map(|items| {
                        items
                            .iter()
                            .map(|item| item.as_str().unwrap_or_default())
                            .collect::<Vec<_>>()
                            .join(", ")
                    })
            })
            .unwrap_or_else(|| code.to_string());

        match status.as_u16() {
            401 | 403 => Self::Authentication(message),
            404 => Self::NotFound(message),
            408 | 504 => Self::Timeout,
            400 | 422 => Self::InvalidArgument(message),
            429 => Self::RateLimit(message),
            _ if code == "not_enough_space" => Self::NotEnoughSpace(message),
            _ if code == "file_not_found" => Self::FileNotFound(message),
            _ => Self::Sandbox(message),
        }
    }
}
