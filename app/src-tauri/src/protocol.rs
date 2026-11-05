//! Engine protocol (stdio NDJSON) as seen from the Tauri side.
//!
//! Role in the architecture: every line the Python engine writes to stdout passes
//! through [`parse_line`] before anything else touches it. The engine is a separate
//! process (and in development may be any command the developer points us at), so
//! its output is treated as untrusted input: shape, types and ranges are checked here
//! and malformed lines become a [`ParseError`] instead of a panic.
//!
//! Field names and value sets follow `docs/specs/app-architecture.md` and the
//! engine-side detail in `docs/specs/pipeline-engine.md`.

use std::fmt;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

/// Protocol version this app speaks (`engine.info.protocol_version`).
pub const PROTOCOL_VERSION: u64 = 1;

/// Upper bound for one NDJSON line, matching the engine's own limit.
pub const MAX_LINE_BYTES: usize = 1024 * 1024;

/// Upper bound for user-visible text fields; a subtitle line far beyond this is
/// a sign of a runaway model or a broken engine, not something worth rendering.
const MAX_TEXT_CHARS: usize = 10_000;
const MAX_EVENT_NAME_CHARS: usize = 64;
const MAX_CODE_CHARS: usize = 100;
const MAX_LANGUAGE_CHARS: usize = 16;
const MAX_MODEL_CHARS: usize = 200;

/// Error object of a failed response (`{"code", "message"}`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EngineError {
    pub code: String,
    pub message: String,
}

/// One validated message from the engine.
#[derive(Debug, Clone, PartialEq)]
pub enum Incoming {
    /// Answer to the request with the same `id`.
    Response {
        id: u64,
        outcome: Result<Value, EngineError>,
    },
    /// Error answer with `"id": null`: the engine could not even read our request.
    /// It cannot be matched to a caller, so it is only logged.
    OrphanError(EngineError),
    /// Unsolicited event.
    Event(EngineEvent),
}

/// Events the UI understands; unknown names are kept so they can be logged.
#[derive(Debug, Clone, PartialEq)]
pub enum EngineEvent {
    Subtitle(SubtitlePayload),
    Status(StatusPayload),
    DownloadProgress(DownloadProgressPayload),
    Log(LogPayload),
    Unknown { name: String },
}

/// `subtitle` event: one `SubtitleUpdate` plus the session it belongs to.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SubtitlePayload {
    pub utterance_id: u64,
    pub original: String,
    pub translation: String,
    pub source_language: String,
    pub target_language: String,
    pub is_final: bool,
    pub start_sample: u64,
    pub end_sample: u64,
    /// Added by pipeline-engine.md; optional so an engine following only the
    /// architecture spec is still accepted.
    #[serde(default)]
    pub session_id: Option<u64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum EngineState {
    Idle,
    Loading,
    Running,
    Error,
}

/// `status` event.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StatusPayload {
    pub state: EngineState,
    #[serde(default)]
    pub detail: Option<String>,
    #[serde(default)]
    pub session_id: Option<u64>,
    #[serde(default)]
    pub code: Option<String>,
    /// Extra facts for some codes (Milestone 13: `translation_model_missing`
    /// carries `{"models": [...]}`); absent otherwise, and left out when
    /// forwarded so other payloads keep their exact shape.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data: Option<Map<String, Value>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DownloadState {
    Downloading,
    Done,
    Error,
}

fn default_download_state() -> DownloadState {
    DownloadState::Downloading
}

/// `download_progress` event.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DownloadProgressPayload {
    pub model: String,
    pub downloaded_bytes: u64,
    pub total_bytes: u64,
    #[serde(default = "default_download_state")]
    pub state: DownloadState,
    #[serde(default)]
    pub error: Option<String>,
    /// What is being downloaded (Milestone 13): `asr`, `gpu_runtime` or
    /// `translation`; ASR and translation models may share a name. Kept as a
    /// checked string so a kind added by a newer engine is still forwarded.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<String>,
}

const MAX_KIND_CHARS: usize = 32;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LogLevel {
    Debug,
    Info,
    Warning,
    Error,
}

/// `log` event.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LogPayload {
    pub level: LogLevel,
    pub message: String,
}

/// Why a line from the engine was rejected.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    NotUtf8,
    InvalidJson(String),
    NotObject,
    /// Valid JSON object that does not match the protocol.
    Shape(String),
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ParseError::NotUtf8 => write!(f, "line is not valid UTF-8"),
            ParseError::InvalidJson(e) => write!(f, "line is not valid JSON: {e}"),
            ParseError::NotObject => write!(f, "line is not a JSON object"),
            ParseError::Shape(e) => write!(f, "message does not match the protocol: {e}"),
        }
    }
}

impl std::error::Error for ParseError {}

fn shape(msg: impl Into<String>) -> ParseError {
    ParseError::Shape(msg.into())
}

/// Parses and validates one stdout line.
///
/// Returns `Ok(None)` for blank lines (the engine may emit them and they carry
/// nothing), `Ok(Some(_))` for a valid message and `Err` for anything else.
pub fn parse_line(bytes: &[u8]) -> Result<Option<Incoming>, ParseError> {
    let text = std::str::from_utf8(bytes).map_err(|_| ParseError::NotUtf8)?;
    let text = text.trim();
    if text.is_empty() {
        return Ok(None);
    }
    let value: Value =
        serde_json::from_str(text).map_err(|e| ParseError::InvalidJson(e.to_string()))?;
    let Value::Object(obj) = value else {
        return Err(ParseError::NotObject);
    };
    if obj.contains_key("event") {
        if obj.contains_key("id") {
            return Err(shape("message has both \"event\" and \"id\""));
        }
        return parse_event(obj).map(|e| Some(Incoming::Event(e)));
    }
    if obj.contains_key("id") {
        return parse_response(obj).map(Some);
    }
    Err(shape("message has neither \"id\" nor \"event\""))
}

fn parse_response(mut obj: Map<String, Value>) -> Result<Incoming, ParseError> {
    let id = match obj.remove("id") {
        Some(Value::Null) => None,
        // `as_u64` is None for negatives and floats; serde_json never turns
        // booleans into numbers, so `true` cannot sneak in as 1.
        Some(Value::Number(n)) => Some(
            n.as_u64()
                .ok_or_else(|| shape("\"id\" must be a non-negative integer"))?,
        ),
        _ => return Err(shape("\"id\" must be a non-negative integer or null")),
    };
    let result = obj.remove("result");
    let error = obj.remove("error");
    match (id, result, error) {
        (_, Some(_), Some(_)) => Err(shape("response has both \"result\" and \"error\"")),
        (None, Some(_), None) => Err(shape("successful response must have an integer \"id\"")),
        (Some(id), Some(result), None) => {
            if !result.is_object() {
                return Err(shape("\"result\" must be an object"));
            }
            Ok(Incoming::Response {
                id,
                outcome: Ok(result),
            })
        }
        (id, None, Some(error)) => {
            let error = parse_error_object(error)?;
            Ok(match id {
                Some(id) => Incoming::Response {
                    id,
                    outcome: Err(error),
                },
                None => Incoming::OrphanError(error),
            })
        }
        (_, None, None) => Err(shape("response has neither \"result\" nor \"error\"")),
    }
}

fn parse_error_object(value: Value) -> Result<EngineError, ParseError> {
    let error: EngineError = serde_json::from_value(value)
        .map_err(|e| shape(format!("\"error\" must be {{code, message}} strings: {e}")))?;
    check_non_empty_len("error.code", &error.code, MAX_CODE_CHARS)?;
    check_len("error.message", &error.message, MAX_TEXT_CHARS)?;
    Ok(error)
}

fn parse_event(mut obj: Map<String, Value>) -> Result<EngineEvent, ParseError> {
    let name = match obj.remove("event") {
        Some(Value::String(s)) => s,
        _ => return Err(shape("\"event\" must be a string")),
    };
    check_non_empty_len("event", &name, MAX_EVENT_NAME_CHARS)?;
    let data = match obj.remove("data") {
        Some(v @ Value::Object(_)) => v,
        _ => return Err(shape(format!("event {name:?}: \"data\" must be an object"))),
    };
    let ctx = |e: serde_json::Error| shape(format!("event {name:?}: {e}"));
    let event = match name.as_str() {
        "subtitle" => {
            let p: SubtitlePayload = serde_json::from_value(data).map_err(ctx)?;
            validate_subtitle(&p)?;
            EngineEvent::Subtitle(p)
        }
        "status" => {
            let p: StatusPayload = serde_json::from_value(data).map_err(ctx)?;
            if let Some(d) = &p.detail {
                check_len("status.detail", d, MAX_TEXT_CHARS)?;
            }
            if let Some(c) = &p.code {
                check_non_empty_len("status.code", c, MAX_CODE_CHARS)?;
            }
            if let Some(d) = &p.data {
                // The whole line is already bounded (MAX_LINE_BYTES); this keeps
                // one status from carrying a large blob into every window.
                let size = serde_json::to_vec(d).map_or(usize::MAX, |v| v.len());
                if size > MAX_TEXT_CHARS {
                    return Err(shape(format!(
                        "status.data is {size} bytes, limit {MAX_TEXT_CHARS}"
                    )));
                }
            }
            EngineEvent::Status(p)
        }
        "download_progress" => {
            let p: DownloadProgressPayload = serde_json::from_value(data).map_err(ctx)?;
            check_non_empty_len("download_progress.model", &p.model, MAX_MODEL_CHARS)?;
            if let Some(e) = &p.error {
                check_len("download_progress.error", e, MAX_TEXT_CHARS)?;
            }
            if let Some(k) = &p.kind
                && (k.is_empty()
                    || k.len() > MAX_KIND_CHARS
                    || !k.bytes().all(|b| b.is_ascii_lowercase() || b == b'_'))
            {
                return Err(shape(format!(
                    "download_progress.kind is not an identifier: {k:?}"
                )));
            }
            EngineEvent::DownloadProgress(p)
        }
        "log" => {
            let p: LogPayload = serde_json::from_value(data).map_err(ctx)?;
            check_len("log.message", &p.message, MAX_TEXT_CHARS)?;
            EngineEvent::Log(p)
        }
        _ => EngineEvent::Unknown { name },
    };
    Ok(event)
}

fn validate_subtitle(p: &SubtitlePayload) -> Result<(), ParseError> {
    check_len("subtitle.original", &p.original, MAX_TEXT_CHARS)?;
    check_len("subtitle.translation", &p.translation, MAX_TEXT_CHARS)?;
    check_language("subtitle.source_language", &p.source_language)?;
    check_language("subtitle.target_language", &p.target_language)?;
    if p.end_sample < p.start_sample {
        return Err(shape("subtitle.end_sample is before start_sample"));
    }
    Ok(())
}

fn check_len(field: &str, s: &str, max_chars: usize) -> Result<(), ParseError> {
    if s.chars().count() > max_chars {
        return Err(shape(format!(
            "{field} is longer than {max_chars} characters"
        )));
    }
    Ok(())
}

fn check_non_empty_len(field: &str, s: &str, max_chars: usize) -> Result<(), ParseError> {
    if s.is_empty() {
        return Err(shape(format!("{field} must not be empty")));
    }
    check_len(field, s, max_chars)
}

/// Language tags are used as HTML `lang` attributes in the UI, so only allow the
/// BCP 47 character set (letters, digits, hyphen). Empty is allowed: a filtered
/// final result may not carry a detected language.
fn check_language(field: &str, s: &str) -> Result<(), ParseError> {
    if s.len() > MAX_LANGUAGE_CHARS || !s.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err(shape(format!("{field} is not a language tag: {s:?}")));
    }
    Ok(())
}

/// Serializes one request line (without the trailing newline).
pub fn encode_request(id: u64, method: &str, params: &Value) -> Result<String, serde_json::Error> {
    serde_json::to_string(&serde_json::json!({
        "id": id,
        "method": method,
        "params": params,
    }))
}

/// Shortens a rejected line for logging so a 1 MiB garbage line does not flood the log.
pub fn preview(bytes: &[u8]) -> String {
    const MAX: usize = 160;
    let text = String::from_utf8_lossy(bytes);
    let mut out: String = text.chars().take(MAX).collect();
    if text.chars().count() > MAX {
        out.push('…');
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn parse(s: &str) -> Result<Option<Incoming>, ParseError> {
        parse_line(s.as_bytes())
    }

    fn subtitle_json() -> Value {
        json!({
            "event": "subtitle",
            "data": {
                "utterance_id": 3, "original": "こんにちは", "translation": "你好",
                "source_language": "ja", "target_language": "zh-TW", "is_final": false,
                "start_sample": 16000, "end_sample": 32000, "session_id": 1
            }
        })
    }

    #[test]
    fn blank_lines_are_ignored() {
        assert_eq!(parse(""), Ok(None));
        assert_eq!(parse("  \r"), Ok(None));
    }

    #[test]
    fn success_response() {
        let got = parse(r#"{"id":7,"result":{"session_id":2}}"#)
            .unwrap()
            .unwrap();
        assert_eq!(
            got,
            Incoming::Response {
                id: 7,
                outcome: Ok(json!({"session_id": 2}))
            }
        );
    }

    #[test]
    fn error_response() {
        let got = parse(r#"{"id":1,"error":{"code":"session_active","message":"busy"}}"#)
            .unwrap()
            .unwrap();
        assert_eq!(
            got,
            Incoming::Response {
                id: 1,
                outcome: Err(EngineError {
                    code: "session_active".into(),
                    message: "busy".into()
                })
            }
        );
    }

    #[test]
    fn null_id_error_is_orphan() {
        let got = parse(r#"{"id":null,"error":{"code":"parse_error","message":"bad"}}"#)
            .unwrap()
            .unwrap();
        assert!(matches!(got, Incoming::OrphanError(e) if e.code == "parse_error"));
    }

    #[test]
    fn rejects_non_utf8_and_non_json() {
        assert_eq!(parse_line(&[0xff, 0xfe, b'{']), Err(ParseError::NotUtf8));
        assert!(matches!(
            parse("{not json"),
            Err(ParseError::InvalidJson(_))
        ));
        assert_eq!(parse("[1,2]"), Err(ParseError::NotObject));
        assert_eq!(parse("42"), Err(ParseError::NotObject));
    }

    #[test]
    fn rejects_bad_ids() {
        for line in [
            r#"{"id":-1,"result":{}}"#,
            r#"{"id":1.5,"result":{}}"#,
            r#"{"id":"1","result":{}}"#,
            r#"{"id":true,"result":{}}"#,
            r#"{"id":null,"result":{}}"#,
        ] {
            assert!(matches!(parse(line), Err(ParseError::Shape(_))), "{line}");
        }
    }

    #[test]
    fn rejects_ambiguous_responses() {
        for line in [
            r#"{"id":1,"result":{},"error":{"code":"x","message":"y"}}"#,
            r#"{"id":1}"#,
            r#"{"id":1,"result":[1]}"#,
            r#"{"id":1,"error":"boom"}"#,
            r#"{"id":1,"error":{"code":"","message":"y"}}"#,
            r#"{"id":1,"event":"log","data":{}}"#,
            r#"{"hello":1}"#,
        ] {
            assert!(matches!(parse(line), Err(ParseError::Shape(_))), "{line}");
        }
    }

    #[test]
    fn subtitle_event() {
        let got = parse(&subtitle_json().to_string()).unwrap().unwrap();
        let Incoming::Event(EngineEvent::Subtitle(s)) = got else {
            panic!("expected subtitle, got {got:?}");
        };
        assert_eq!(s.utterance_id, 3);
        assert!(!s.is_final);
        assert_eq!(s.session_id, Some(1));
    }

    #[test]
    fn subtitle_without_session_id_is_accepted() {
        let mut v = subtitle_json();
        v["data"].as_object_mut().unwrap().remove("session_id");
        let got = parse(&v.to_string()).unwrap().unwrap();
        assert!(matches!(got, Incoming::Event(EngineEvent::Subtitle(s)) if s.session_id.is_none()));
    }

    #[test]
    fn subtitle_validation() {
        let cases: Vec<(&str, Value)> = vec![
            ("utterance_id", json!(-1)),
            ("utterance_id", json!("3")),
            ("is_final", json!(1)),
            ("original", json!(null)),
            ("source_language", json!("<script>")),
            ("end_sample", json!(10)),
            ("original", json!("あ".repeat(MAX_TEXT_CHARS + 1))),
        ];
        for (field, value) in cases {
            let mut v = subtitle_json();
            v["data"][field] = value.clone();
            assert!(
                matches!(parse(&v.to_string()), Err(ParseError::Shape(_))),
                "{field} = {value}"
            );
        }
    }

    #[test]
    fn status_event() {
        let got = parse(r#"{"event":"status","data":{"state":"error","detail":"no mic","session_id":2,"code":"device_lost"}}"#)
            .unwrap()
            .unwrap();
        let Incoming::Event(EngineEvent::Status(s)) = got else {
            panic!("expected status");
        };
        assert_eq!(s.state, EngineState::Error);
        assert_eq!(s.code.as_deref(), Some("device_lost"));
        assert!(matches!(
            parse(r#"{"event":"status","data":{"state":"sleeping"}}"#),
            Err(ParseError::Shape(_))
        ));
    }

    #[test]
    fn status_event_keeps_data_for_translation_model_missing() {
        let line = r#"{"event":"status","data":{"state":"error","detail":"missing","session_id":3,"code":"translation_model_missing","data":{"models":["qwen3.5:9b"]}}}"#;
        let Some(Incoming::Event(EngineEvent::Status(s))) = parse(line).unwrap() else {
            panic!("expected status");
        };
        assert_eq!(s.data.as_ref().unwrap()["models"], json!(["qwen3.5:9b"]));
        // Forwarded to the webview unchanged.
        assert_eq!(
            serde_json::to_value(&s).unwrap()["data"],
            json!({"models": ["qwen3.5:9b"]})
        );
        // Without data the forwarded payload has no `data` key at all.
        let Some(Incoming::Event(EngineEvent::Status(plain))) =
            parse(r#"{"event":"status","data":{"state":"idle"}}"#).unwrap()
        else {
            panic!("expected status");
        };
        assert!(serde_json::to_value(&plain).unwrap().get("data").is_none());
        for bad in [
            r#"{"event":"status","data":{"state":"error","data":[1]}}"#,
            r#"{"event":"status","data":{"state":"error","data":"x"}}"#,
        ] {
            assert!(matches!(parse(bad), Err(ParseError::Shape(_))), "{bad}");
        }
        let huge = json!({"event": "status", "data": {"state": "error", "data": {"models": ["m".repeat(MAX_TEXT_CHARS)]}}});
        assert!(matches!(
            parse(&huge.to_string()),
            Err(ParseError::Shape(_))
        ));
    }

    #[test]
    fn download_progress_kind() {
        let got = parse(r#"{"event":"download_progress","data":{"model":"qwen3.5:9b","kind":"translation","downloaded_bytes":0,"total_bytes":0,"state":"downloading","error":null}}"#)
            .unwrap()
            .unwrap();
        let Incoming::Event(EngineEvent::DownloadProgress(p)) = got else {
            panic!("expected download_progress");
        };
        assert_eq!(p.kind.as_deref(), Some("translation"));
        assert_eq!(
            serde_json::to_value(&p).unwrap()["kind"],
            json!("translation")
        );
        for bad in [json!(""), json!("Translation"), json!("<b>"), json!(1)] {
            let line = json!({"event": "download_progress", "data": {
                "model": "m", "kind": bad, "downloaded_bytes": 0, "total_bytes": 0
            }});
            assert!(
                matches!(parse(&line.to_string()), Err(ParseError::Shape(_))),
                "{line}"
            );
        }
    }

    #[test]
    fn download_progress_defaults_state() {
        let got = parse(r#"{"event":"download_progress","data":{"model":"base","downloaded_bytes":5,"total_bytes":10}}"#)
            .unwrap()
            .unwrap();
        assert!(matches!(
            got,
            Incoming::Event(EngineEvent::DownloadProgress(p)) if p.state == DownloadState::Downloading
        ));
    }

    #[test]
    fn log_event_and_unknown_event() {
        assert!(matches!(
            parse(r#"{"event":"log","data":{"level":"warning","message":"slow"}}"#),
            Ok(Some(Incoming::Event(EngineEvent::Log(_))))
        ));
        assert!(matches!(
            parse(r#"{"event":"log","data":{"level":"loud","message":"x"}}"#),
            Err(ParseError::Shape(_))
        ));
        assert_eq!(
            parse(r#"{"event":"future_thing","data":{}}"#),
            Ok(Some(Incoming::Event(EngineEvent::Unknown {
                name: "future_thing".into()
            })))
        );
    }

    #[test]
    fn event_requires_object_data() {
        assert!(matches!(
            parse(r#"{"event":"log","data":[1]}"#),
            Err(ParseError::Shape(_))
        ));
        assert!(matches!(
            parse(r#"{"event":"log"}"#),
            Err(ParseError::Shape(_))
        ));
        assert!(matches!(
            parse(r#"{"event":5,"data":{}}"#),
            Err(ParseError::Shape(_))
        ));
    }

    #[test]
    fn encode_request_round_trips() {
        let line = encode_request(9, "session.start", &json!({"mode": "media"})).unwrap();
        assert!(!line.contains('\n'));
        let v: Value = serde_json::from_str(&line).unwrap();
        assert_eq!(
            v,
            json!({"id": 9, "method": "session.start", "params": {"mode": "media"}})
        );
    }

    #[test]
    fn preview_truncates() {
        let long = "x".repeat(500);
        let p = preview(long.as_bytes());
        assert!(p.chars().count() <= 161);
        assert!(p.ends_with('…'));
    }
}
