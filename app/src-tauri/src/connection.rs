//! Request/response multiplexing over one engine stdio connection.
//!
//! Role: the transport-independent core of the engine bridge. It writes requests
//! as NDJSON, reads and validates every line coming back, routes responses to the
//! waiting caller by `id`, and hands events to an [`EventSink`]. It knows nothing
//! about processes or Tauri, so tests drive it with in-memory pipes.
//!
//! Every request has a deadline covering both the write and the wait for the
//! answer: a hung engine must not hang the UI. A response that arrives after its
//! deadline (or for an id we never sent) is logged and dropped.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Serialize;
use serde_json::Value;
use tokio::io::{AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};
use tokio::sync::{Mutex as AsyncMutex, oneshot};

use crate::line_reader::{BoundedLineReader, LineRead};
use crate::protocol::{self, EngineEvent, Incoming, MAX_LINE_BYTES};

/// Receives validated engine events.
pub trait EventSink: Send + Sync + 'static {
    fn engine_event(&self, event: EngineEvent);
}

/// Failure of a single request, serialized to the frontend as-is.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum RequestError {
    /// The engine answered with an error object.
    Engine { code: String, message: String },
    /// No answer within the deadline.
    Timeout { method: String, timeout_ms: u64 },
    /// The engine is not running or the connection broke.
    Disconnected { detail: String },
    /// The request was rejected before it reached the engine.
    InvalidInput { message: String },
}

impl std::fmt::Display for RequestError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RequestError::Engine { code, message } => write!(f, "engine error {code}: {message}"),
            RequestError::Timeout { method, timeout_ms } => {
                write!(f, "{method} timed out after {timeout_ms} ms")
            }
            RequestError::Disconnected { detail } => write!(f, "engine disconnected: {detail}"),
            RequestError::InvalidInput { message } => write!(f, "invalid input: {message}"),
        }
    }
}

impl std::error::Error for RequestError {}

type Reply = oneshot::Sender<Result<Value, RequestError>>;
type Pending = Arc<Mutex<HashMap<u64, Reply>>>;

/// One live connection to an engine.
pub struct Connection {
    writer: AsyncMutex<Box<dyn AsyncWrite + Send + Unpin>>,
    pending: Pending,
    next_id: AtomicU64,
    closed: Arc<AtomicBool>,
}

impl Connection {
    /// Starts the reader task and returns the connection plus a receiver that
    /// fires once with a reason when the engine side of the connection ends.
    ///
    /// Must be called inside a Tokio runtime.
    pub fn spawn<R, W>(
        reader: R,
        writer: W,
        sink: Arc<dyn EventSink>,
    ) -> (Arc<Connection>, oneshot::Receiver<String>)
    where
        R: AsyncRead + Send + Unpin + 'static,
        W: AsyncWrite + Send + Unpin + 'static,
    {
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let closed = Arc::new(AtomicBool::new(false));
        let (closed_tx, closed_rx) = oneshot::channel();
        tokio::spawn(read_loop(
            BoundedLineReader::new(BufReader::new(reader), MAX_LINE_BYTES),
            pending.clone(),
            closed.clone(),
            sink,
            closed_tx,
        ));
        let conn = Connection {
            writer: AsyncMutex::new(Box::new(writer)),
            pending,
            next_id: AtomicU64::new(1),
            closed,
        };
        (Arc::new(conn), closed_rx)
    }

    /// Whether the engine side has gone away.
    pub fn is_closed(&self) -> bool {
        self.closed.load(Ordering::SeqCst)
    }

    /// Sends `method` with `params` and waits for the matching response.
    pub async fn request(
        &self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value, RequestError> {
        if self.is_closed() {
            return Err(RequestError::Disconnected {
                detail: "connection closed".into(),
            });
        }
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let mut line = protocol::encode_request(id, method, &params).map_err(|e| {
            RequestError::InvalidInput {
                message: format!("cannot encode params: {e}"),
            }
        })?;
        line.push('\n');
        let (tx, rx) = oneshot::channel();
        lock_pending(&self.pending).insert(id, tx);

        let exchange = async {
            {
                let mut writer = self.writer.lock().await;
                writer.write_all(line.as_bytes()).await?;
                writer.flush().await?;
            }
            Ok::<_, std::io::Error>(rx.await)
        };
        let outcome = tokio::time::timeout(timeout, exchange).await;
        match outcome {
            Ok(Ok(Ok(reply))) => reply,
            Ok(Ok(Err(_))) => Err(RequestError::Disconnected {
                detail: "connection closed before the response arrived".into(),
            }),
            Ok(Err(io)) => {
                lock_pending(&self.pending).remove(&id);
                log::error!("writing {method} (id {id}) to the engine failed: {io}");
                Err(RequestError::Disconnected {
                    detail: format!("cannot write to engine: {io}"),
                })
            }
            Err(_) => {
                lock_pending(&self.pending).remove(&id);
                let timeout_ms = u64::try_from(timeout.as_millis()).unwrap_or(u64::MAX);
                log::warn!("{method} (id {id}) timed out after {timeout_ms} ms");
                Err(RequestError::Timeout {
                    method: method.to_string(),
                    timeout_ms,
                })
            }
        }
    }

    /// Fails every waiting request; used when the process is known to be gone
    /// even though the pipe has not reported EOF yet.
    pub fn fail_all(&self, detail: &str) {
        self.closed.store(true, Ordering::SeqCst);
        fail_pending(&self.pending, detail);
    }
}

/// The mutex only guards a HashMap and no code panics while holding it, but if a
/// panic ever poisoned it we still want the bridge to keep routing responses.
fn lock_pending(pending: &Pending) -> std::sync::MutexGuard<'_, HashMap<u64, Reply>> {
    pending
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn fail_pending(pending: &Pending, detail: &str) {
    let drained: Vec<Reply> = lock_pending(pending).drain().map(|(_, tx)| tx).collect();
    for tx in drained {
        // The receiver may already have timed out and gone; nothing to tell then.
        let _ = tx.send(Err(RequestError::Disconnected {
            detail: detail.to_string(),
        }));
    }
}

async fn read_loop<R: tokio::io::AsyncBufRead + Unpin>(
    mut lines: BoundedLineReader<R>,
    pending: Pending,
    closed: Arc<AtomicBool>,
    sink: Arc<dyn EventSink>,
    closed_tx: oneshot::Sender<String>,
) {
    let reason = loop {
        match lines.next_line().await {
            Ok(LineRead::Line(bytes)) => dispatch(&bytes, &pending, sink.as_ref()),
            Ok(LineRead::TooLong { length }) => {
                log::error!("rejected engine line of {length} bytes (limit {MAX_LINE_BYTES})");
            }
            Ok(LineRead::Eof) => break "engine closed its output".to_string(),
            Err(e) => {
                log::error!("reading engine output failed: {e}");
                break format!("reading engine output failed: {e}");
            }
        }
    };
    closed.store(true, Ordering::SeqCst);
    fail_pending(&pending, &reason);
    // The supervisor may have stopped listening (e.g. during shutdown).
    let _ = closed_tx.send(reason);
}

fn dispatch(bytes: &[u8], pending: &Pending, sink: &dyn EventSink) {
    match protocol::parse_line(bytes) {
        Ok(None) => {}
        Ok(Some(Incoming::Response { id, outcome })) => {
            let waiter = lock_pending(pending).remove(&id);
            match waiter {
                Some(tx) => {
                    let reply = outcome.map_err(|e| RequestError::Engine {
                        code: e.code,
                        message: e.message,
                    });
                    if tx.send(reply).is_err() {
                        log::warn!("response {id} arrived but its caller is gone");
                    }
                }
                None => {
                    log::warn!("dropping engine response for unknown or timed-out request id {id}")
                }
            }
        }
        Ok(Some(Incoming::OrphanError(e))) => {
            log::error!(
                "engine could not read a request: {} ({})",
                e.code,
                e.message
            );
        }
        Ok(Some(Incoming::Event(EngineEvent::Unknown { name }))) => {
            log::warn!("ignoring unknown engine event {name:?}");
        }
        Ok(Some(Incoming::Event(event))) => sink.engine_event(event),
        Err(e) => {
            log::error!(
                "rejected engine line: {e}; line: {}",
                protocol::preview(bytes)
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use tokio::io::{AsyncBufReadExt, DuplexStream, Lines};

    #[derive(Default)]
    struct RecordingSink(Mutex<Vec<EngineEvent>>);

    impl EventSink for RecordingSink {
        fn engine_event(&self, event: EngineEvent) {
            self.0.lock().unwrap().push(event);
        }
    }

    /// The fake engine's end of both pipes.
    struct EngineSide {
        requests: Lines<BufReader<DuplexStream>>,
        output: DuplexStream,
    }

    impl EngineSide {
        async fn next_request(&mut self) -> Value {
            let line = self
                .requests
                .next_line()
                .await
                .unwrap()
                .expect("request line");
            serde_json::from_str(&line).unwrap()
        }

        async fn send(&mut self, line: &str) {
            self.output.write_all(line.as_bytes()).await.unwrap();
            self.output.write_all(b"\n").await.unwrap();
        }
    }

    fn setup() -> (
        Arc<Connection>,
        oneshot::Receiver<String>,
        EngineSide,
        Arc<RecordingSink>,
    ) {
        let (app_out, engine_in) = tokio::io::duplex(1 << 16);
        let (engine_out, app_in) = tokio::io::duplex(1 << 16);
        let sink = Arc::new(RecordingSink::default());
        let (conn, closed) = Connection::spawn(app_in, app_out, sink.clone());
        let engine = EngineSide {
            requests: BufReader::new(engine_in).lines(),
            output: engine_out,
        };
        (conn, closed, engine, sink)
    }

    const LONG: Duration = Duration::from_secs(5);

    #[tokio::test]
    async fn matches_out_of_order_responses_by_id() {
        let (conn, _closed, mut engine, _sink) = setup();
        let c1 = conn.clone();
        let c2 = conn.clone();
        let first = tokio::spawn(async move { c1.request("devices.list", json!({}), LONG).await });
        let r1 = engine.next_request().await;
        let second = tokio::spawn(async move { c2.request("engine.info", json!({}), LONG).await });
        let r2 = engine.next_request().await;
        assert_eq!(r1["method"], "devices.list");
        assert_eq!(r2["method"], "engine.info");
        assert_ne!(r1["id"], r2["id"]);
        // Answer the second request first.
        engine
            .send(&format!(r#"{{"id":{},"result":{{"which":2}}}}"#, r2["id"]))
            .await;
        engine
            .send(&format!(r#"{{"id":{},"result":{{"which":1}}}}"#, r1["id"]))
            .await;
        assert_eq!(first.await.unwrap().unwrap(), json!({"which": 1}));
        assert_eq!(second.await.unwrap().unwrap(), json!({"which": 2}));
    }

    #[tokio::test]
    async fn engine_error_is_returned() {
        let (conn, _closed, mut engine, _sink) = setup();
        let task = tokio::spawn(async move { conn.request("nope", json!({}), LONG).await });
        let req = engine.next_request().await;
        engine
            .send(&format!(
                r#"{{"id":{},"error":{{"code":"unknown_method","message":"nope"}}}}"#,
                req["id"]
            ))
            .await;
        assert_eq!(
            task.await.unwrap(),
            Err(RequestError::Engine {
                code: "unknown_method".into(),
                message: "nope".into()
            })
        );
    }

    #[tokio::test]
    async fn timeout_then_late_response_is_dropped() {
        let (conn, _closed, mut engine, _sink) = setup();
        let err = conn
            .request("hardware.detect", json!({}), Duration::from_millis(50))
            .await
            .unwrap_err();
        assert_eq!(
            err,
            RequestError::Timeout {
                method: "hardware.detect".into(),
                timeout_ms: 50
            }
        );
        let late = engine.next_request().await;
        assert!(lock_pending(&conn.pending).is_empty());
        // A late answer must not disturb the next request.
        engine
            .send(&format!(r#"{{"id":{},"result":{{}}}}"#, late["id"]))
            .await;
        let c = conn.clone();
        let task = tokio::spawn(async move { c.request("engine.info", json!({}), LONG).await });
        let req = engine.next_request().await;
        engine
            .send(&format!(r#"{{"id":{},"result":{{"ok":true}}}}"#, req["id"]))
            .await;
        assert_eq!(task.await.unwrap().unwrap(), json!({"ok": true}));
    }

    #[tokio::test]
    async fn malformed_lines_are_skipped_and_events_forwarded() {
        let (conn, _closed, mut engine, sink) = setup();
        let task = tokio::spawn(async move { conn.request("engine.info", json!({}), LONG).await });
        let req = engine.next_request().await;
        engine.send("this is not json").await;
        engine.send(r#"{"id":"x","result":{}}"#).await;
        engine
            .send(r#"{"event":"subtitle","data":{"utterance_id":-4}}"#)
            .await;
        engine.send(r#"{"event":"mystery","data":{}}"#).await;
        engine
            .send(r#"{"event":"log","data":{"level":"info","message":"hello"}}"#)
            .await;
        engine.output.write_all(&[0xff, 0xfe, b'\n']).await.unwrap();
        engine
            .send(&format!(
                r#"{{"id":{},"result":{{"version":"0.1.0"}}}}"#,
                req["id"]
            ))
            .await;
        assert_eq!(task.await.unwrap().unwrap(), json!({"version": "0.1.0"}));
        let events = sink.0.lock().unwrap().clone();
        assert_eq!(events.len(), 1);
        assert!(matches!(&events[0], EngineEvent::Log(l) if l.message == "hello"));
    }

    #[tokio::test]
    async fn over_long_line_does_not_break_the_stream() {
        let (conn, _closed, mut engine, _sink) = setup();
        let task = tokio::spawn(async move { conn.request("engine.info", json!({}), LONG).await });
        let req = engine.next_request().await;
        let huge = format!(
            r#"{{"event":"log","data":{{"level":"info","message":"{}"}}}}"#,
            "x".repeat(MAX_LINE_BYTES)
        );
        let writer = tokio::spawn(async move {
            engine.send(&huge).await;
            engine
                .send(&format!(r#"{{"id":{},"result":{{}}}}"#, req["id"]))
                .await;
            engine
        });
        assert_eq!(task.await.unwrap().unwrap(), json!({}));
        writer.await.unwrap();
    }

    #[tokio::test]
    async fn eof_fails_pending_requests_and_reports_close() {
        let (conn, closed, mut engine, _sink) = setup();
        let c = conn.clone();
        let task = tokio::spawn(async move { c.request("session.start", json!({}), LONG).await });
        engine.next_request().await;
        drop(engine);
        let err = task.await.unwrap().unwrap_err();
        assert!(matches!(err, RequestError::Disconnected { .. }), "{err:?}");
        assert_eq!(closed.await.unwrap(), "engine closed its output");
        assert!(conn.is_closed());
        assert!(matches!(
            conn.request("engine.info", json!({}), LONG).await,
            Err(RequestError::Disconnected { .. })
        ));
    }

    #[tokio::test]
    async fn fail_all_rejects_waiters() {
        let (conn, _closed, mut engine, _sink) = setup();
        let c = conn.clone();
        let task = tokio::spawn(async move { c.request("session.stop", json!({}), LONG).await });
        engine.next_request().await;
        conn.fail_all("process exited");
        assert_eq!(
            task.await.unwrap(),
            Err(RequestError::Disconnected {
                detail: "process exited".into()
            })
        );
    }
}
