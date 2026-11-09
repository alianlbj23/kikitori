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
