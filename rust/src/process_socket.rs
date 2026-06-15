use std::time::Duration;

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine as _;
use futures_util::{SinkExt, StreamExt};
use http::header::{HeaderName, HeaderValue};
use serde_json::Value;
use tokio::net::TcpStream;
use tokio::time::timeout;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{connect_async, MaybeTlsStream, WebSocketStream};

use crate::config::KEEPALIVE_PING_INTERVAL_SECS;
use crate::error::{Error, Result};

/// Streaming WebSocket connection to the sandbox process runtime.
pub struct ProcessSocket {
    socket: WebSocketStream<MaybeTlsStream<TcpStream>>,
}

impl ProcessSocket {
    /// Connect to a process runtime WebSocket path with bearer-token auth.
    pub async fn connect(base_url: &str, token: &str, path: &str) -> Result<Self> {
        let mut request = ws_url(base_url, path)?.into_client_request()?;
        request.headers_mut().insert(
            HeaderName::from_static("authorization"),
            HeaderValue::from_str(&format!("Bearer {token}"))?,
        );
        let (socket, _response) = connect_async(request).await?;
        Ok(Self { socket })
    }

    /// Send a JSON frame to the process runtime.
    pub async fn send_json(&mut self, payload: &Value) -> Result<()> {
        self.socket
            .send(Message::Text(payload.to_string().into()))
            .await?;
        Ok(())
    }

    /// Send stdin bytes encoded in the sandbox runtime protocol.
    pub async fn send_stdin(&mut self, data: impl AsRef<[u8]>) -> Result<()> {
        self.send_json(&stdin_payload(data)).await
    }

    /// Close stdin for the attached process.
    pub async fn close_stdin(&mut self) -> Result<()> {
        self.send_json(&close_stdin_payload()).await
    }

    /// Send a WebSocket ping frame.
    pub async fn send_ping(&mut self) -> Result<()> {
        self.socket
            .send(Message::Ping(b"watasu-sdk".to_vec().into()))
            .await?;
        Ok(())
    }

    /// Close the local WebSocket stream.
    pub async fn close(&mut self) -> Result<()> {
        self.socket.close(None).await?;
        Ok(())
    }

    /// Read the next JSON process frame.
    pub async fn next_frame(&mut self) -> Result<Option<Value>> {
        let idle = Duration::from_secs((KEEPALIVE_PING_INTERVAL_SECS / 2).max(1));

        loop {
            match timeout(idle, self.socket.next()).await {
                Ok(Some(Ok(message))) => match message {
                    Message::Text(text) => {
                        let frame: Value = serde_json::from_str(&text)?;
                        match frame.get("type").and_then(Value::as_str) {
                            Some("ready" | "pong") => continue,
                            Some("error") => {
                                let message = frame
                                    .get("message")
                                    .or_else(|| frame.get("code"))
                                    .and_then(Value::as_str)
                                    .unwrap_or("process error");
                                return Err(Error::Sandbox(message.to_string()));
                            }
                            _ => return Ok(Some(frame)),
                        }
                    }
                    Message::Binary(_) => {
                        return Err(Error::Sandbox(
                            "process websocket returned binary frame".into(),
                        ))
                    }
                    Message::Close(_) => return Ok(None),
                    Message::Ping(payload) => self.socket.send(Message::Pong(payload)).await?,
                    Message::Pong(_) => continue,
                    Message::Frame(_) => continue,
                },
                Ok(Some(Err(error))) => return Err(error.into()),
                Ok(None) => return Ok(None),
                Err(_elapsed) => {
                    self.send_ping().await?;
                    continue;
                }
            }
        }
    }

    /// Keepalive interval used by the SDK.
    pub fn keepalive_interval_secs(&self) -> u64 {
        KEEPALIVE_PING_INTERVAL_SECS
    }
}

/// Encode stdin bytes for the sandbox runtime protocol.
pub fn encode_runtime_data(data: impl AsRef<[u8]>) -> String {
    BASE64.encode(data)
}

pub(crate) fn stdin_payload(data: impl AsRef<[u8]>) -> Value {
    serde_json::json!({
        "type": "stdin",
        "data": encode_runtime_data(data)
    })
}

pub(crate) fn close_stdin_payload() -> Value {
    serde_json::json!({"type": "close_stdin"})
}

/// Decode base64 stdout/stderr frame data from the sandbox runtime protocol.
pub fn decode_runtime_data(value: &str) -> String {
    BASE64
        .decode(value)
        .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
        .unwrap_or_else(|_| value.to_string())
}

fn ws_url(base_url: &str, path: &str) -> Result<String> {
    let mut url = url::Url::parse(base_url)?;
    let scheme = match url.scheme() {
        "https" => "wss",
        "http" => "ws",
        other => other,
    }
    .to_string();
    url.set_scheme(&scheme)
        .map_err(|_| Error::Sandbox("invalid websocket scheme".into()))?;
    url.set_path(path.split('?').next().unwrap_or(path));
    if let Some(query) = path.split_once('?').map(|(_, query)| query) {
        url.set_query(Some(query));
    }
    Ok(url.to_string())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{close_stdin_payload, stdin_payload};

    #[test]
    fn process_input_payloads_match_runtime_protocol() {
        assert_eq!(
            stdin_payload("hi\n"),
            json!({"type": "stdin", "data": "aGkK"})
        );
        assert_eq!(close_stdin_payload(), json!({"type": "close_stdin"}));
    }
}
