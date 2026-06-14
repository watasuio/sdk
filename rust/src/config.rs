use std::env;

/// WebSocket keepalive interval used by command streams.
pub const KEEPALIVE_PING_INTERVAL_SECS: u64 = 50;
/// Default timeout for create/connect operations that encapsulate runtime readiness.
pub const SESSION_OPERATION_REQUEST_TIMEOUT_SECS: u64 = 150;

/// Optional connection overrides accepted by SDK entrypoints.
#[derive(Clone, Debug, Default)]
pub struct ConnectionOptions {
    /// API key. Defaults to `WATASU_API_KEY`.
    pub api_key: Option<String>,
    /// Base Watasu domain. Defaults to `watasu.io`.
    pub domain: Option<String>,
    /// Absolute control-plane API URL. Defaults to `https://api.<domain>/v1`.
    pub api_url: Option<String>,
    /// Data-plane base domain used only for derived public port hosts.
    pub data_plane_domain: Option<String>,
    /// HTTP request timeout in seconds.
    pub request_timeout_secs: Option<u64>,
}

/// Resolved connection settings used by control-plane and data-plane clients.
#[derive(Clone, Debug)]
pub struct ConnectionConfig {
    /// API bearer token, when configured.
    pub api_key: Option<String>,
    /// Base Watasu domain.
    pub domain: String,
    /// Absolute control-plane API URL.
    pub api_url: String,
    /// Data-plane domain used for public sandbox port hostnames.
    pub data_plane_domain: String,
    /// HTTP request timeout in seconds.
    pub request_timeout_secs: u64,
}

impl ConnectionConfig {
    /// Resolve connection options from explicit values and environment.
    pub fn new(opts: ConnectionOptions) -> Self {
        let domain = opts
            .domain
            .or_else(|| env::var("WATASU_DOMAIN").ok())
            .unwrap_or_else(|| "watasu.io".to_string());
        let api_url = opts
            .api_url
            .or_else(|| env::var("WATASU_API_URL").ok())
            .unwrap_or_else(|| format!("https://api.{domain}/v1"));

        Self {
            api_key: opts.api_key.or_else(|| env::var("WATASU_API_KEY").ok()),
            domain,
            api_url,
            data_plane_domain: opts
                .data_plane_domain
                .or_else(|| env::var("WATASU_DATA_PLANE_DOMAIN").ok())
                .unwrap_or_else(|| "watasuhost.com".to_string()),
            request_timeout_secs: opts
                .request_timeout_secs
                .unwrap_or(SESSION_OPERATION_REQUEST_TIMEOUT_SECS),
        }
    }
}
