use std::io::ErrorKind;
use std::net::TcpStream;
use std::time::Duration;

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine as _;
use http::Request;
use serde_json::Value;
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{connect, Error as WebSocketError, Message, WebSocket};

use crate::config::KEEPALIVE_PING_INTERVAL_SECS;
use crate::error::{Error, Result};

/// Streaming WebSocket connection to the sandbox process runtime.
pub struct ProcessSocket {
    socket: WebSocket<MaybeTlsStream<TcpStream>>,
}

impl ProcessSocket {
    /// Connect to a process runtime WebSocket path with bearer-token auth.
    pub fn connect(base_url: &str, token: &str, path: &str) -> Result<Self> {
        let request = Request::builder()
            .uri(ws_url(base_url, path)?)
            .header("Authorization", format!("Bearer {token}"))
            .body(())?;
        let (mut socket, _response) = connect(request)?;
        set_read_timeout(
            &mut socket,
            Some(Duration::from_secs(KEEPALIVE_PING_INTERVAL_SECS / 2)),
        )?;
        Ok(Self { socket })
    }

    /// Send a JSON frame to the process runtime.
    pub fn send_json(&mut self, payload: &Value) -> Result<()> {
        self.socket
            .send(Message::Text(payload.to_string().into()))?;
        Ok(())
    }

    /// Send stdin bytes encoded in the sandbox runtime protocol.
    pub fn send_stdin(&mut self, data: impl AsRef<[u8]>) -> Result<()> {
        self.send_json(&serde_json::json!({
            "type": "stdin",
            "data": encode_runtime_data(data)
        }))
    }

    /// Send a WebSocket ping frame.
    pub fn send_ping(&mut self) -> Result<()> {
        self.socket
            .send(Message::Ping(b"watasu-sdk".to_vec().into()))?;
        Ok(())
    }

    /// Read the next JSON process frame.
    pub fn next_frame(&mut self) -> Result<Option<Value>> {
        loop {
            match self.socket.read() {
                Ok(message) => match message {
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
                    Message::Ping(payload) => self.socket.send(Message::Pong(payload))?,
                    Message::Pong(_) => continue,
                    Message::Frame(_) => continue,
                },
                Err(WebSocketError::Io(error))
                    if matches!(error.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) =>
                {
                    self.send_ping()?;
                    continue;
                }
                Err(error) => return Err(error.into()),
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

/// Decode base64 stdout/stderr frame data from the sandbox runtime protocol.
pub fn decode_runtime_data(value: &str) -> String {
    BASE64
        .decode(value)
        .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
        .unwrap_or_else(|_| value.to_string())
}

fn set_read_timeout(
    socket: &mut WebSocket<MaybeTlsStream<TcpStream>>,
    timeout: Option<Duration>,
) -> Result<()> {
    match socket.get_mut() {
        MaybeTlsStream::Plain(stream) => stream.set_read_timeout(timeout)?,
        MaybeTlsStream::Rustls(stream) => stream.sock.set_read_timeout(timeout)?,
        _ => {
            return Err(Error::Sandbox(
                "unsupported websocket transport stream".into(),
            ))
        }
    }
    Ok(())
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
