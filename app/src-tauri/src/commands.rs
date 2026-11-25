//! Tauri commands exposed to the webviews.
//!
//! Role: the only entry points from JavaScript into Rust. Each engine protocol
//! method has one command that validates its arguments (the webview is also an
//! input source) and forwards them through the [`EngineBridge`] with a deadline
//! suited to that method. Overlay commands drive the subtitle window. Which
//! window may call which command is decided by `capabilities/*.json`.

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_opener::OpenerExt;

use crate::connection::RequestError;
use crate::engine::{BridgeStatus, EngineBridge, third_party_licenses_dir};
use crate::obs::{ObsContent, ObsOutput, ObsStatus};
use crate::output::{self, OutputFolder};
use crate::overlay::{MAX_LOCALE_LEN, OVERLAY_LABEL, OverlaySnapshot, OverlayState, OverlayStyle};

type CmdResult<T> = Result<T, RequestError>;

const QUICK: Duration = Duration::from_secs(10);
/// Background methods (`hardware.detect`, `models.list`, `models.validate`,
/// `settings.apply_recommendation`, `settings.preview_recommendation`,
/// `settings.preview_languages`, `models.pull_translation`) run `nvidia-smi` and
/// probe the translation server (2 s timeout per address), and the engine runs
/// them one at a time in arrival order, so a request may first wait for the
/// ones queued before it. `models.delete` uses the same deadline: removing a
/// multi-GB model folder can take seconds on a slow disk.
const BACKGROUND: Duration = Duration::from_secs(45);
/// The engine answers `session.start` after validating, before loading models.
const SESSION_START: Duration = Duration::from_secs(30);
/// The engine joins its worker threads with a 5 s timeout.
const SESSION_STOP: Duration = Duration::from_secs(15);
/// `remote.server_set`, `remote.pair_open` and `remote.pair` open listening or
/// outgoing sockets (and may create the TLS identity on first use), which can
/// take a few seconds beyond the usual quick answer. `remote.host_info` gives
/// up on the compute host after 10 s itself, so this leaves it room to answer.
const REMOTE_SOCKETS: Duration = Duration::from_secs(20);

const MAX_MODEL_ID_CHARS: usize = 100;
/// Translation model names follow the engine's rule (`models.pull_translation`).
const MAX_TRANSLATION_MODEL_CHARS: usize = 200;
/// Language list codes (`zh-TW`) and aliases the engine normalizes (`zh_Hant`).
const MAX_LANGUAGE_CHARS: usize = 16;
/// `models.download` id of the optional NVIDIA cuBLAS runtime (hardware.md).
pub const GPU_RUNTIME_ID: &str = "gpu-runtime";
const MAX_DEVICE_ID_CHARS: usize = 512;
/// The engine caps `settings.subtitle` at 16 KiB; the whole patch is far smaller.
const MAX_SETTINGS_PATCH_BYTES: usize = 64 * 1024;

pub const EVENT_OVERLAY_STATE: &str = "overlay://state";

fn invalid(message: impl Into<String>) -> RequestError {
    RequestError::InvalidInput {
        message: message.into(),
    }
}

/// ASR model ids are registry keys like `large-v3-turbo`.
pub fn validate_model_id(model: &str) -> Result<(), RequestError> {
    if model.is_empty() || model.chars().count() > MAX_MODEL_ID_CHARS {
        return Err(invalid(format!(
            "model id must be 1–{MAX_MODEL_ID_CHARS} characters"
        )));
    }
    if !model
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
    {
        return Err(invalid(
            "model id may only contain letters, digits, '.', '_' and '-'",
        ));
    }
    Ok(())
}

/// Translation model names are server tags such as `qwen3.5:9b` or
/// `hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS`; same rule as the engine
/// (letters, digits and `._:/-`, starting with a letter or digit, no `..`), so
/// nothing path-like reaches the Ollama pull.
pub fn validate_translation_model(model: &str) -> Result<(), RequestError> {
    let count = model.chars().count();
    if count == 0 || count > MAX_TRANSLATION_MODEL_CHARS {
        return Err(invalid(format!(
            "translation model must be 1–{MAX_TRANSLATION_MODEL_CHARS} characters"
        )));
    }
    let first_ok = model
        .chars()
        .next()
        .is_some_and(|c| c.is_ascii_alphanumeric());
    let chars_ok = model
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | ':' | '/' | '-'));
    if !first_ok || !chars_ok || model.contains("..") {
        return Err(invalid(
            "translation model may only contain letters, digits and '._:/-', start with a letter or digit, and not contain '..'",
        ));
    }
    Ok(())
}

pub fn validate_device_id(device_id: Option<&str>) -> Result<(), RequestError> {
    if let Some(id) = device_id
        && (id.is_empty() || id.chars().count() > MAX_DEVICE_ID_CHARS)
    {
        return Err(invalid(format!(
            "device id must be 1–{MAX_DEVICE_ID_CHARS} characters"
        )));
    }
    Ok(())
}

/// `settings.set` takes a partial settings object; the engine validates fields.
pub fn validate_settings_patch(patch: &Value) -> Result<(), RequestError> {
    if !patch.is_object() {
        return Err(invalid("settings must be an object"));
    }
    let size = serde_json::to_vec(patch)
        .map_err(|e| invalid(e.to_string()))?
        .len();
    if size > MAX_SETTINGS_PATCH_BYTES {
        return Err(invalid(format!(
            "settings patch is {size} bytes, limit {MAX_SETTINGS_PATCH_BYTES}"
        )));
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    Media,
    Interpret,
}

impl Mode {
    fn as_str(self) -> &'static str {
        match self {
            Mode::Media => "media",
            Mode::Interpret => "interpret",
        }
    }
}

/// The language combination the settings page previews (`settings.preview_languages`).
/// Every field is optional; the engine fills the rest from the saved settings and
/// validates the codes against its language list.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LanguageChoice {
    pub mode: Option<Mode>,
    pub source_language: Option<String>,
    pub target_language: Option<String>,
    pub partner_language: Option<String>,
}

/// Only the shape of a language code is checked here (letters, digits, `-`,
/// `_`): the list of supported languages lives in the engine, so a new language
/// does not need a new app build.
fn validate_language_code(field: &str, code: &str) -> Result<(), RequestError> {
    if code.is_empty()
        || code.len() > MAX_LANGUAGE_CHARS
        || !code
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_'))
    {
        return Err(invalid(format!(
            "{field} must be a language code of 1–{MAX_LANGUAGE_CHARS} letters, digits, '-' or '_'"
        )));
    }
    Ok(())
}

pub fn preview_languages_params(choice: &LanguageChoice) -> Result<Value, RequestError> {
    let mut params = Map::new();
    if let Some(mode) = choice.mode {
        params.insert("mode".into(), json!(mode.as_str()));
    }
    for (field, value) in [
        ("source_language", &choice.source_language),
        ("target_language", &choice.target_language),
        ("partner_language", &choice.partner_language),
    ] {
        if let Some(code) = value {
            validate_language_code(field, code)?;
            params.insert(field.into(), json!(code));
        }
    }
    Ok(Value::Object(params))
}

pub fn pull_translation_params(model: &str) -> Result<Value, RequestError> {
    validate_translation_model(model)?;
    Ok(json!({ "model": model }))
}

pub fn session_start_params(mode: Mode, device_id: Option<&str>) -> Result<Value, RequestError> {
    validate_device_id(device_id)?;
    // `device_id: null` explicitly means "system default device"; the UI always
    // sends its current choice so the engine never falls back to a stale one.
    Ok(json!({ "mode": mode.as_str(), "device_id": device_id }))
}

/// `models.download` parameters. `accept_license` only exists for the GPU
/// runtime (the engine rejects it for ASR models) and is sent only when the user
/// ticked the NVIDIA EULA box; without it the engine answers
/// `license_not_accepted`, so the gate holds even if the UI misbehaves.
pub fn models_download_params(model: &str, accept_license: bool) -> Result<Value, RequestError> {
    validate_model_id(model)?;
    if model == GPU_RUNTIME_ID {
        if accept_license {
            return Ok(json!({ "model": model, "accept_license": true }));
        }
        return Ok(json!({ "model": model }));
    }
    if accept_license {
        return Err(invalid("accept_license only applies to the GPU runtime"));
    }
    Ok(json!({ "model": model }))
}

/// `models.delete` parameters (competitive-features.md F7). Only downloaded
/// speech recognition models and the furigana dictionary (`furigana-dictionary`,
/// docs/specs/furigana.md) can be deleted; the GPU runtime is refused here so
/// a misbehaving webview cannot even ask, and the engine re-checks the rest
/// (registry id, not in use, path inside the model cache).
pub fn models_delete_params(model: &str) -> Result<Value, RequestError> {
    validate_model_id(model)?;
    if model == GPU_RUNTIME_ID {
        return Err(invalid(
            "models.delete only applies to speech recognition models",
        ));
    }
    Ok(json!({ "model": model }))
}

/// `models.validate` parameters: the candidate partial settings, or none to
/// check the saved settings.
pub fn models_validate_params(settings: Option<&Value>) -> Result<Value, RequestError> {
    match settings {
        Some(patch) => {
            validate_settings_patch(patch)?;
            Ok(json!({ "settings": patch }))
        }
        None => Ok(json!({})),
    }
}

#[tauri::command]
pub async fn engine_info(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("engine.info", json!({}), QUICK).await
}

#[tauri::command]
pub async fn hardware_detect(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge
        .request("hardware.detect", json!({}), BACKGROUND)
        .await
}

#[tauri::command]
pub async fn models_list(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("models.list", json!({}), BACKGROUND).await
}

#[tauri::command]
pub async fn models_download(
    bridge: State<'_, EngineBridge>,
    model: String,
    accept_license: Option<bool>,
) -> CmdResult<Value> {
    let params = models_download_params(&model, accept_license.unwrap_or(false))?;
    bridge.request("models.download", params, QUICK).await
}

/// Deletes a downloaded ASR model; answers `{model, freed_bytes}`.
#[tauri::command]
pub async fn models_delete(bridge: State<'_, EngineBridge>, model: String) -> CmdResult<Value> {
    let params = models_delete_params(&model)?;
    bridge.request("models.delete", params, BACKGROUND).await
}

#[tauri::command]
pub async fn models_validate(
    bridge: State<'_, EngineBridge>,
    settings: Option<Value>,
) -> CmdResult<Value> {
    let params = models_validate_params(settings.as_ref())?;
    bridge.request("models.validate", params, BACKGROUND).await
}

/// Starts downloading a translation model on the user's Ollama server; progress
/// arrives as `download_progress` events with `kind: "translation"`.
#[tauri::command]
pub async fn models_pull_translation(
    bridge: State<'_, EngineBridge>,
    model: String,
) -> CmdResult<Value> {
    let params = pull_translation_params(&model)?;
    bridge
        .request("models.pull_translation", params, BACKGROUND)
        .await
}

#[tauri::command]
pub async fn languages_list(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("languages.list", json!({}), QUICK).await
}

/// What choosing these languages would use and download, without saving.
#[tauri::command]
pub async fn settings_preview_languages(
    bridge: State<'_, EngineBridge>,
    languages: LanguageChoice,
) -> CmdResult<Value> {
    let params = preview_languages_params(&languages)?;
    bridge
        .request("settings.preview_languages", params, BACKGROUND)
        .await
}

#[tauri::command]
pub async fn devices_list(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("devices.list", json!({}), QUICK).await
}

#[tauri::command]
pub async fn settings_get(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("settings.get", json!({}), QUICK).await
}

#[tauri::command]
pub async fn settings_set(bridge: State<'_, EngineBridge>, patch: Value) -> CmdResult<Value> {
    validate_settings_patch(&patch)?;
    bridge.request("settings.set", patch, QUICK).await
}

/// `only_if_new` is the first-run auto-apply: the engine writes only while no
/// settings were saved yet, so a combination the user just saved is kept.
pub fn apply_recommendation_params(only_if_new: bool) -> Value {
    if only_if_new {
        json!({ "only_if_new": true })
    } else {
        json!({})
    }
}

#[tauri::command]
pub async fn settings_apply_recommendation(
    bridge: State<'_, EngineBridge>,
    only_if_new: Option<bool>,
) -> CmdResult<Value> {
    let params = apply_recommendation_params(only_if_new.unwrap_or(false));
    bridge
        .request("settings.apply_recommendation", params, BACKGROUND)
        .await
}

/// What `settings_apply_recommendation` would write, without saving (`differs`
/// tells the UI whether to offer it).
#[tauri::command]
pub async fn settings_preview_recommendation(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge
        .request("settings.preview_recommendation", json!({}), BACKGROUND)
        .await
}

#[tauri::command]
pub async fn session_start(
    bridge: State<'_, EngineBridge>,
    mode: Mode,
    device_id: Option<String>,
) -> CmdResult<Value> {
    let params = session_start_params(mode, device_id.as_deref())?;
    bridge.request("session.start", params, SESSION_START).await
}

#[tauri::command]
pub async fn session_stop(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge
        .request("session.stop", json!({}), SESSION_STOP)
        .await
}

/// The "show translation" switch: saved in the settings and applied to a
/// running session at once.
#[tauri::command]
pub async fn session_set_translation(
    bridge: State<'_, EngineBridge>,
    enabled: bool,
) -> CmdResult<Value> {
    bridge
        .request(
            "session.set_translation",
            json!({ "enabled": enabled }),
            QUICK,
        )
        .await
}

/// Turns furigana on or off (saved as `furigana.enabled`, applied to the
/// running session). The engine answers `model_not_downloaded` when turning it
/// on before the dictionary is downloaded.
#[tauri::command]
pub async fn session_set_furigana(
    bridge: State<'_, EngineBridge>,
    enabled: bool,
) -> CmdResult<Value> {
    bridge
        .request("session.set_furigana", json!({ "enabled": enabled }), QUICK)
        .await
}

// --- Remote compute (T13 contract, docs/specs/remote-compute.md) -------------
//
// The engine validates these again; checking here as well means a misbehaving
// webview cannot even hand the engine a malformed host, port or confirmation.

/// Ports below 1024 need administrator rights on Linux and belong to system
/// services; the compute host never uses them.
/// The main port + 1 is the pairing port, so the main port stops at 65534 (as in the engine).
const REMOTE_PORTS: std::ops::RangeInclusive<u16> = 1024..=65534;
/// Longest DNS name.
const MAX_HOST_CHARS: usize = 253;
const SPKI_HEX_CHARS: usize = 64;
const PAIRING_DIGITS: usize = 6;

fn validate_port(port: Option<u16>) -> Result<(), RequestError> {
    match port {
        Some(p) if !REMOTE_PORTS.contains(&p) => Err(invalid(format!(
            "port must be {}–{}",
            REMOTE_PORTS.start(),
            REMOTE_PORTS.end()
        ))),
        _ => Ok(()),
    }
}

/// A compute host is an IP address (v4 or v6) or a host name: letters, digits,
/// `.`, `:` and `-` only, so no URL, path, user info or whitespace gets through.
pub fn validate_host(host: &str) -> Result<(), RequestError> {
    if host.is_empty()
        || host.len() > MAX_HOST_CHARS
        || !host
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b':' | b'-'))
    {
        return Err(invalid(format!(
            "host must be 1–{MAX_HOST_CHARS} letters, digits, '.', ':' or '-'"
        )));
    }
    Ok(())
}

/// `remote.server_set` parameters. The listen address must be one IP address:
/// the user picks one of this computer's addresses, never a host name that
/// could resolve elsewhere.
pub fn remote_server_set_params(
    enabled: bool,
    listen_host: Option<&str>,
    port: Option<u16>,
) -> Result<Value, RequestError> {
    let mut params = Map::new();
    params.insert("enabled".into(), json!(enabled));
    if let Some(host) = listen_host {
        if host.parse::<std::net::IpAddr>().is_err() {
            return Err(invalid("listen host must be an IP address"));
        }
        params.insert("listen_host".into(), json!(host));
    }
    validate_port(port)?;
    if let Some(port) = port {
        params.insert("port".into(), json!(port));
    }
    Ok(Value::Object(params))
}

/// `remote.pair` parameters: the compute host and its main port (the engine
/// pairs on port + 1).
pub fn remote_pair_params(host: &str, port: Option<u16>) -> Result<Value, RequestError> {
    validate_host(host)?;
    validate_port(port)?;
    let mut params = Map::new();
    params.insert("host".into(), json!(host));
    if let Some(port) = port {
        params.insert("port".into(), json!(port));
    }
    Ok(Value::Object(params))
}

/// `remote.pair_confirm` parameters: the compute host sends the digits the
/// user typed, the other computer sends whether both screens show success.
/// Exactly one of the two, so a confirmation is never ambiguous.
pub fn remote_pair_confirm_params(
    digits: Option<&str>,
    same: Option<bool>,
) -> Result<Value, RequestError> {
    match (digits, same) {
        (Some(digits), None) => {
            // The digits are shown as `123 456`; full-width digits are
            // converted by the UI, anything else is refused.
            let digits: String = digits.chars().filter(|c| !c.is_whitespace()).collect();
            if digits.len() != PAIRING_DIGITS || !digits.bytes().all(|b| b.is_ascii_digit()) {
                return Err(invalid("pairing code must be exactly 6 digits"));
            }
            Ok(json!({ "digits": digits }))
        }
        (None, Some(same)) => Ok(json!({ "same": same })),
        _ => Err(invalid("give exactly one of digits or same")),
    }
}

/// `remote.unpair` parameters: which trusted computer (its SPKI SHA-256) and
/// in which list (`peer` = may connect here, `server` = a paired compute host).
pub fn remote_unpair_params(spki: &str, role: &str) -> Result<Value, RequestError> {
    if spki.len() != SPKI_HEX_CHARS
        || !spki
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(invalid("spki must be 64 lowercase hex digits"));
    }
    if !matches!(role, "peer" | "server") {
        return Err(invalid("role must be \"peer\" or \"server\""));
    }
    Ok(json!({ "spki": spki, "role": role }))
}

/// This computer's identity, compute-host server state, trusted computers and
/// any pairing in progress (`RemoteStatus`).
#[tauri::command]
pub async fn remote_status(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("remote.status", json!({}), QUICK).await
}

/// Turns the compute-host server on or off (saved as `compute.server_enabled`).
#[tauri::command]
pub async fn remote_server_set(
    bridge: State<'_, EngineBridge>,
    enabled: bool,
    listen_host: Option<String>,
    port: Option<u16>,
) -> CmdResult<Value> {
    let params = remote_server_set_params(enabled, listen_host.as_deref(), port)?;
    bridge
        .request("remote.server_set", params, REMOTE_SOCKETS)
        .await
}

/// Replaces this computer's identity (key and certificate) with a new one and
/// clears both lists of paired computers, which no longer recognise it. The
/// way out of a damaged identity (`identity_error`); refused with
/// `session_active` while subtitles run on this computer or another computer
/// is using it.
#[tauri::command]
pub async fn remote_identity_reset(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge
        .request("remote.identity_reset", json!({}), REMOTE_SOCKETS)
        .await
}

/// Drops the computer currently using this compute host.
#[tauri::command]
pub async fn remote_disconnect(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("remote.disconnect", json!({}), QUICK).await
}

/// Lets a new computer pair with this compute host; progress arrives as
/// `engine://remote` events.
#[tauri::command]
pub async fn remote_pair_open(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge
        .request("remote.pair_open", json!({}), REMOTE_SOCKETS)
        .await
}

/// Starts pairing with a compute host; progress arrives as `engine://remote`.
#[tauri::command]
pub async fn remote_pair(
    bridge: State<'_, EngineBridge>,
    host: String,
    port: Option<u16>,
) -> CmdResult<Value> {
    let params = remote_pair_params(&host, port)?;
    bridge.request("remote.pair", params, REMOTE_SOCKETS).await
}

#[tauri::command]
pub async fn remote_pair_confirm(
    bridge: State<'_, EngineBridge>,
    digits: Option<String>,
    same: Option<bool>,
) -> CmdResult<Value> {
    let params = remote_pair_confirm_params(digits.as_deref(), same)?;
    bridge.request("remote.pair_confirm", params, QUICK).await
}

#[tauri::command]
pub async fn remote_pair_cancel(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("remote.pair_cancel", json!({}), QUICK).await
}

#[tauri::command]
pub async fn remote_unpair(
    bridge: State<'_, EngineBridge>,
    spki: String,
    role: String,
) -> CmdResult<Value> {
    let params = remote_unpair_params(&spki, &role)?;
    bridge.request("remote.unpair", params, QUICK).await
}

// --- The compute host's capacity (docs/specs/compute-capacity.md 二) -------

/// More models than the engine's registry will ever list.
const MAX_HOST_MODELS: usize = 32;
const MAX_HOST_GPU_NAME_CHARS: usize = 64;
const MAX_HOST_COMPUTE_TYPE_CHARS: usize = 32;
/// 1 TiB in MiB: no GPU or model needs more than this.
const HOST_MB_RANGE: std::ops::RangeInclusive<f64> = 0.0..=1_048_576.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum HostDevice {
    Cuda,
    Cpu,
}

/// Whether a model fits the compute host's memory next to its real-time model.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ModelFit {
    Ok,
    Tight,
    Over,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HostGpu {
    pub name: String,
    pub vram_total_mb: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelPair {
    pub fast: String,
    pub r#final: String,
}

/// The compute host's translation models (compute-capacity.md 七); "" = none set.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TranslationPair {
    pub fast: String,
    pub r#final: String,
}

/// `HostInfo.current`: the compute host's own settings.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HostCurrent {
    pub fast: String,
    pub r#final: String,
    /// Absent from compute hosts older than compute-capacity.md 七; `None`
    /// too when malformed (dropped alone, see [`check_host_info`]).
    #[serde(default, deserialize_with = "lenient_current_translation")]
    pub translation: Option<TranslationPair>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HostModel {
    pub id: String,
    pub downloaded: bool,
    pub need_mb: f64,
    /// As the final model next to the current real-time model; `None` when
    /// the compute host could not measure its hardware.
    pub fit: Option<ModelFit>,
    /// As the real-time model next to the current final model, as the compute
    /// host counts it; `None` when it could not tell or is older than this field.
    #[serde(default)]
    pub fit_as_fast: Option<ModelFit>,
}

/// `HostInfo`: what the compute host can run. It comes from another computer
/// through the engine, so it is checked here like any engine output; fields
/// this app does not know are dropped when it is serialized again.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HostInfo {
    pub gpu: Option<HostGpu>,
    pub device: HostDevice,
    pub compute_type: String,
    pub current: HostCurrent,
    pub recommended: Option<ModelPair>,
    pub models: Vec<HostModel>,
    /// `None` too when it is not a model name (dropped alone).
    #[serde(default, deserialize_with = "lenient_translation_model")]
    pub translation_model: Option<String>,
    /// The models installed on the compute host's translation server; `None`
    /// when it could not be reached (or the compute host is older), or when the
    /// list itself is malformed. A malformed entry is skipped.
    #[serde(default, deserialize_with = "lenient_translation_models")]
    pub translation_models: Option<Vec<String>>,
}

/// More translation models than a translation server lists in practice.
const MAX_HOST_TRANSLATION_MODELS: usize = 64;

// The translation fields of a `HostInfo` are dropped one by one when malformed
// instead of losing the whole report: they label no recognition model, and an
// odd name from the compute host's translation server should not hide its
// recognition models. Each drop is logged. Same rules as the UI's `parseHostInfo`.

fn drop_translation_field<T>(field: &str, reason: impl std::fmt::Display) -> Option<T> {
    log::warn!("ignoring {field} of the compute host's capacity report: {reason}");
    None
}

/// A translation model name, or "" (none set) when `allow_unset`.
fn is_translation_name(model: &str, allow_unset: bool) -> bool {
    (allow_unset && model.is_empty()) || validate_translation_model(model).is_ok()
}

fn current_translation_from(value: Value) -> Option<TranslationPair> {
    if value.is_null() {
        return None;
    }
    let pair: TranslationPair = match serde_json::from_value(value) {
        Ok(pair) => pair,
        Err(e) => return drop_translation_field("current.translation", e),
    };
    if !is_translation_name(&pair.fast, true) || !is_translation_name(&pair.r#final, true) {
        return drop_translation_field("current.translation", "not a model name");
    }
    Some(pair)
}

fn translation_model_from(value: Value) -> Option<String> {
    match value {
        Value::Null => None,
        Value::String(model) if is_translation_name(&model, false) => Some(model),
        _ => drop_translation_field("translation_model", "not a model name"),
    }
}

fn translation_models_from(value: Value) -> Option<Vec<String>> {
    let models = match value {
        Value::Null => return None,
        Value::Array(models) => models,
        _ => return drop_translation_field("translation_models", "expected an array"),
    };
    if models.len() > MAX_HOST_TRANSLATION_MODELS {
        return drop_translation_field(
            "translation_models",
            format!("more than {MAX_HOST_TRANSLATION_MODELS} entries"),
        );
    }
    let kept = models
        .into_iter()
        .enumerate()
        .filter_map(|(i, model)| match model {
            Value::String(model) if is_translation_name(&model, false) => Some(model),
            _ => drop_translation_field(&format!("translation_models[{i}]"), "not a model name"),
        })
        .collect();
    Some(kept)
}

fn lenient_current_translation<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<TranslationPair>, D::Error> {
    Value::deserialize(deserializer).map(current_translation_from)
}

fn lenient_translation_model<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<String>, D::Error> {
    Value::deserialize(deserializer).map(translation_model_from)
}

fn lenient_translation_models<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<Vec<String>>, D::Error> {
    Value::deserialize(deserializer).map(translation_models_from)
}

fn check_model_pair(fast: &str, r#final: &str, field: &str) -> Result<(), String> {
    for (name, id) in [("fast", fast), ("final", r#final)] {
        validate_model_id(id).map_err(|e| format!("{field}.{name}: {e}"))?;
    }
    Ok(())
}

/// The shape and ranges of a `HostInfo` (the same rules as the UI's
/// `parseHostInfo`): a malformed recognition field fails the whole report, a
/// malformed translation field is already dropped alone while deserializing.
pub fn check_host_info(value: Value) -> Result<HostInfo, String> {
    let host: HostInfo = serde_json::from_value(value).map_err(|e| e.to_string())?;
    if let Some(gpu) = &host.gpu {
        if gpu.name.chars().count() > MAX_HOST_GPU_NAME_CHARS {
            return Err(format!(
                "gpu.name is longer than {MAX_HOST_GPU_NAME_CHARS} characters"
            ));
        }
        if !HOST_MB_RANGE.contains(&gpu.vram_total_mb) {
            return Err("gpu.vram_total_mb is out of range".into());
        }
    }
    if host.compute_type.is_empty()
        || host.compute_type.len() > MAX_HOST_COMPUTE_TYPE_CHARS
        || !host
            .compute_type
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
    {
        return Err("compute_type is not a precision name".into());
    }
    check_model_pair(&host.current.fast, &host.current.r#final, "current")?;
    if let Some(recommended) = &host.recommended {
        check_model_pair(&recommended.fast, &recommended.r#final, "recommended")?;
    }
    if host.models.len() > MAX_HOST_MODELS {
        return Err(format!("more than {MAX_HOST_MODELS} models"));
    }
    for (i, model) in host.models.iter().enumerate() {
        validate_model_id(&model.id).map_err(|e| format!("models[{i}].id: {e}"))?;
        if !HOST_MB_RANGE.contains(&model.need_mb) {
            return Err(format!("models[{i}].need_mb is out of range"));
        }
    }
    Ok(host)
}

/// `remote.host_info` result as the UI receives it: `{host}` with a checked
/// `HostInfo`, or `host: null` when the compute host sent none or a malformed
/// one. A bad report only loses the capacity labels, so it is not an error.
pub fn host_info_result(result: Value) -> Value {
    let host = match result {
        Value::Object(mut o) => o.remove("host").unwrap_or(Value::Null),
        _ => Value::Null,
    };
    if host.is_null() {
        return json!({ "host": null });
    }
    match check_host_info(host) {
        Ok(host) => json!({ "host": host }),
        Err(e) => {
            log::warn!("ignoring the compute host's capacity report: {e}");
            json!({ "host": null })
        }
    }
}

/// What the configured compute host can run, asked with one probe connection
/// that does not take its GPU (it answers while busy too).
#[tauri::command]
pub async fn remote_host_info(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    let result = bridge
        .request("remote.host_info", json!({}), REMOTE_SOCKETS)
        .await?;
    Ok(host_info_result(result))
}

/// One side of `remote.host_set_models`: the real-time and/or the final model
/// (compute-capacity.md 七). A missing field keeps the compute host's value.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModelChoice {
    pub fast_model: Option<String>,
    pub final_model: Option<String>,
}

fn model_choice_params(
    choice: &ModelChoice,
    field: &str,
    check: fn(&str) -> Result<(), RequestError>,
) -> Result<Option<Value>, RequestError> {
    let mut out = Map::new();
    for (name, value) in [
        ("fast_model", &choice.fast_model),
        ("final_model", &choice.final_model),
    ] {
        if let Some(model) = value {
            check(model).map_err(|e| invalid(format!("{field}.{name}: {e}")))?;
            out.insert(name.into(), json!(model));
        }
    }
    Ok((!out.is_empty()).then_some(Value::Object(out)))
}

/// `remote.host_set_models` parameters: ASR ids and translation model names in
/// the commands' formats. Whether the compute host has them is its own check.
pub fn host_set_models_params(
    asr: Option<&ModelChoice>,
    translation: Option<&ModelChoice>,
) -> Result<Value, RequestError> {
    let mut params = Map::new();
    if let Some(choice) = asr
        && let Some(v) = model_choice_params(choice, "asr", validate_model_id)?
    {
        params.insert("asr".into(), v);
    }
    if let Some(choice) = translation
        && let Some(v) = model_choice_params(choice, "translation", validate_translation_model)?
    {
        params.insert("translation".into(), v);
    }
    if params.is_empty() {
        return Err(invalid("no model to change"));
    }
    Ok(Value::Object(params))
}

/// `remote.host_download` parameters: only speech recognition models can be
/// downloaded on the compute host.
pub fn host_download_params(model: &str) -> Result<Value, RequestError> {
    validate_model_id(model)?;
    if model == GPU_RUNTIME_ID {
        return Err(invalid(
            "remote.host_download only applies to speech recognition models",
        ));
    }
    Ok(json!({ "model": model }))
}

/// Changes the compute host's own models (saved there; its next session uses
/// them). Answers with its updated `HostInfo`, checked like `remote_host_info`.
#[tauri::command]
pub async fn remote_host_set_models(
    bridge: State<'_, EngineBridge>,
    asr: Option<ModelChoice>,
    translation: Option<ModelChoice>,
) -> CmdResult<Value> {
    let params = host_set_models_params(asr.as_ref(), translation.as_ref())?;
    let result = bridge
        .request("remote.host_set_models", params, REMOTE_SOCKETS)
        .await?;
    Ok(host_info_result(result))
}

/// Starts a model download on the compute host; progress arrives as
/// `engine://remote` events of kind `host_download`.
#[tauri::command]
pub async fn remote_host_download(
    bridge: State<'_, EngineBridge>,
    model: String,
) -> CmdResult<Value> {
    let params = host_download_params(&model)?;
    let result = bridge
        .request("remote.host_download", params, REMOTE_SOCKETS)
        .await?;
    // Only the flag is passed on; the progress comes as events.
    let started = result.get("started").and_then(Value::as_bool) == Some(true);
    Ok(json!({ "started": started }))
}

#[tauri::command]
pub async fn engine_shutdown(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.shutdown().await;
    Ok(Value::Object(Map::new()))
}

#[tauri::command]
pub async fn engine_restart(bridge: State<'_, EngineBridge>) -> CmdResult<BridgeStatus> {
    if let Err(e) = bridge.restart().await {
        log::error!("manual engine restart failed: {e}");
    }
    // The status carries the outcome (Ready or Failed with a reason).
    Ok(bridge.status())
}

#[tauri::command]
pub fn bridge_status(bridge: State<'_, EngineBridge>) -> BridgeStatus {
    bridge.status()
}

/// Folder inside the resource directory with kikitori's own LICENSE and the
/// notices for the Rust crates and npm packages compiled into the app
/// (generated by `packaging/app/licenses.py`, bundled through `bundle.resources`
/// in `tauri.sidecar.conf.json`).
pub const APP_LICENSES_DIR: &str = "licenses";

/// The app's license folder for a given resource directory.
pub fn app_licenses_dir(resource_dir: &Path) -> PathBuf {
    resource_dir.join(APP_LICENSES_DIR)
}

/// Which bundled license folder to open. The webview picks one of these two
/// names; the path itself is always computed here.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LicenseFolder {
    /// The app's own license plus Rust/npm notices ([`APP_LICENSES_DIR`]).
    App,
    /// The engine's `THIRD_PARTY_LICENSES` (PyInstaller-bundled Python components).
    Engine,
}

/// License facts for the settings page's "about" section.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AboutInfo {
    /// The app's own license (Cargo.toml / tauri.conf.json `bundle.license`).
    pub license: &'static str,
    /// The app's license folder, when it exists (release builds).
    pub app_licenses: Option<String>,
    /// The bundled engine's third-party license folder, when it exists
    /// (release builds); development builds have no bundled engine.
    pub engine_licenses: Option<String>,
}

/// `path` as users expect to read and paste it: without the Windows verbatim
/// prefix (`\\?\C:\…` → `C:\…`, `\\?\UNC\server\share` → `\\server\share`).
///
/// Tauri canonicalizes `resource_dir()`, which on Windows adds that prefix; the
/// about page showed it, and the file manager's address bar rejects it.
pub fn display_path(path: &Path) -> String {
    let text = path.to_string_lossy();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{rest}")
    } else if let Some(rest) = text.strip_prefix(r"\\?\") {
        rest.to_owned()
    } else {
        text.into_owned()
    }
}

/// `folder`'s path under `resource_dir`, only when it is an existing directory.
pub fn license_folder(resource_dir: Option<&Path>, folder: LicenseFolder) -> Option<String> {
    let dir = match folder {
        LicenseFolder::App => app_licenses_dir(resource_dir?),
        LicenseFolder::Engine => third_party_licenses_dir(resource_dir?),
    };
    dir.is_dir().then(|| display_path(&dir))
}

pub fn about_info(resource_dir: Option<&Path>) -> AboutInfo {
    AboutInfo {
        license: env!("CARGO_PKG_LICENSE"),
        app_licenses: license_folder(resource_dir, LicenseFolder::App),
        engine_licenses: license_folder(resource_dir, LicenseFolder::Engine),
    }
}

fn resource_dir(app: &AppHandle) -> Option<std::path::PathBuf> {
    app.path()
        .resource_dir()
        .map_err(|e| log::error!("cannot resolve the resource directory: {e}"))
        .ok()
}

#[tauri::command]
pub fn app_about(app: AppHandle) -> AboutInfo {
    about_info(resource_dir(&app).as_deref())
}

/// Opens one of the bundled license folders (the app's notices or the engine's
/// THIRD_PARTY_LICENSES) in the file manager. Takes only a [`LicenseFolder`]
/// name from the webview, never a path, so it cannot open anything else.
#[tauri::command]
pub fn open_third_party_licenses(app: AppHandle, folder: LicenseFolder) -> CmdResult<()> {
    let dir = license_folder(resource_dir(&app).as_deref(), folder)
        .ok_or_else(|| invalid("this build has no bundled license folder of that kind"))?;
    app.opener()
        .open_path(dir.clone(), None::<String>)
        .map_err(|e| {
            log::error!("cannot open {dir}: {e}");
            invalid(format!("cannot open the license folder: {e}"))
        })
}

/// The user's Documents folder (Tauri path API); `None` when the OS has none.
pub fn documents_dir(app: &AppHandle) -> Option<PathBuf> {
    app.path()
        .document_dir()
        .map_err(|e| log::error!("cannot resolve the Documents folder: {e}"))
        .ok()
}

/// Where transcripts and the OBS text go, for the settings page.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OutputInfo {
    /// `<Documents>/kikitori/transcripts`; `None` without a Documents folder.
    pub transcripts_dir: Option<String>,
    /// `<Documents>/kikitori/obs/subtitle.txt`.
    pub obs_file: Option<String>,
}

pub fn output_info_for(documents: Option<&Path>) -> OutputInfo {
    OutputInfo {
        transcripts_dir: documents
            .map(|d| display_path(&output::folder_path(d, OutputFolder::Transcripts))),
        obs_file: documents.map(|d| display_path(&output::obs_file_path(d))),
    }
}

#[tauri::command]
pub fn output_info(app: AppHandle) -> OutputInfo {
    output_info_for(documents_dir(&app).as_deref())
}

/// Saves an exported transcript into `<Documents>/kikitori/transcripts/` and
/// returns its full path. The webview only names the file (validated by
/// [`output::validate_transcript_name`]); the folder is decided here.
#[tauri::command]
pub async fn transcript_save(
    app: AppHandle,
    file_name: String,
    contents: String,
) -> CmdResult<String> {
    // Checked before touching the disk, so a bad name never creates folders.
    output::validate_transcript_name(&file_name).map_err(invalid)?;
    let documents =
        documents_dir(&app).ok_or_else(|| invalid("the Documents folder could not be found"))?;
    let dir = output::folder_path(&documents, OutputFolder::Transcripts);
    // File I/O off the async runtime's workers.
    tauri::async_runtime::spawn_blocking(move || {
        output::save_transcript(&dir, &file_name, &contents)
    })
    .await
    .map_err(|e| invalid(format!("the save task failed: {e}")))?
    .map(|path| display_path(&path))
    .map_err(invalid)
}

/// Opens the transcripts or OBS folder in the file manager (created first, so
/// the button works before anything was saved). Takes only a folder name.
#[tauri::command]
pub fn open_output_folder(app: AppHandle, folder: OutputFolder) -> CmdResult<()> {
    let documents =
        documents_dir(&app).ok_or_else(|| invalid("the Documents folder could not be found"))?;
    let dir = output::folder_path(&documents, folder);
    std::fs::create_dir_all(&dir).map_err(|e| {
        log::error!("cannot create {}: {e}", dir.display());
        invalid(format!("cannot create the folder: {e}"))
    })?;
    let shown = display_path(&dir);
    app.opener()
        .open_path(shown.clone(), None::<String>)
        .map_err(|e| {
            log::error!("cannot open {shown}: {e}");
            invalid(format!("cannot open the folder: {e}"))
        })
}

/// Turns the OBS text output on or off (`settings.subtitle.obs_output`).
#[tauri::command]
pub fn obs_set_output(obs: State<'_, ObsOutput>, content: ObsContent) -> ObsStatus {
    obs.set_content(content)
}

#[tauri::command]
pub fn overlay_get_state(overlay: State<'_, OverlayState>) -> OverlaySnapshot {
    overlay.snapshot()
}

fn broadcast_overlay(app: &AppHandle, overlay: &OverlayState) {
    if let Err(e) = app.emit(EVENT_OVERLAY_STATE, overlay.snapshot()) {
        log::error!("cannot emit overlay state: {e}");
    }
}

#[tauri::command]
pub fn overlay_set_style(
    app: AppHandle,
    overlay: State<'_, OverlayState>,
    obs: State<'_, ObsOutput>,
    style: OverlayStyle,
) -> CmdResult<OverlaySnapshot> {
    overlay.set_style(style.clone()).map_err(invalid)?;
    // The OBS text mirrors the overlay: same line count and idle clearing.
    obs.set_style(&style);
    broadcast_overlay(&app, &overlay);
    Ok(overlay.snapshot())
}

/// Tells the overlay which UI locale the main window uses, so its labels and
/// title follow the language chosen on the settings page.
#[tauri::command]
pub fn overlay_set_locale(
    app: AppHandle,
    overlay: State<'_, OverlayState>,
    locale: String,
) -> CmdResult<OverlaySnapshot> {
    overlay.set_locale(locale).map_err(invalid)?;
    broadcast_overlay(&app, &overlay);
    Ok(overlay.snapshot())
}

/// Hands the overlay the names the user gave the speaker labels this session
/// (docs/specs/speaker-labels.md); index `i` names `voice` `i + 1`.
#[tauri::command]
pub fn overlay_set_speaker_names(
    app: AppHandle,
    overlay: State<'_, OverlayState>,
    names: Vec<String>,
) -> CmdResult<OverlaySnapshot> {
    overlay.set_speaker_names(names).map_err(invalid)?;
    broadcast_overlay(&app, &overlay);
    Ok(overlay.snapshot())
}

/// Tells the overlay whether translations are shown; without them it promotes
/// the original to the primary subtitle style (settings `translation.enabled`).
#[tauri::command]
pub fn overlay_set_show_translation(
    app: AppHandle,
    overlay: State<'_, OverlayState>,
    obs: State<'_, ObsOutput>,
    enabled: bool,
) -> OverlaySnapshot {
    overlay.set_show_translation(enabled);
    obs.set_show_translation(enabled);
    broadcast_overlay(&app, &overlay);
    overlay.snapshot()
}

/// Applies click-through to the overlay window; shared with the global shortcut.
pub fn apply_click_through(
    app: &AppHandle,
    overlay: &OverlayState,
    enabled: bool,
) -> CmdResult<()> {
    let window = app
        .get_webview_window(OVERLAY_LABEL)
        .ok_or_else(|| invalid("overlay window does not exist"))?;
    window.set_ignore_cursor_events(enabled).map_err(|e| {
        log::error!("set_ignore_cursor_events({enabled}) failed: {e}");
        invalid(format!("cannot change click-through: {e}"))
    })?;
    overlay.set_click_through(enabled);
    broadcast_overlay(app, overlay);
    Ok(())
}

#[tauri::command]
pub fn overlay_set_click_through(
    app: AppHandle,
    overlay: State<'_, OverlayState>,
    enabled: bool,
) -> CmdResult<OverlaySnapshot> {
    apply_click_through(&app, &overlay, enabled)?;
    Ok(overlay.snapshot())
}

#[tauri::command]
pub fn overlay_set_visible(
    app: AppHandle,
    overlay: State<'_, OverlayState>,
    visible: bool,
) -> CmdResult<OverlaySnapshot> {
    let window = app
        .get_webview_window(OVERLAY_LABEL)
        .ok_or_else(|| invalid("overlay window does not exist"))?;
    let result = if visible {
        window.show()
    } else {
        window.hide()
    };
    result.map_err(|e| {
        log::error!("overlay show/hide failed: {e}");
        invalid(format!("cannot show or hide the subtitle window: {e}"))
    })?;
    if visible {
        // Some window managers drop the topmost flag while a window is hidden.
        if let Err(e) = window.set_always_on_top(true) {
            log::warn!("cannot re-apply always-on-top: {e}");
        }
    }
    overlay.set_visible(visible);
    broadcast_overlay(&app, &overlay);
    Ok(overlay.snapshot())
}

/// Sent to the overlay by [`overlay_notice`].
pub const EVENT_OVERLAY_NOTICE: &str = "overlay://notice";
/// Longest notice text accepted (one short sentence in any language).
pub const MAX_NOTICE_CHARS: usize = 200;
/// Default overlay size from tauri.conf.json, used by [`overlay_reset_position`].
const OVERLAY_DEFAULT_SIZE: (f64, f64) = (960.0, 200.0);

/// A short message the overlay shows for a few seconds (G3: "say that again").
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OverlayNotice {
    pub text: String,
    /// BCP 47-ish tag of `text` (`lang` attribute, picks the right CJK glyphs).
    pub lang: String,
}

/// Checks a notice from the main window before it reaches the overlay.
pub fn validate_notice(text: &str, lang: &str) -> Result<OverlayNotice, RequestError> {
    let text = text.trim();
    if text.is_empty()
        || text.chars().count() > MAX_NOTICE_CHARS
        || text.chars().any(char::is_control)
    {
        return Err(invalid(format!(
            "notice must be 1-{MAX_NOTICE_CHARS} characters without control characters"
        )));
    }
    let lang_ok = !lang.is_empty()
        && lang.len() <= MAX_LOCALE_LEN
        && lang.chars().all(|c| c.is_ascii_alphanumeric() || c == '-');
    if !lang_ok {
        return Err(invalid("notice language must be a short language tag"));
    }
    Ok(OverlayNotice {
        text: text.to_owned(),
        lang: lang.to_owned(),
    })
}

/// Shows a short notice on the overlay (interpret mode "say that again").
#[tauri::command]
pub fn overlay_notice(app: AppHandle, text: String, lang: String) -> CmdResult<()> {
    let notice = validate_notice(&text, &lang)?;
    app.emit_to(OVERLAY_LABEL, EVENT_OVERLAY_NOTICE, notice)
        .map_err(|e| {
            log::error!("cannot emit {EVENT_OVERLAY_NOTICE}: {e}");
            invalid(format!("cannot show the notice: {e}"))
        })
}

/// Where the overlay goes when reset: default size, centred near the bottom of
/// the monitor (all values in physical pixels).
pub fn default_overlay_frame(monitor: (i32, i32, u32, u32), scale: f64) -> (i32, i32, u32, u32) {
    let (mx, my, mw, mh) = monitor;
    let scale = if scale.is_finite() && scale > 0.0 {
        scale
    } else {
        1.0
    };
    let w = ((OVERLAY_DEFAULT_SIZE.0 * scale).round() as u32).min(mw.max(1));
    let h = ((OVERLAY_DEFAULT_SIZE.1 * scale).round() as u32).min(mh.max(1));
    // Saturating: a monitor that reports 0×0 while it is being unplugged must not wrap around.
    let x = mx + (mw.saturating_sub(w) / 2) as i32;
    // Leave a tenth of the screen below it (taskbar, video controls).
    let room = mh.saturating_sub(h);
    let y = my + room as i32 - (mh / 10).min(room) as i32;
    (x, y, w, h)
}

/// Moves the overlay back onto the primary monitor at its default size and
/// shows it (G6: a window left on an unplugged monitor is otherwise lost).
#[tauri::command]
pub fn overlay_reset_position(
    app: AppHandle,
    overlay: State<'_, OverlayState>,
) -> CmdResult<OverlaySnapshot> {
    let window = app
        .get_webview_window(OVERLAY_LABEL)
        .ok_or_else(|| invalid("overlay window does not exist"))?;
    let monitor = window
        .primary_monitor()
        .ok()
        .flatten()
        .or_else(|| window.current_monitor().ok().flatten())
        .ok_or_else(|| invalid("no monitor found"))?;
    let pos = monitor.position();
    let size = monitor.size();
    let (x, y, w, h) = default_overlay_frame(
        (pos.x, pos.y, size.width, size.height),
        monitor.scale_factor(),
    );
    let moved = window
        .set_size(tauri::PhysicalSize::new(w, h))
        .and_then(|()| window.set_position(tauri::PhysicalPosition::new(x, y)))
        .and_then(|()| window.show());
    moved.map_err(|e| {
        log::error!("overlay reset failed: {e}");
        invalid(format!("cannot move the subtitle window: {e}"))
    })?;
    if let Err(e) = window.set_always_on_top(true) {
        log::warn!("cannot re-apply always-on-top: {e}");
    }
    overlay.set_visible(true);
    broadcast_overlay(&app, &overlay);
    Ok(overlay.snapshot())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn notices_are_validated() {
        let ok = validate_notice("  すみません、もう一度お願いします。 ", "ja").unwrap();
        assert_eq!(ok.text, "すみません、もう一度お願いします。");
        assert_eq!(ok.lang, "ja");
        assert!(validate_notice("x", "zh-TW").is_ok());
        assert!(validate_notice("", "ja").is_err());
        assert!(validate_notice("   ", "ja").is_err());
        assert!(validate_notice(&"あ".repeat(MAX_NOTICE_CHARS + 1), "ja").is_err());
        assert!(validate_notice(&"あ".repeat(MAX_NOTICE_CHARS), "ja").is_ok());
        assert!(validate_notice("a\nb", "ja").is_err());
        assert!(validate_notice("hi", "").is_err());
        assert!(validate_notice("hi", "en US").is_err());
        assert!(validate_notice("hi", &"a".repeat(MAX_LOCALE_LEN + 1)).is_err());
    }

    #[test]
    fn default_overlay_frame_is_centred_near_the_bottom() {
        // 1920x1080 at 100 %: 960x200, centred, 108 px above the bottom edge.
        assert_eq!(
            default_overlay_frame((0, 0, 1920, 1080), 1.0),
            (480, 772, 960, 200)
        );
        // A secondary primary monitor to the left, at 150 %.
        assert_eq!(
            default_overlay_frame((-2560, 0, 2560, 1440), 1.5),
            (-2560 + 560, 1440 - 300 - 144, 1440, 300)
        );
        // Tiny screens: never larger than the monitor, never off it.
        let (x, y, w, h) = default_overlay_frame((0, 0, 800, 150), 1.0);
        assert_eq!((w, h), (800, 150));
        assert_eq!((x, y), (0, 0));
        // A bad scale factor falls back to 1.
        assert_eq!(
            default_overlay_frame((0, 0, 1920, 1080), f64::NAN),
            (480, 772, 960, 200)
        );
        // A 0×0 monitor (mid-unplug): a 1×1 window at its origin, no overflow.
        assert_eq!(default_overlay_frame((10, 20, 0, 0), 1.0), (10, 20, 1, 1));
    }

    #[test]
    fn model_ids() {
        assert!(validate_model_id("large-v3-turbo").is_ok());
        assert!(validate_model_id("base").is_ok());
        assert!(validate_model_id("").is_err());
        assert!(validate_model_id("../etc/passwd").is_err());
        assert!(validate_model_id("a b").is_err());
        assert!(validate_model_id(&"a".repeat(101)).is_err());
    }

    #[test]
    fn device_ids() {
        assert!(validate_device_id(None).is_ok());
        assert!(validate_device_id(Some("{0.0.1.00000000}.{abc}")).is_ok());
        assert!(validate_device_id(Some("")).is_err());
        assert!(validate_device_id(Some(&"x".repeat(513))).is_err());
    }

    #[test]
    fn settings_patch() {
        assert!(validate_settings_patch(&json!({"partner_language": "en"})).is_ok());
        assert!(validate_settings_patch(&json!([1])).is_err());
        assert!(validate_settings_patch(&json!("x")).is_err());
        let big = json!({"subtitle": {"note": "x".repeat(MAX_SETTINGS_PATCH_BYTES)}});
        assert!(validate_settings_patch(&big).is_err());
    }

    #[test]
    fn download_params_gate_the_gpu_runtime_license() {
        assert_eq!(
            models_download_params("base", false).unwrap(),
            json!({"model": "base"})
        );
        assert!(models_download_params("base", true).is_err());
        assert_eq!(
            models_download_params(GPU_RUNTIME_ID, true).unwrap(),
            json!({"model": "gpu-runtime", "accept_license": true})
        );
        // Not accepted: no flag at all, so the engine refuses with license_not_accepted.
        assert_eq!(
            models_download_params(GPU_RUNTIME_ID, false).unwrap(),
            json!({"model": "gpu-runtime"})
        );
        assert!(models_download_params("../x", false).is_err());
    }

    #[test]
    fn delete_params_only_take_asr_model_ids() {
        assert_eq!(
            models_delete_params("large-v3").unwrap(),
            json!({"model": "large-v3"})
        );
        for bad in ["", "../x", "a/b", GPU_RUNTIME_ID] {
            assert!(models_delete_params(bad).is_err(), "{bad:?}");
        }
        assert!(models_delete_params(&"m".repeat(MAX_MODEL_ID_CHARS + 1)).is_err());
    }

    #[test]
    fn apply_params_send_only_if_new_only_when_asked() {
        assert_eq!(apply_recommendation_params(false), json!({}));
        assert_eq!(
            apply_recommendation_params(true),
            json!({"only_if_new": true})
        );
    }

    #[test]
    fn validate_params_wrap_the_candidate() {
        assert_eq!(models_validate_params(None).unwrap(), json!({}));
        let patch = json!({"asr": {"device": "cuda"}});
        assert_eq!(
            models_validate_params(Some(&patch)).unwrap(),
            json!({"settings": {"asr": {"device": "cuda"}}})
        );
        assert!(models_validate_params(Some(&json!([1]))).is_err());
    }

    #[test]
    fn about_reports_licenses_only_when_bundled() {
        let none = about_info(None);
        assert_eq!(none.license, "MIT");
        assert_eq!(none.app_licenses, None);
        assert_eq!(none.engine_licenses, None);

        let resources = std::env::temp_dir().join(format!("kikitori-about-{}", std::process::id()));
        let empty = about_info(Some(&resources));
        assert_eq!((empty.app_licenses, empty.engine_licenses), (None, None));

        // Each folder is reported on its own: an app built without the engine
        // still shows its notices, and vice versa.
        let app_dir = app_licenses_dir(&resources);
        std::fs::create_dir_all(&app_dir).unwrap();
        let only_app = about_info(Some(&resources));
        assert_eq!(
            only_app.app_licenses,
            Some(app_dir.to_string_lossy().into_owned())
        );
        assert_eq!(only_app.engine_licenses, None);

        let engine_dir = third_party_licenses_dir(&resources);
        std::fs::create_dir_all(&engine_dir).unwrap();
        let both = about_info(Some(&resources));
        assert_eq!(
            both.app_licenses,
            Some(app_dir.to_string_lossy().into_owned())
        );
        assert_eq!(
            both.engine_licenses,
            Some(engine_dir.to_string_lossy().into_owned())
        );
        if let Err(e) = std::fs::remove_dir_all(&resources) {
            eprintln!("could not remove {}: {e}", resources.display());
        }
    }

    #[test]
    fn license_paths_are_shown_without_the_verbatim_prefix() {
        // Tauri's resource_dir() is canonicalized, which on Windows yields
        // `\\?\C:\...`; the about page showed that prefix to users, and the
        // file manager's address bar does not accept it.
        assert_eq!(
            display_path(Path::new(r"\\?\C:\Program Files\kikitori\licenses")),
            r"C:\Program Files\kikitori\licenses"
        );
        assert_eq!(
            display_path(Path::new(r"\\?\UNC\server\share\kikitori")),
            r"\\server\share\kikitori"
        );
        assert_eq!(
            display_path(Path::new(r"C:\Program Files\kikitori")),
            r"C:\Program Files\kikitori"
        );
        assert_eq!(
            display_path(Path::new("/usr/lib/kikitori/licenses")),
            "/usr/lib/kikitori/licenses"
        );

        let resources =
            std::env::temp_dir().join(format!("kikitori-verbatim-{}", std::process::id()));
        let app_dir = app_licenses_dir(&resources);
        std::fs::create_dir_all(&app_dir).unwrap();
        if cfg!(windows) {
            let verbatim = PathBuf::from(format!(r"\\?\{}", resources.display()));
            assert_eq!(
                about_info(Some(&verbatim)).app_licenses,
                Some(app_dir.to_string_lossy().into_owned())
            );
        }
        if let Err(e) = std::fs::remove_dir_all(&resources) {
            eprintln!("could not remove {}: {e}", resources.display());
        }
    }

    #[test]
    fn app_licenses_live_in_the_resource_root() {
        let resources = PathBuf::from("/usr/lib/kikitori");
        assert_eq!(app_licenses_dir(&resources), resources.join("licenses"));
        // Same folder name as the bundle.resources target in tauri.sidecar.conf.json.
        let conf: Value = serde_json::from_str(include_str!("../tauri.sidecar.conf.json")).unwrap();
        assert_eq!(
            conf["bundle"]["resources"]["licenses/"],
            json!(format!("{APP_LICENSES_DIR}/"))
        );
    }

    #[test]
    fn license_folder_names_are_the_only_input() {
        assert_eq!(
            serde_json::from_value::<LicenseFolder>(json!("app")).unwrap(),
            LicenseFolder::App
        );
        assert_eq!(
            serde_json::from_value::<LicenseFolder>(json!("engine")).unwrap(),
            LicenseFolder::Engine
        );
        for bad in [json!("../etc"), json!("/tmp"), json!(1), json!(null)] {
            assert!(serde_json::from_value::<LicenseFolder>(bad).is_err());
        }
    }

    #[test]
    fn translation_model_names() {
        for ok in [
            "qwen3.5:9b",
            "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS",
            "hy-mt1.5-7b:latest",
        ] {
            assert!(validate_translation_model(ok).is_ok(), "{ok}");
        }
        for bad in [
            "",
            ":latest",
            "a..b",
            "../x",
            "qwen 9b",
            "qwen;rm",
            "\u{6a21}\u{578b}",
        ] {
            assert!(validate_translation_model(bad).is_err(), "{bad:?}");
        }
        assert!(validate_translation_model(&"a".repeat(201)).is_err());
        assert_eq!(
            pull_translation_params("qwen3.5:9b").unwrap(),
            json!({"model": "qwen3.5:9b"})
        );
    }

    #[test]
    fn preview_languages_params_send_only_the_given_fields() {
        assert_eq!(
            preview_languages_params(&LanguageChoice::default()).unwrap(),
            json!({})
        );
        let choice: LanguageChoice = serde_json::from_value(json!({
            "mode": "interpret", "target_language": "zh-TW", "partner_language": "ko"
        }))
        .unwrap();
        assert_eq!(
            preview_languages_params(&choice).unwrap(),
            json!({"mode": "interpret", "target_language": "zh-TW", "partner_language": "ko"})
        );
        let auto = LanguageChoice {
            source_language: Some("auto".into()),
            ..Default::default()
        };
        assert_eq!(
            preview_languages_params(&auto).unwrap(),
            json!({"source_language": "auto"})
        );
        for bad in ["", "zh TW", "<b>", "abcdefghijklmnopq"] {
            let choice = LanguageChoice {
                target_language: Some(bad.into()),
                ..Default::default()
            };
            assert!(preview_languages_params(&choice).is_err(), "{bad:?}");
        }
        // Unknown fields and modes are refused while deserializing the argument.
        assert!(serde_json::from_value::<LanguageChoice>(json!({"language": "ja"})).is_err());
        assert!(serde_json::from_value::<LanguageChoice>(json!({"mode": "karaoke"})).is_err());
    }

    #[test]
    fn session_params_send_explicit_null_device() {
        assert_eq!(
            session_start_params(Mode::Media, None).unwrap(),
            json!({"mode": "media", "device_id": null})
        );
        assert_eq!(
            session_start_params(Mode::Interpret, Some("mic-1")).unwrap(),
            json!({"mode": "interpret", "device_id": "mic-1"})
        );
        assert!(serde_json::from_value::<Mode>(json!("karaoke")).is_err());
    }

    fn is_invalid_input(result: Result<Value, RequestError>) -> bool {
        matches!(result, Err(RequestError::InvalidInput { .. }))
    }

    #[test]
    fn remote_server_set_params_send_snake_case_and_check_values() {
        assert_eq!(
            remote_server_set_params(false, None, None).unwrap(),
            json!({"enabled": false})
        );
        assert_eq!(
            remote_server_set_params(true, Some("192.168.8.236"), Some(47100)).unwrap(),
            json!({"enabled": true, "listen_host": "192.168.8.236", "port": 47100})
        );
        for ok in ["100.88.94.10", "::1", "fe80::1", "0.0.0.0"] {
            assert!(
                remote_server_set_params(true, Some(ok), None).is_ok(),
                "{ok}"
            );
        }
        for bad in [
            "",
            "localhost",
            "alian_server",
            "192.168.8.256",
            " 192.168.8.236",
            "192.168.8.236:47100",
            "fe80::1%3",
            "http://1.2.3.4",
        ] {
            assert!(
                is_invalid_input(remote_server_set_params(true, Some(bad), None)),
                "{bad:?}"
            );
        }
        for port in [0, 80, 1023] {
            assert!(is_invalid_input(remote_server_set_params(
                true,
                None,
                Some(port)
            )));
        }
        for port in [1024, 65534] {
            assert!(remote_server_set_params(true, None, Some(port)).is_ok());
        }
        assert!(is_invalid_input(remote_server_set_params(
            true,
            None,
            Some(65535)
        )));
    }

    #[test]
    fn remote_pair_params_check_host_and_port() {
        assert_eq!(
            remote_pair_params("192.168.8.236", None).unwrap(),
            json!({"host": "192.168.8.236"})
        );
        assert_eq!(
            remote_pair_params("alian-server.tailnet.ts.net", Some(47100)).unwrap(),
            json!({"host": "alian-server.tailnet.ts.net", "port": 47100})
        );
        for ok in ["::1", "fe80::1", "a", "a".repeat(MAX_HOST_CHARS).as_str()] {
            assert!(remote_pair_params(ok, None).is_ok(), "{ok}");
        }
        for bad in [
            "",
            "a".repeat(MAX_HOST_CHARS + 1).as_str(),
            "alian_server",
            "host name",
            "http://host",
            "user@host",
            "host/path",
            "[::1]",
            "host\n",
            "ホスト",
        ] {
            assert!(is_invalid_input(remote_pair_params(bad, None)), "{bad:?}");
        }
        assert!(is_invalid_input(remote_pair_params("host", Some(1023))));
        assert!(remote_pair_params("host", Some(65534)).is_ok());
        assert!(is_invalid_input(remote_pair_params("host", Some(65535))));
    }

    #[test]
    fn remote_pair_confirm_params_take_exactly_one_answer() {
        assert_eq!(
            remote_pair_confirm_params(Some("166425"), None).unwrap(),
            json!({"digits": "166425"})
        );
        // Spaces as shown on screen are removed before sending.
        assert_eq!(
            remote_pair_confirm_params(Some(" 166 425 "), None).unwrap(),
            json!({"digits": "166425"})
        );
        assert_eq!(
            remote_pair_confirm_params(None, Some(true)).unwrap(),
            json!({"same": true})
        );
        assert_eq!(
            remote_pair_confirm_params(None, Some(false)).unwrap(),
            json!({"same": false})
        );
        assert!(is_invalid_input(remote_pair_confirm_params(None, None)));
        assert!(is_invalid_input(remote_pair_confirm_params(
            Some("166425"),
            Some(true)
        )));
        for bad in [
            "",
            "12345",
            "1234567",
            "12345a",
            "１２３４５６",
            "123-456",
            "   ",
        ] {
            assert!(
                is_invalid_input(remote_pair_confirm_params(Some(bad), None)),
                "{bad:?}"
            );
        }
    }

    #[test]
    fn remote_unpair_params_check_spki_and_role() {
        let spki = "05aa3f394a539a2362d9a1e4cd57cba2".repeat(2);
        for role in ["peer", "server"] {
            assert_eq!(
                remote_unpair_params(&spki, role).unwrap(),
                json!({"spki": spki, "role": role})
            );
        }
        for bad_role in ["", "client", "Peer", "peers"] {
            assert!(is_invalid_input(remote_unpair_params(&spki, bad_role)));
        }
        for bad in [
            String::new(),
            spki[..63].to_owned(),
            format!("{spki}0"),
            spki.to_uppercase(),
            format!("{}g", &spki[..63]),
            "05aa 3f39 4a53 9a23 62d9 a1e4 cd57 cba2".to_owned(),
        ] {
            assert!(
                is_invalid_input(remote_unpair_params(&bad, "peer")),
                "{bad:?}"
            );
        }
    }

    fn host_info_example() -> Value {
        json!({
            "gpu": {"name": "NVIDIA GeForce GTX 1080 Ti", "vram_total_mb": 11264},
            "device": "cuda",
            "compute_type": "int8",
            "current": {"fast": "large-v3-turbo", "final": "large-v3"},
            "recommended": {"fast": "large-v3-turbo", "final": "large-v3"},
            "models": [
                {"id": "large-v3", "downloaded": true, "need_mb": 2140, "fit": "ok", "fit_as_fast": "tight"},
                {"id": "kotoba-whisper-v2.0", "downloaded": false, "need_mb": 1600.5, "fit": "tight"}
            ],
            "translation_model": "qwen3.5:9b",
            "newer_field": true
        })
    }

    #[test]
    fn host_info_result_keeps_a_valid_report() {
        let out = host_info_result(json!({ "host": host_info_example() }));
        let host = &out["host"];
        assert_eq!(host["gpu"]["name"], json!("NVIDIA GeForce GTX 1080 Ti"));
        assert_eq!(host["device"], json!("cuda"));
        assert_eq!(host["current"]["final"], json!("large-v3"));
        assert_eq!(host["models"][1]["fit"], json!("tight"));
        // The real-time label is forwarded; an older compute host's absent one is null.
        assert_eq!(host["models"][0]["fit_as_fast"], json!("tight"));
        assert!(host["models"][1]["fit_as_fast"].is_null());
        assert_eq!(host["translation_model"], json!("qwen3.5:9b"));
        // Fields this app does not know are not forwarded.
        assert!(host.get("newer_field").is_none());

        // No GPU, no recommendation, no translation model.
        let mut cpu = host_info_example();
        cpu["gpu"] = Value::Null;
        cpu["device"] = json!("cpu");
        cpu["recommended"] = Value::Null;
        cpu["translation_model"] = Value::Null;
        cpu["models"] = json!([]);
        let out = host_info_result(json!({ "host": cpu }));
        assert!(out["host"]["gpu"].is_null() && out["host"]["recommended"].is_null());
        assert_eq!(out["host"]["device"], json!("cpu"));

        // The compute host could not measure its hardware: fit is unknown, the report stays.
        let mut unknown = host_info_example();
        unknown["models"][0]["fit"] = Value::Null;
        let out = host_info_result(json!({ "host": unknown }));
        assert!(out["host"]["models"][0]["fit"].is_null());
        assert_eq!(out["host"]["models"][1]["fit"], json!("tight"));

        for none in [json!({"host": null}), json!({}), json!(null), json!("host")] {
            assert_eq!(host_info_result(none), json!({"host": null}));
        }
    }

    #[test]
    fn host_info_result_drops_a_malformed_report_whole() {
        let cases: Vec<(&str, Value)> = vec![
            ("/gpu/name", json!("x".repeat(65))),
            ("/gpu/vram_total_mb", json!(-1)),
            ("/gpu/vram_total_mb", json!(2_000_000)),
            ("/device", json!("rocm")),
            ("/compute_type", json!("INT8; rm")),
            ("/compute_type", json!("")),
            ("/current/final", json!("../etc")),
            ("/current/fast", json!(3)),
            ("/recommended/final", json!("a b")),
            ("/models/0/id", json!("")),
            ("/models/0/downloaded", json!("yes")),
            ("/models/0/need_mb", json!(-5)),
            ("/models/0/fit", json!("maybe")),
            ("/models/0/fit_as_fast", json!("maybe")),
        ];
        for (pointer, bad) in cases {
            let mut host = host_info_example();
            *host.pointer_mut(pointer).unwrap() = bad.clone();
            assert_eq!(
                host_info_result(json!({ "host": host })),
                json!({"host": null}),
                "{pointer} = {bad}"
            );
        }
        let mut many = host_info_example();
        many["models"] = Value::Array(
            (0..=MAX_HOST_MODELS)
                .map(|i| json!({"id": format!("m{i}"), "downloaded": true, "need_mb": 1, "fit": "ok"}))
                .collect(),
        );
        assert_eq!(
            host_info_result(json!({ "host": many })),
            json!({"host": null})
        );
        let mut missing = host_info_example();
        missing.as_object_mut().unwrap().remove("current");
        assert_eq!(
            host_info_result(json!({ "host": missing })),
            json!({"host": null})
        );
    }

    #[test]
    fn host_info_carries_the_translation_models() {
        let mut host = host_info_example();
        host["current"]["translation"] = json!({"fast": "qwen3.5:4b", "final": "qwen3.5:9b"});
        host["translation_models"] = json!(["qwen3.5:4b", "qwen3.5:9b"]);
        let out = host_info_result(json!({ "host": host }));
        assert_eq!(
            out["host"]["current"]["translation"]["final"],
            json!("qwen3.5:9b")
        );
        assert_eq!(out["host"]["translation_models"][1], json!("qwen3.5:9b"));

        // An older compute host: both absent, forwarded as null.
        let out = host_info_result(json!({ "host": host_info_example() }));
        assert!(out["host"]["current"]["translation"].is_null());
        assert!(out["host"]["translation_models"].is_null());

        // No translation model set yet is "", not malformed.
        let mut unset = host_info_example();
        unset["current"]["translation"] = json!({"fast": "", "final": ""});
        unset["translation_models"] = Value::Null;
        assert!(!host_info_result(json!({ "host": unset }))["host"].is_null());
    }

    #[test]
    fn host_info_drops_only_the_malformed_translation_field() {
        let base = || {
            let mut host = host_info_example();
            host["current"]["translation"] = json!({"fast": "a", "final": "b"});
            host["translation_models"] = json!(["a", "b"]);
            host
        };
        let check = |host: Value| {
            let out = host_info_result(json!({ "host": host }))["host"].clone();
            // The recognition part is always kept.
            assert_eq!(out["models"][0]["id"], json!("large-v3"));
            assert_eq!(out["current"]["final"], json!("large-v3"));
            out
        };

        for bad in [
            json!({"fast": "../x", "final": "qwen3.5:9b"}),
            json!({"fast": "qwen3.5:4b"}),
            json!({"fast": 1, "final": "b"}),
            json!("qwen3.5:9b"),
        ] {
            let mut host = base();
            host["current"]["translation"] = bad.clone();
            let out = check(host);
            assert!(out["current"]["translation"].is_null(), "{bad}");
            assert_eq!(out["translation_models"], json!(["a", "b"]), "{bad}");
            assert_eq!(out["translation_model"], json!("qwen3.5:9b"), "{bad}");
        }

        // A bad entry is skipped, the good ones stay.
        let mut host = base();
        host["translation_models"] = json!(["ok", "a b", 3, "../x", "q:1"]);
        assert_eq!(check(host)["translation_models"], json!(["ok", "q:1"]));
        for bad in [
            json!("qwen3.5:9b"),
            json!({"a": 1}),
            Value::Array(
                (0..=MAX_HOST_TRANSLATION_MODELS)
                    .map(|i| json!(format!("m{i}")))
                    .collect(),
            ),
        ] {
            let mut host = base();
            host["translation_models"] = bad;
            let out = check(host);
            assert!(out["translation_models"].is_null());
            assert_eq!(out["current"]["translation"]["fast"], json!("a"));
        }

        for bad in [
            json!("../x"),
            json!("a b"),
            json!(7),
            json!("x".repeat(201)),
            json!(""),
        ] {
            let mut host = base();
            host["translation_model"] = bad.clone();
            let out = check(host);
            assert!(out["translation_model"].is_null(), "{bad}");
            assert_eq!(out["translation_models"], json!(["a", "b"]), "{bad}");
        }
        let mut host = base();
        host["translation_model"] = json!("x".repeat(200));
        assert_eq!(check(host)["translation_model"], json!("x".repeat(200)));
    }

    fn choice(fast: Option<&str>, r#final: Option<&str>) -> ModelChoice {
        ModelChoice {
            fast_model: fast.map(str::to_owned),
            final_model: r#final.map(str::to_owned),
        }
    }

    #[test]
    fn host_set_models_params_checks_each_model() {
        assert_eq!(
            host_set_models_params(Some(&choice(None, Some("large-v3"))), None).unwrap(),
            json!({"asr": {"final_model": "large-v3"}})
        );
        assert_eq!(
            host_set_models_params(
                Some(&choice(Some("large-v3-turbo"), Some("large-v3"))),
                Some(&choice(Some("qwen3.5:4b"), Some("hf.co/x/y:Q4"))),
            )
            .unwrap(),
            json!({
                "asr": {"fast_model": "large-v3-turbo", "final_model": "large-v3"},
                "translation": {"fast_model": "qwen3.5:4b", "final_model": "hf.co/x/y:Q4"}
            })
        );
        // Nothing to change at all is a mistake of the caller.
        assert!(is_invalid_input(host_set_models_params(None, None)));
        assert!(is_invalid_input(host_set_models_params(
            Some(&choice(None, None)),
            Some(&choice(None, None)),
        )));
        // ASR ids and translation names each follow their own rule.
        for bad in ["", "../x", "a b", "qwen3.5:9b"] {
            assert!(
                is_invalid_input(host_set_models_params(Some(&choice(Some(bad), None)), None)),
                "{bad:?}"
            );
        }
        for bad in ["", "../x", "a b", ":x"] {
            assert!(
                is_invalid_input(host_set_models_params(None, Some(&choice(None, Some(bad))))),
                "{bad:?}"
            );
        }
        // Unknown fields are refused rather than silently dropped.
        assert!(serde_json::from_value::<ModelChoice>(json!({"model": "x"})).is_err());
    }

    #[test]
    fn host_download_params_only_takes_speech_models() {
        assert_eq!(
            host_download_params("large-v3").unwrap(),
            json!({"model": "large-v3"})
        );
        for bad in ["", "../x", GPU_RUNTIME_ID] {
            assert!(is_invalid_input(host_download_params(bad)), "{bad:?}");
        }
    }

    const REMOTE_COMMANDS: [&str; 12] = [
        "remote_status",
        "remote_host_info",
        "remote_host_set_models",
        "remote_host_download",
        "remote_server_set",
        "remote_identity_reset",
        "remote_disconnect",
        "remote_pair_open",
        "remote_pair",
        "remote_pair_confirm",
        "remote_pair_cancel",
        "remote_unpair",
    ];

    fn granted(capability: &str) -> Vec<String> {
        let v: Value = serde_json::from_str(capability).unwrap();
        v["permissions"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|p| p.as_str().or_else(|| p["identifier"].as_str()))
            .map(str::to_owned)
            .collect()
    }

    #[test]
    fn remote_commands_are_granted_to_the_main_window_only() {
        let main = granted(include_str!("../capabilities/main.json"));
        let overlay = granted(include_str!("../capabilities/overlay.json"));
        let build = include_str!("../build.rs");
        for command in REMOTE_COMMANDS {
            let permission = format!("allow-{}", command.replace('_', "-"));
            assert!(main.contains(&permission), "main.json lacks {permission}");
            assert!(
                build.contains(&format!("\"{command}\"")),
                "build.rs does not declare {command}"
            );
        }
        // The overlay can be captured on stream and has no engine control.
        let remote: Vec<_> = overlay.iter().filter(|p| p.contains("remote")).collect();
        assert!(remote.is_empty(), "overlay.json grants {remote:?}");
    }

    #[test]
    fn output_paths_live_under_documents_or_are_absent() {
        assert_eq!(
            output_info_for(None),
            OutputInfo {
                transcripts_dir: None,
                obs_file: None
            }
        );
        let docs = Path::new("Docs");
        let info = output_info_for(Some(docs));
        assert_eq!(
            info.transcripts_dir.as_deref(),
            Some(display_path(&docs.join("kikitori").join("transcripts")).as_str())
        );
        assert_eq!(
            info.obs_file.as_deref(),
            Some(display_path(&docs.join("kikitori").join("obs").join("subtitle.txt")).as_str())
        );
        assert_eq!(
            serde_json::from_value::<ObsContent>(json!("both")).unwrap(),
            ObsContent::Both
        );
        assert!(serde_json::from_value::<ObsContent>(json!("everything")).is_err());
    }
}
