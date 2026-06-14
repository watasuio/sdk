use std::time::Duration;

use reqwest::blocking::{Client, Response};
use reqwest::Method;
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

    pub(crate) fn get(&self, path: &str) -> Result<Value> {
        self.request(Method::GET, path, None)
    }

    pub(crate) fn post(&self, path: &str, body: Value) -> Result<Value> {
        self.request(Method::POST, path, Some(body))
    }

    pub(crate) fn patch(&self, path: &str, body: Value) -> Result<Value> {
        self.request(Method::PATCH, path, Some(body))
    }

    pub(crate) fn delete(&self, path: &str) -> Result<Value> {
        self.request(Method::DELETE, path, None)
    }

    fn request(&self, method: Method, path: &str, body: Option<Value>) -> Result<Value> {
        let api_key = self.config.api_key.as_ref().ok_or(Error::MissingApiKey)?;
        let mut request = self
            .client
            .request(method, join_url(&self.config.api_url, path))
            .bearer_auth(api_key);
        if let Some(body) = body {
            request = request.json(&body);
        }
        json_response(request.send()?)
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

    pub(crate) fn get_json(&self, path: &str) -> Result<Value> {
        json_response(
            self.client
                .get(join_url(&self.base_url, path))
                .bearer_auth(&self.token)
                .send()?,
        )
    }

    pub(crate) fn post_json(&self, path: &str, body: Value) -> Result<Value> {
        json_response(
            self.client
                .post(join_url(&self.base_url, path))
                .bearer_auth(&self.token)
                .json(&body)
                .send()?,
        )
    }

    pub(crate) fn delete_json(&self, path: &str) -> Result<Value> {
        json_response(
            self.client
                .delete(join_url(&self.base_url, path))
                .bearer_auth(&self.token)
                .send()?,
        )
    }

    pub(crate) fn get_bytes(&self, path: &str) -> Result<Vec<u8>> {
        let response = self
            .client
            .get(join_url(&self.base_url, path))
            .bearer_auth(&self.token)
            .send()?;
        if !response.status().is_success() {
            return Err(Error::from_status(
                response.status(),
                &read_json_or_text(response)?,
            ));
        }
        Ok(response.bytes()?.to_vec())
    }

    pub(crate) fn put_bytes(&self, path: &str, data: Vec<u8>) -> Result<Value> {
        json_response(
            self.client
                .put(join_url(&self.base_url, path))
                .bearer_auth(&self.token)
                .header("content-type", "application/octet-stream")
                .body(data)
                .send()?,
        )
    }
}

fn json_response(response: Response) -> Result<Value> {
    let status = response.status();
    let payload = read_json_or_text(response)?;
    if !status.is_success() {
        return Err(Error::from_status(status, &payload));
    }
    Ok(payload)
}

fn read_json_or_text(response: Response) -> Result<Value> {
    let text = response.text()?;
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
