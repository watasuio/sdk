use std::time::Duration;

use reqwest::{Client, Method, Response};
use serde_json::Value;

use crate::config::ConnectionConfig;
use crate::error::{Error, Result};

#[derive(Clone)]
pub(crate) struct ControlClient {
    config: ConnectionConfig,
    client: Client,
}

impl ControlClient {
    pub(crate) fn new(config: ConnectionConfig) -> Result<Self> {
        let client = Client::builder()
            .timeout(Duration::from_secs(config.request_timeout_secs))
            .build()?;
        Ok(Self { config, client })
    }

    pub(crate) async fn get(&self, path: &str) -> Result<Value> {
        self.request(Method::GET, path, None).await
    }

    pub(crate) async fn post(&self, path: &str, body: Value) -> Result<Value> {
        self.request(Method::POST, path, Some(body)).await
    }

    pub(crate) async fn post_idempotent(
        &self,
        path: &str,
        body: Value,
        idempotency_key: Option<&str>,
    ) -> Result<Value> {
        self.request_with_idempotency(Method::POST, path, Some(body), idempotency_key)
            .await
    }

    pub(crate) async fn put(&self, path: &str, body: Value) -> Result<Value> {
        self.request(Method::PUT, path, Some(body)).await
    }

    pub(crate) async fn patch(&self, path: &str, body: Value) -> Result<Value> {
        self.request(Method::PATCH, path, Some(body)).await
    }

    pub(crate) async fn delete(&self, path: &str) -> Result<Value> {
        self.request(Method::DELETE, path, None).await
    }

    pub(crate) async fn delete_with_body(&self, path: &str, body: Value) -> Result<Value> {
        self.request(Method::DELETE, path, Some(body)).await
    }

    async fn request(&self, method: Method, path: &str, body: Option<Value>) -> Result<Value> {
        self.request_with_idempotency(method, path, body, None)
            .await
    }

    async fn request_with_idempotency(
        &self,
        method: Method,
        path: &str,
        body: Option<Value>,
        idempotency_key: Option<&str>,
    ) -> Result<Value> {
        let api_key = self.config.api_key.as_ref().ok_or(Error::MissingApiKey)?;
        let mut request = self
            .client
            .request(method, join_url(&self.config.api_url, path))
            .bearer_auth(api_key);
        if let Some(idempotency_key) = idempotency_key {
            request = request.header("Idempotency-Key", idempotency_key);
        }
        if let Some(body) = body {
            request = request.json(&body);
        }
        json_response(request.send().await?).await
    }
}

#[derive(Clone)]
pub(crate) struct DataPlaneClient {
    pub(crate) base_url: String,
    pub(crate) token: String,
    client: Client,
}

impl DataPlaneClient {
    pub(crate) fn new(base_url: String, token: String, config: &ConnectionConfig) -> Result<Self> {
        let client = Client::builder()
            .timeout(Duration::from_secs(config.request_timeout_secs))
            .build()?;
        Ok(Self {
            base_url,
            token,
            client,
        })
    }

    pub(crate) async fn get_json(&self, path: &str) -> Result<Value> {
        json_response(
            self.client
                .get(join_url(&self.base_url, path))
                .bearer_auth(&self.token)
                .send()
                .await?,
        )
        .await
    }

    pub(crate) async fn post_json(&self, path: &str, body: Value) -> Result<Value> {
        json_response(
            self.client
                .post(join_url(&self.base_url, path))
                .bearer_auth(&self.token)
                .json(&body)
                .send()
                .await?,
        )
        .await
    }

    pub(crate) async fn delete_json(&self, path: &str) -> Result<Value> {
        json_response(
            self.client
                .delete(join_url(&self.base_url, path))
                .bearer_auth(&self.token)
                .send()
                .await?,
        )
        .await
    }

    pub(crate) async fn get_bytes_with_limit(
        &self,
        path: &str,
        max_bytes: Option<usize>,
    ) -> Result<Vec<u8>> {
        let response = self
            .client
            .get(join_url(&self.base_url, path))
            .bearer_auth(&self.token)
            .send()
            .await?;
        if !response.status().is_success() {
            return Err(Error::from_status(
                response.status(),
                &read_json_or_text(response).await?,
            ));
        }
        read_limited_bytes(response, max_bytes).await
    }

    pub(crate) async fn put_bytes(&self, path: &str, data: Vec<u8>) -> Result<Value> {
        json_response(
            self.client
                .put(join_url(&self.base_url, path))
                .bearer_auth(&self.token)
                .header("content-type", "application/octet-stream")
                .body(data)
                .send()
                .await?,
        )
        .await
    }
}

async fn read_limited_bytes(mut response: Response, max_bytes: Option<usize>) -> Result<Vec<u8>> {
    if let (Some(content_length), Some(max_bytes)) = (response.content_length(), max_bytes) {
        if content_length > max_bytes as u64 {
            return Err(Error::ByteLimitExceeded {
                stream: "file",
                max_bytes,
                actual_bytes: content_length as usize,
            });
        }
    }

    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await? {
        if let Some(max_bytes) = max_bytes {
            let actual_bytes = bytes.len().saturating_add(chunk.len());
            if actual_bytes > max_bytes {
                return Err(Error::ByteLimitExceeded {
                    stream: "file",
                    max_bytes,
                    actual_bytes,
                });
            }
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}

async fn json_response(response: Response) -> Result<Value> {
    let status = response.status();
    let payload = read_json_or_text(response).await?;
    if !status.is_success() {
        return Err(Error::from_status(status, &payload));
    }
    Ok(payload)
}

async fn read_json_or_text(response: Response) -> Result<Value> {
    let text = response.text().await?;
    if text.trim().is_empty() {
        return Ok(Value::Object(Default::default()));
    }
    Ok(serde_json::from_str(&text).unwrap_or_else(|_| serde_json::json!({ "message": text })))
}

pub(crate) fn join_url(base: &str, path: &str) -> String {
    format!(
        "{}{}",
        base.trim_end_matches('/'),
        if path.starts_with('/') {
            path.to_string()
        } else {
            format!("/{path}")
        }
    )
}
