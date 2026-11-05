//! Engine process supervisor.
//!
//! Role: owns the `kikitori-engine` child process. It decides which command to run
//! (developer override, `uv run kikitori engine` in debug builds, or the
//! PyInstaller one-folder engine bundled as a Tauri resource in release builds), wires the child's stdio to a [`Connection`],
//! verifies the protocol version, forwards stderr to the app log, and restarts the
//! engine with backoff when it dies unexpectedly. The current state is reported to
//! the UI through [`BridgeSink::bridge_state`] so the user always sees whether the
//! engine is usable.

use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::{Value, json};
use tokio::io::BufReader;
use tokio::process::{Child, Command};
use tokio::sync::{RwLock, oneshot, watch};

use crate::connection::{Connection, EventSink, RequestError};
use crate::line_reader::{BoundedLineReader, LineRead};
use crate::protocol::{EngineEvent, MAX_LINE_BYTES, PROTOCOL_VERSION};

/// Environment variable with a full engine command line, e.g.
/// `["uv","run","--no-project","python","app/dev/fake_engine.py"]` or a
/// whitespace-separated string.
pub const ENGINE_CMD_ENV: &str = "KIKITORI_ENGINE_CMD";
/// Optional working directory for the engine command.
pub const ENGINE_CWD_ENV: &str = "KIKITORI_ENGINE_CWD";
/// Folder inside the app's resource directory that holds the PyInstaller
/// one-folder engine (`bundle.resources` in `tauri.sidecar.conf.json`).
pub const ENGINE_RESOURCE_DIR: &str = "kikitori-engine";
/// Engine executable inside [`ENGINE_RESOURCE_DIR`] (plus `EXE_SUFFIX`).
pub const ENGINE_BINARY_NAME: &str = "kikitori-engine";
/// License texts of everything PyInstaller bundled (packaging/engine/licenses.py).
pub const THIRD_PARTY_LICENSES_DIR: &str = "THIRD_PARTY_LICENSES";

/// The bundled engine folder for a given resource directory.
pub fn bundled_engine_dir(resource_dir: &Path) -> PathBuf {
    resource_dir.join(ENGINE_RESOURCE_DIR)
}

/// Third-party license folder shipped with the bundled engine.
pub fn third_party_licenses_dir(resource_dir: &Path) -> PathBuf {
    bundled_engine_dir(resource_dir).join(THIRD_PARTY_LICENSES_DIR)
}

/// The first `uv run` may have to create the virtualenv and download wheels
/// before Python even starts, so the handshake gets a generous deadline.
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(120);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(3);
const KILL_WAIT_TIMEOUT: Duration = Duration::from_secs(3);

/// Where the engine command came from (shown in the UI and logs).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CommandSource {
    Env,
    DevUv,
    Sidecar,
}

/// A resolved engine command line. Never run through a shell.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EngineCommand {
    pub program: String,
    pub args: Vec<String>,
    pub cwd: Option<PathBuf>,
    /// Extra environment variables (on top of the app's own environment).
    pub env: Vec<(String, String)>,
    pub source: CommandSource,
}

impl EngineCommand {
    /// Human-readable command line for logs (contains no secrets: only argv).
    pub fn display(&self) -> String {
        std::iter::once(self.program.as_str())
            .chain(self.args.iter().map(String::as_str))
            .collect::<Vec<_>>()
            .join(" ")
    }
}

/// Splits `KIKITORI_ENGINE_CMD`: a JSON array of strings (needed for paths with
/// spaces) or a plain whitespace-separated command.
pub fn parse_command_line(raw: &str) -> Result<Vec<String>, String> {
    let trimmed = raw.trim();
    let argv: Vec<String> = if trimmed.starts_with('[') {
        let v: Vec<String> = serde_json::from_str(trimmed)
            .map_err(|e| format!("{ENGINE_CMD_ENV} is not a JSON array of strings: {e}"))?;
        v
    } else {
        trimmed.split_whitespace().map(str::to_string).collect()
    };
    if argv.is_empty() || argv[0].trim().is_empty() {
        return Err(format!("{ENGINE_CMD_ENV} is empty"));
    }
    if argv.iter().any(|a| a.contains('\0')) {
        return Err(format!("{ENGINE_CMD_ENV} contains a NUL character"));
    }
    Ok(argv)
}

/// Inputs for [`resolve_engine_command`], gathered by the caller so the decision
/// itself is a pure function.
pub struct ResolveInput<'a> {
    pub env_cmd: Option<&'a str>,
    pub env_cwd: Option<&'a str>,
    pub debug_build: bool,
    /// `PathResolver::resource_dir()`: the install directory on Windows,
    /// `Contents/Resources` on macOS, `/usr/lib/<productName>` on Linux.
    pub resource_dir: Option<&'a Path>,
    pub repo_root: &'a Path,
}

/// Picks the engine command: env override > debug `uv run` > bundled engine.
pub fn resolve_engine_command(input: &ResolveInput<'_>) -> Result<EngineCommand, String> {
    let cwd_override = match input.env_cwd.map(str::trim).filter(|s| !s.is_empty()) {
        Some(dir) => {
            let path = PathBuf::from(dir);
            if !path.is_absolute() {
                return Err(format!("{ENGINE_CWD_ENV} must be an absolute path"));
            }
            Some(path)
        }
        None => None,
    };
    let dev_cwd = || {
        cwd_override
            .clone()
            .or_else(|| Some(input.repo_root.to_path_buf()))
    };

    if let Some(raw) = input.env_cmd.filter(|s| !s.trim().is_empty()) {
        let mut argv = parse_command_line(raw)?;
        let program = argv.remove(0);
        return Ok(EngineCommand {
            program,
            args: argv,
            cwd: dev_cwd(),
            env: Vec::new(),
            source: CommandSource::Env,
        });
    }
    if input.debug_build {
        return Ok(EngineCommand {
            program: "uv".into(),
            args: vec!["run".into(), "kikitori".into(), "engine".into()],
            cwd: dev_cwd(),
            env: Vec::new(),
            source: CommandSource::DevUv,
        });
    }
    let resource_dir = input
        .resource_dir
        .ok_or_else(|| "cannot locate the app resource directory".to_string())?;
    let engine_dir = bundled_engine_dir(resource_dir);
    let file = format!("{ENGINE_BINARY_NAME}{}", std::env::consts::EXE_SUFFIX);
    Ok(EngineCommand {
        program: engine_dir.join(file).to_string_lossy().into_owned(),
        args: Vec::new(),
        // The one-folder build finds `_internal/` next to its executable either
        // way; running from its own folder keeps relative paths predictable.
        cwd: cwd_override.or(Some(engine_dir)),
        env: Vec::new(),
        source: CommandSource::Sidecar,
    })
}

/// Repo root for development: `app/src-tauri/../..`, fixed at compile time.
pub fn compile_time_repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// Limits automatic restarts so a crash loop ends in a visible failure instead
/// of spinning forever.
#[derive(Debug)]
pub struct RestartTracker {
    max_restarts: usize,
    window: Duration,
    base_delay: Duration,
    history: VecDeque<Instant>,
}

impl RestartTracker {
    pub fn new(max_restarts: usize, window: Duration, base_delay: Duration) -> Self {
        Self {
            max_restarts,
            window,
            base_delay,
            history: VecDeque::new(),
        }
    }

    /// Records a crash at `now`; returns the delay before restarting, or `None`
    /// when too many crashes happened within the window.
    pub fn on_crash(&mut self, now: Instant) -> Option<Duration> {
        while let Some(&first) = self.history.front() {
            if now.duration_since(first) > self.window {
                self.history.pop_front();
            } else {
                break;
            }
        }
        if self.history.len() >= self.max_restarts {
            return None;
        }
        let attempt = u32::try_from(self.history.len()).unwrap_or(u32::MAX);
        self.history.push_back(now);
        Some(self.base_delay.saturating_mul(2u32.saturating_pow(attempt)))
    }

    pub fn reset(&mut self) {
        self.history.clear();
    }

    pub fn recent(&self) -> usize {
        self.history.len()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BridgePhase {
    Starting,
    Ready,
    Restarting,
    Stopped,
    Failed,
}

/// Engine-process state shown in the UI (`bridge://state`).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct BridgeStatus {
    pub phase: BridgePhase,
    pub detail: Option<String>,
    pub restarts: usize,
    pub command: Option<String>,
    pub source: Option<CommandSource>,
    /// `engine.info` result once the handshake succeeded.
    pub info: Option<Value>,
}

/// Receives engine events and process-state changes.
pub trait BridgeSink: EventSink {
    fn bridge_state(&self, status: &BridgeStatus);
}

/// Adapts a [`BridgeSink`] to the narrower [`EventSink`] the connection needs.
struct EventOnly(Arc<dyn BridgeSink>);

impl EventSink for EventOnly {
    fn engine_event(&self, event: EngineEvent) {
        self.0.engine_event(event);
    }
}

struct Running {
    conn: Arc<Connection>,
    generation: u64,
    /// Set before the app itself stops this process, so its exit is not
    /// mistaken for a crash (and does not trigger an automatic restart).
    stopping: Arc<AtomicBool>,
    kill_tx: Mutex<Option<oneshot::Sender<()>>>,
    exited: watch::Receiver<bool>,
}

/// Shared handle to the engine; cheap to clone, managed as Tauri state.
#[derive(Clone)]
pub struct EngineBridge {
    inner: Arc<Inner>,
}

struct Inner {
    command: Result<EngineCommand, String>,
    sink: Arc<dyn BridgeSink>,
    current: RwLock<Option<Arc<Running>>>,
    status: Mutex<BridgeStatus>,
    restarts: Mutex<RestartTracker>,
    generation: AtomicU64,
    shutting_down: AtomicBool,
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    // Nothing panics while these locks are held; recover rather than cascade.
    m.lock().unwrap_or_else(|p| p.into_inner())
}

impl EngineBridge {
    pub fn new(command: Result<EngineCommand, String>, sink: Arc<dyn BridgeSink>) -> Self {
        let (cmd, source) = match &command {
            Ok(c) => (Some(c.display()), Some(c.source)),
            Err(_) => (None, None),
        };
        Self {
            inner: Arc::new(Inner {
                command,
                sink,
                current: RwLock::new(None),
                status: Mutex::new(BridgeStatus {
                    phase: BridgePhase::Stopped,
                    detail: None,
                    restarts: 0,
                    command: cmd,
                    source,
                    info: None,
                }),
                restarts: Mutex::new(RestartTracker::new(
                    3,
                    Duration::from_secs(60),
                    Duration::from_secs(1),
                )),
                generation: AtomicU64::new(0),
                shutting_down: AtomicBool::new(false),
            }),
        }
    }

    pub fn status(&self) -> BridgeStatus {
        lock(&self.inner.status).clone()
    }

    fn set_status(&self, phase: BridgePhase, detail: Option<String>, info: Option<Value>) {
        let snapshot = {
            let mut s = lock(&self.inner.status);
            s.phase = phase;
            s.detail = detail;
            s.info = info;
            s.restarts = lock(&self.inner.restarts).recent();
            s.clone()
        };
        match phase {
            BridgePhase::Failed => log::error!("engine bridge failed: {:?}", snapshot.detail),
            _ => log::info!("engine bridge {:?}: {:?}", phase, snapshot.detail),
        }
        self.inner.sink.bridge_state(&snapshot);
    }

    /// Sends a request to the running engine.
    pub async fn request(
        &self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value, RequestError> {
        let conn = {
            let current = self.inner.current.read().await;
            current.as_ref().map(|r| r.conn.clone())
        };
        match conn {
            Some(conn) => conn.request(method, params, timeout).await,
            None => Err(RequestError::Disconnected {
                detail: "engine is not running".into(),
            }),
        }
    }

    /// Spawns the engine and performs the `engine.info` handshake. Errors are
    /// reported through the status as well as returned.
    pub async fn start(&self) -> Result<(), String> {
        self.inner.shutting_down.store(false, Ordering::SeqCst);
        let command = match &self.inner.command {
            Ok(c) => c.clone(),
            Err(e) => {
                let msg = format!("engine command is invalid: {e}");
                self.set_status(BridgePhase::Failed, Some(msg.clone()), None);
                return Err(msg);
            }
        };
        self.set_status(BridgePhase::Starting, Some(command.display()), None);
        let running = match self.spawn(&command) {
            Ok(r) => r,
            Err(e) => {
                let msg = format!("cannot start engine `{}`: {e}", command.display());
                self.set_status(BridgePhase::Failed, Some(msg.clone()), None);
                return Err(msg);
            }
        };
        let conn = running.conn.clone();
        *self.inner.current.write().await = Some(running);

        match conn
            .request("engine.info", json!({}), HANDSHAKE_TIMEOUT)
            .await
        {
            Ok(info) => match check_protocol(&info) {
                Ok(()) => {
                    self.set_status(BridgePhase::Ready, None, Some(info));
                    Ok(())
                }
                Err(e) => {
                    // A protocol mismatch will not fix itself by restarting.
                    // Failed is set before the kill so the exit handler keeps it.
                    self.set_status(BridgePhase::Failed, Some(e.clone()), Some(info));
                    self.kill_current().await;
                    Err(e)
                }
            },
            Err(e) if conn.is_closed() => {
                // The process died during the handshake; the supervisor's
                // crash handling (restart with backoff) takes over.
                let msg = format!("engine handshake failed: {e}");
                log::error!("{msg}");
                Err(msg)
            }
            Err(e) => {
                // Alive but silent: restarting would most likely hang again, so
                // stop it and let the user retry from the UI.
                let msg = format!("engine handshake failed: {e}");
                self.set_status(BridgePhase::Failed, Some(msg.clone()), None);
                self.kill_current().await;
                Err(msg)
            }
        }
    }

    fn spawn(&self, command: &EngineCommand) -> std::io::Result<Arc<Running>> {
        let mut cmd = Command::new(&command.program);
        cmd.args(&command.args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        if let Some(cwd) = &command.cwd {
            cmd.current_dir(cwd);
        }
        cmd.envs(command.env.iter().map(|(k, v)| (k, v)));
        #[cfg(windows)]
        {
            // CREATE_NO_WINDOW: the release app has no console, and without this
            // flag Windows would pop up a console window for the Python engine.
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        log::info!("starting engine: {}", command.display());
        let mut child = cmd.spawn()?;
        let missing = || std::io::Error::other("child stdio pipe missing");
        let stdin = child.stdin.take().ok_or_else(missing)?;
        let stdout = child.stdout.take().ok_or_else(missing)?;
        let stderr = child.stderr.take().ok_or_else(missing)?;

        tokio::spawn(forward_stderr(stderr));
        let sink: Arc<dyn EventSink> = Arc::new(EventOnly(self.inner.sink.clone()));
        let (conn, closed_rx) = Connection::spawn(stdout, stdin, sink);
        let (kill_tx, kill_rx) = oneshot::channel();
        let (exited_tx, exited_rx) = watch::channel(false);
        let generation = self.inner.generation.fetch_add(1, Ordering::SeqCst) + 1;
        let stopping = Arc::new(AtomicBool::new(false));
        let running = Arc::new(Running {
            conn: conn.clone(),
            generation,
            stopping: stopping.clone(),
            kill_tx: Mutex::new(Some(kill_tx)),
            exited: exited_rx,
        });
        let bridge = self.clone();
        tokio::spawn(async move {
            let detail = supervise(child, closed_rx, kill_rx).await;
            conn.fail_all(&detail);
            // Receivers may all be gone during app teardown; nothing to notify.
            let _ = exited_tx.send(true);
            bridge.on_exit(generation, stopping, detail).await;
        });
        Ok(running)
    }

    async fn on_exit(&self, generation: u64, stopping: Arc<AtomicBool>, detail: String) {
        {
            let mut current = self.inner.current.write().await;
            match current.as_ref() {
                Some(r) if r.generation == generation => *current = None,
                // A newer engine already replaced this one (manual restart).
                _ => return,
            }
        }
        if stopping.load(Ordering::SeqCst) || self.inner.shutting_down.load(Ordering::SeqCst) {
            // Keep an earlier Failed (and its reason) instead of hiding it
            // behind a generic "stopped".
            let failed = lock(&self.inner.status).phase == BridgePhase::Failed;
            if !failed {
                self.set_status(BridgePhase::Stopped, Some(detail), None);
            }
            return;
        }
        let delay = lock(&self.inner.restarts).on_crash(Instant::now());
        match delay {
            Some(delay) => {
                log::warn!("engine exited unexpectedly ({detail}); restarting in {delay:?}");
                self.set_status(BridgePhase::Restarting, Some(detail), None);
                tokio::time::sleep(delay).await;
                // Skip if the app is quitting or someone (the restart button)
                // already started a newer engine while we were waiting.
                if self.inner.shutting_down.load(Ordering::SeqCst)
                    || self.inner.generation.load(Ordering::SeqCst) != generation
                {
                    return;
                }
                let bridge = self.clone();
                // Boxed to break the start → spawn → on_exit → start future cycle.
                tokio::spawn(async move {
                    if let Err(e) = Box::pin(bridge.start()).await {
                        log::error!("engine restart failed: {e}");
                    }
                });
            }
            None => {
                self.set_status(
                    BridgePhase::Failed,
                    Some(format!("engine keeps exiting; last reason: {detail}")),
                    None,
                );
            }
        }
    }

    async fn kill_current(&self) {
        let running = self.inner.current.read().await.clone();
        if let Some(r) = running {
            r.stopping.store(true, Ordering::SeqCst);
            if let Some(tx) = lock(&r.kill_tx).take() {
                // The supervisor may already have finished; then nothing to kill.
                let _ = tx.send(());
            }
            wait_exited(r.exited.clone(), KILL_WAIT_TIMEOUT).await;
        }
    }

    /// Stops the current engine (if any) and starts a fresh one, clearing the
    /// crash counter: used by the "restart engine" button.
    pub async fn restart(&self) -> Result<(), String> {
        self.shutdown().await;
        lock(&self.inner.restarts).reset();
        self.start().await
    }

    /// Asks the engine to exit, then kills it if it does not within the deadline.
    pub async fn shutdown(&self) {
        self.inner.shutting_down.store(true, Ordering::SeqCst);
        let running = self.inner.current.read().await.clone();
        let Some(r) = running else {
            return;
        };
        r.stopping.store(true, Ordering::SeqCst);
        if let Err(e) = r
            .conn
            .request("engine.shutdown", json!({}), SHUTDOWN_TIMEOUT)
            .await
        {
            log::warn!("engine.shutdown failed ({e}); killing the engine");
        }
        if !wait_exited(r.exited.clone(), SHUTDOWN_TIMEOUT).await {
            log::warn!("engine did not exit within {SHUTDOWN_TIMEOUT:?}; killing it");
            self.kill_current().await;
        }
    }
}

fn check_protocol(info: &Value) -> Result<(), String> {
    match info.get("protocol_version").and_then(Value::as_u64) {
        Some(PROTOCOL_VERSION) => Ok(()),
        Some(v) => Err(format!(
            "engine speaks protocol version {v}, this app needs {PROTOCOL_VERSION}"
        )),
        None => Err("engine.info has no integer protocol_version".into()),
    }
}

async fn wait_exited(mut exited: watch::Receiver<bool>, timeout: Duration) -> bool {
    tokio::time::timeout(timeout, exited.wait_for(|e| *e))
        .await
        .map(|r| r.is_ok())
        .unwrap_or(false)
}

/// Waits for the child to exit, its stdout to close, or a kill request, and
/// returns a description of how it ended.
async fn supervise(
    mut child: Child,
    closed_rx: oneshot::Receiver<String>,
    kill_rx: oneshot::Receiver<()>,
) -> String {
    tokio::select! {
        status = child.wait() => match status {
            Ok(s) => format!("engine exited ({s})"),
            Err(e) => format!("cannot wait for engine: {e}"),
        },
        reason = closed_rx => {
            // Output closed but the process may still be alive (e.g. it closed
            // stdout by mistake): it is useless without the channel, so end it.
            let reason = reason.unwrap_or_else(|_| "connection dropped".into());
            kill_and_wait(&mut child, &reason).await
        }
        _ = kill_rx => kill_and_wait(&mut child, "engine stopped by the app").await,
    }
}

async fn kill_and_wait(child: &mut Child, reason: &str) -> String {
    // Give a process that is exiting on its own a moment before killing it.
    if let Ok(Ok(status)) = tokio::time::timeout(Duration::from_millis(500), child.wait()).await {
        return format!("{reason}; engine exited ({status})");
    }
    if let Err(e) = child.start_kill() {
        log::error!("cannot kill engine: {e}");
    }
    match tokio::time::timeout(KILL_WAIT_TIMEOUT, child.wait()).await {
        Ok(Ok(status)) => format!("{reason}; engine killed ({status})"),
        Ok(Err(e)) => format!("{reason}; cannot wait for engine: {e}"),
        Err(_) => format!("{reason}; engine did not exit after kill"),
    }
}

async fn forward_stderr(stderr: tokio::process::ChildStderr) {
    let mut lines = BoundedLineReader::new(BufReader::new(stderr), MAX_LINE_BYTES);
    loop {
        match lines.next_line().await {
            Ok(LineRead::Line(bytes)) => {
                let text = String::from_utf8_lossy(&bytes);
                if !text.trim().is_empty() {
                    log::info!(target: "engine", "{text}");
                }
            }
            Ok(LineRead::TooLong { length }) => {
                log::warn!(target: "engine", "dropped a {length}-byte stderr line");
            }
            Ok(LineRead::Eof) => return,
            Err(e) => {
                log::warn!("reading engine stderr failed: {e}");
                return;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_command_line_forms() {
        assert_eq!(
            parse_command_line("uv run kikitori engine").unwrap(),
            vec!["uv", "run", "kikitori", "engine"]
        );
        assert_eq!(
            parse_command_line(r#"["C:\\Program Files\\py.exe", "fake engine.py"]"#).unwrap(),
            vec!["C:\\Program Files\\py.exe", "fake engine.py"]
        );
        assert!(parse_command_line("   ").is_err());
        assert!(parse_command_line("[]").is_err());
        assert!(parse_command_line("[1, 2]").is_err());
        assert!(parse_command_line(r#"["a\u0000b"]"#).is_err());
    }

    fn input<'a>(
        env_cmd: Option<&'a str>,
        env_cwd: Option<&'a str>,
        debug: bool,
        resources: Option<&'a Path>,
    ) -> ResolveInput<'a> {
        ResolveInput {
            env_cmd,
            env_cwd,
            debug_build: debug,
            resource_dir: resources,
            repo_root: Path::new("/repo"),
        }
    }

    #[test]
    fn env_override_wins() {
        let cmd =
            resolve_engine_command(&input(Some("python fake.py"), None, false, None)).unwrap();
        assert_eq!(cmd.program, "python");
        assert_eq!(cmd.args, vec!["fake.py"]);
        assert_eq!(cmd.cwd.as_deref(), Some(Path::new("/repo")));
        assert_eq!(cmd.source, CommandSource::Env);
    }

    #[test]
    fn debug_uses_uv_in_repo_root() {
        let cmd = resolve_engine_command(&input(None, None, true, None)).unwrap();
        assert_eq!(cmd.display(), "uv run kikitori engine");
        assert_eq!(cmd.cwd.as_deref(), Some(Path::new("/repo")));
        assert_eq!(cmd.source, CommandSource::DevUv);
    }

    #[test]
    fn release_uses_the_bundled_engine_folder() {
        let resources = PathBuf::from("/usr/lib/kikitori");
        let cmd = resolve_engine_command(&input(None, None, false, Some(&resources))).unwrap();
        let folder = resources.join("kikitori-engine");
        let expected = folder.join(format!("kikitori-engine{}", std::env::consts::EXE_SUFFIX));
        assert_eq!(PathBuf::from(&cmd.program), expected);
        assert!(cmd.args.is_empty());
        assert_eq!(cmd.cwd.as_deref(), Some(folder.as_path()));
        assert_eq!(cmd.source, CommandSource::Sidecar);
        assert!(resolve_engine_command(&input(None, None, false, None)).is_err());
    }

    #[test]
    fn release_honours_overrides() {
        let resources = PathBuf::from("/usr/lib/kikitori");
        let abs = std::env::temp_dir();
        let abs_str = abs.to_string_lossy().into_owned();
        let cmd =
            resolve_engine_command(&input(None, Some(&abs_str), false, Some(&resources))).unwrap();
        assert_eq!(cmd.cwd.as_deref(), Some(abs.as_path()));
        let cmd =
            resolve_engine_command(&input(Some("python e.py"), None, false, Some(&resources)))
                .unwrap();
        assert_eq!(cmd.program, "python");
        assert_eq!(cmd.source, CommandSource::Env);
    }

    #[test]
    fn third_party_licenses_live_inside_the_engine_folder() {
        let resources = PathBuf::from("/usr/lib/kikitori");
        assert_eq!(
            third_party_licenses_dir(&resources),
            resources
                .join("kikitori-engine")
                .join("THIRD_PARTY_LICENSES")
        );
    }

    #[test]
    fn cwd_override_must_be_absolute() {
        assert!(
            resolve_engine_command(&input(Some("x"), Some("relative/dir"), true, None)).is_err()
        );
        let abs = std::env::temp_dir();
        let abs_str = abs.to_string_lossy().into_owned();
        let cmd = resolve_engine_command(&input(Some("x"), Some(&abs_str), true, None)).unwrap();
        assert_eq!(cmd.cwd.as_deref(), Some(abs.as_path()));
    }

    #[test]
    fn restart_tracker_backs_off_and_gives_up() {
        let mut t = RestartTracker::new(3, Duration::from_secs(60), Duration::from_secs(1));
        let t0 = Instant::now();
        assert_eq!(t.on_crash(t0), Some(Duration::from_secs(1)));
        assert_eq!(
            t.on_crash(t0 + Duration::from_secs(5)),
            Some(Duration::from_secs(2))
        );
        assert_eq!(
            t.on_crash(t0 + Duration::from_secs(10)),
            Some(Duration::from_secs(4))
        );
        assert_eq!(t.on_crash(t0 + Duration::from_secs(15)), None);
        // Old crashes fall out of the window.
        assert_eq!(
            t.on_crash(t0 + Duration::from_secs(80)),
            Some(Duration::from_secs(1))
        );
        t.reset();
        assert_eq!(t.recent(), 0);
    }

    /// End-to-end against `app/dev/fake_engine.py` through a real child process.
    /// Needs `uv` on PATH (the project's Python toolchain, also present in CI).
    mod fake_engine {
        use super::*;
        use crate::commands::{GPU_RUNTIME_ID, models_download_params, models_validate_params};
        use crate::protocol::{DownloadProgressPayload, DownloadState, SubtitlePayload};

        const EULA_URL: &str = "https://docs.nvidia.com/cuda/eula/index.html";
        /// Background methods queue behind each other in the engine.
        const SLOW: Duration = Duration::from_secs(45);

        #[derive(Default)]
        struct Recorder {
            events: Mutex<Vec<EngineEvent>>,
            phases: Mutex<Vec<BridgePhase>>,
        }

        impl EventSink for Recorder {
            fn engine_event(&self, event: EngineEvent) {
                lock(&self.events).push(event);
            }
        }

        impl BridgeSink for Recorder {
            fn bridge_state(&self, status: &BridgeStatus) {
                lock(&self.phases).push(status.phase);
            }
        }

        fn fake_command(extra: &[&str]) -> EngineCommand {
            let script = compile_time_repo_root()
                .join("app")
                .join("dev")
                .join("fake_engine.py");
            let mut args: Vec<String> = ["run", "--no-project", "python"]
                .iter()
                .map(|s| s.to_string())
                .collect();
            args.push(script.to_string_lossy().into_owned());
            args.extend(extra.iter().map(|s| s.to_string()));
            EngineCommand {
                program: "uv".into(),
                args,
                cwd: Some(compile_time_repo_root()),
                env: Vec::new(),
                source: CommandSource::Env,
            }
        }

        /// The real engine (`kikitori engine`) in its built-in fake mode, with
        /// settings in a throwaway directory so the developer's file is untouched.
        fn real_engine_fake_mode(config_dir: &Path) -> EngineCommand {
            real_engine_fake_hardware(config_dir, "nvidia")
        }

        /// Same, choosing the engine's fake hardware scenario
        /// (`KIKITORI_ENGINE_FAKE_HARDWARE`: nvidia, apple, slow-runtime, broken-runtime).
        fn real_engine_fake_hardware(config_dir: &Path, scenario: &str) -> EngineCommand {
            EngineCommand {
                program: "uv".into(),
                args: vec!["run".into(), "kikitori".into(), "engine".into()],
                cwd: Some(compile_time_repo_root()),
                env: vec![
                    ("KIKITORI_ENGINE_FAKE".into(), "1".into()),
                    ("KIKITORI_ENGINE_FAKE_HARDWARE".into(), scenario.into()),
                    (
                        "KIKITORI_CONFIG_DIR".into(),
                        config_dir.to_string_lossy().into_owned(),
                    ),
                ],
                source: CommandSource::Env,
            }
        }

        /// A fresh settings directory per test, removed on drop.
        struct TempConfig(PathBuf);

        impl TempConfig {
            fn new(tag: &str) -> Self {
                static COUNTER: AtomicU64 = AtomicU64::new(0);
                let dir = std::env::temp_dir().join(format!(
                    "kikitori-app-test-{tag}-{}-{}",
                    std::process::id(),
                    COUNTER.fetch_add(1, Ordering::SeqCst)
                ));
                std::fs::create_dir_all(&dir).expect("create temp config dir");
                Self(dir)
            }
        }

        impl Drop for TempConfig {
            fn drop(&mut self) {
                if let Err(e) = std::fs::remove_dir_all(&self.0) {
                    eprintln!("could not remove {}: {e}", self.0.display());
                }
            }
        }

        fn engine_code(result: Result<Value, RequestError>) -> (String, String) {
            match result {
                Err(RequestError::Engine { code, message }) => (code, message),
                other => panic!("expected an engine error, got {other:?}"),
            }
        }

        fn downloads(rec: &Recorder, model: &str) -> Vec<DownloadProgressPayload> {
            lock(&rec.events)
                .iter()
                .filter_map(|e| match e {
                    EngineEvent::DownloadProgress(p) if p.model == model => Some(p.clone()),
                    _ => None,
                })
                .collect()
        }

        async fn wait_until(what: &str, mut cond: impl FnMut() -> bool) {
            let deadline = Instant::now() + Duration::from_secs(30);
            while !cond() {
                assert!(Instant::now() < deadline, "timed out waiting for {what}");
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
        }

        fn subtitles(rec: &Recorder) -> Vec<SubtitlePayload> {
            lock(&rec.events)
                .iter()
                .filter_map(|e| match e {
                    EngineEvent::Subtitle(s) => Some(s.clone()),
                    _ => None,
                })
                .collect()
        }

        #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
        async fn session_streams_partial_then_final_and_survives_garbage() {
            let rec = Arc::new(Recorder::default());
            let bridge =
                EngineBridge::new(Ok(fake_command(&["--garbage-every", "2"])), rec.clone());
            bridge.start().await.expect("handshake");
            assert_eq!(bridge.status().phase, BridgePhase::Ready);
            assert_eq!(bridge.status().info.unwrap()["fake"], json!(true));

            let err = bridge
                .request("no.such", json!({}), Duration::from_secs(5))
                .await
                .unwrap_err();
            assert!(
                matches!(err, RequestError::Engine { ref code, .. } if code == "unknown_method")
            );

            let started = bridge
                .request(
                    "session.start",
                    json!({"mode": "media", "device_id": null}),
                    Duration::from_secs(10),
                )
                .await
                .unwrap();
            assert_eq!(started["session_id"], json!(1));
            wait_until("a final subtitle", || {
                subtitles(&rec).iter().any(|s| s.is_final)
            })
            .await;
            let subs = subtitles(&rec);
            let first_final = subs.iter().position(|s| s.is_final).unwrap();
            assert!(first_final > 0, "partials come before the final");
            assert!(
                subs[..first_final]
                    .iter()
                    .all(|s| s.utterance_id == 1 && !s.is_final)
            );

            bridge
                .request("session.stop", json!({}), Duration::from_secs(10))
                .await
                .unwrap();
            bridge.shutdown().await;
            wait_until("stopped", || bridge.status().phase == BridgePhase::Stopped).await;
        }

        #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
        async fn real_engine_in_fake_mode_speaks_the_same_protocol() {
            let config_dir = std::env::temp_dir().join(format!(
                "kikitori-app-test-{}-{}",
                std::process::id(),
                Instant::now().elapsed().as_nanos()
            ));
            std::fs::create_dir_all(&config_dir).unwrap();
            let rec = Arc::new(Recorder::default());
            let bridge = EngineBridge::new(Ok(real_engine_fake_mode(&config_dir)), rec.clone());
            bridge
                .start()
                .await
                .expect("handshake with the real engine");
            let info = bridge.status().info.unwrap();
            assert_eq!(info["fake"], json!(true));

            let settings = bridge
                .request("settings.get", json!({}), Duration::from_secs(10))
                .await
                .unwrap();
            assert!(settings["settings"]["subtitle"].is_object());
            let style = json!({"subtitle": {"font_size": 40, "background_opacity": 0.3, "position": "top", "max_lines": 2}});
            let saved = bridge
                .request("settings.set", style, Duration::from_secs(10))
                .await
                .unwrap();
            assert_eq!(saved["settings"]["subtitle"]["font_size"], json!(40));
            let bad = bridge
                .request(
                    "settings.set",
                    json!({"partner_language": "ko"}),
                    Duration::from_secs(10),
                )
                .await
                .unwrap_err();
            assert!(
                matches!(bad, RequestError::Engine { ref code, .. } if code == "invalid_settings")
            );

            bridge
                .request(
                    "session.start",
                    json!({"mode": "media", "device_id": null}),
                    Duration::from_secs(30),
                )
                .await
                .unwrap();
            wait_until("a final subtitle from the real engine", || {
                subtitles(&rec).iter().any(|s| s.is_final)
            })
            .await;
            bridge.shutdown().await;
            wait_until("stopped", || bridge.status().phase == BridgePhase::Stopped).await;
            if let Err(e) = std::fs::remove_dir_all(&config_dir) {
                eprintln!("could not remove {}: {e}", config_dir.display());
            }
        }

        /// The fake engine answers background methods after a delay, so a later
        /// request is answered first; each caller must still get its own result.
        #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
        async fn background_responses_may_overtake_and_are_matched_by_id() {
            let rec = Arc::new(Recorder::default());
            let bridge = EngineBridge::new(Ok(fake_command(&[])), rec.clone());
            bridge.start().await.expect("handshake");
            let order = Arc::new(Mutex::new(Vec::new()));
            let timed = |method: &'static str, params: Value| {
                let bridge = bridge.clone();
                let order = order.clone();
                async move {
                    let result = bridge.request(method, params, SLOW).await;
                    lock(&order).push(method);
                    result
                }
            };
            let (detect, settings) = tokio::join!(timed("hardware.detect", json!({})), async {
                // Make sure the engine received hardware.detect first.
                tokio::time::sleep(Duration::from_millis(100)).await;
                timed("settings.get", json!({})).await
            });
            assert_eq!(*lock(&order), vec!["settings.get", "hardware.detect"]);
            assert!(detect.unwrap()["recommendation"]["fast_asr"].is_object());
            assert!(settings.unwrap()["settings"]["asr"].is_object());
            bridge.shutdown().await;
        }

        /// Milestone 6 methods against the real engine with fake NVIDIA hardware
        /// (8 GB GPU, cuBLAS missing): detect, validate, apply, the license gate,
        /// then the GPU runtime install switches the recommendation to CUDA.
        #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
        async fn real_engine_hardware_methods_and_gpu_runtime_gate() {
            let config = TempConfig::new("hw");
            let rec = Arc::new(Recorder::default());
            let bridge = EngineBridge::new(Ok(real_engine_fake_mode(&config.0)), rec.clone());
            bridge
                .start()
                .await
                .expect("handshake with the real engine");

            // Background methods may answer after later requests; responses are
            // matched by id, so concurrent callers each get their own result.
            let (detect, settings, devices) = tokio::join!(
                bridge.request("hardware.detect", json!({}), SLOW),
                bridge.request("settings.get", json!({}), Duration::from_secs(10)),
                bridge.request("devices.list", json!({}), Duration::from_secs(10)),
            );
            let detect = detect.unwrap();
            assert!(settings.unwrap()["settings"]["asr"].is_object());
            assert!(devices.unwrap()["devices"].is_array());
            let gpu = &detect["hardware"]["gpus"][0];
            assert!(gpu["memory_total_mb"].as_u64().unwrap() > 0);
            assert!(gpu.get("vram_bytes").is_none());
            let before = &detect["recommendation"];
            assert_eq!(before["gpu_runtime_missing"], json!(true));
            assert_eq!(before["fast_asr"]["device"], json!("cpu"));
            assert!(before["final_asr"]["compute_type"].is_string());
            assert!(before["reasons"].is_array() && before["warnings"].is_array());

            let models = bridge
                .request("models.list", json!({}), SLOW)
                .await
                .unwrap();
            let runtime = &models["gpu_runtime"];
            assert_eq!(runtime["id"], json!(GPU_RUNTIME_ID));
            assert_eq!(runtime["supported"], json!(true));
            assert_eq!(runtime["installed"], json!(false));
            assert_eq!(runtime["license_url"], json!(EULA_URL));
            assert!(models["asr"][0]["recommended_for"].is_array());
            assert!(models["translation"]["models"][0]["commercial_use"].is_boolean());

            // Live validation: a candidate is checked without being saved.
            let candidate = json!({"asr": {"device": "cuda"}});
            let checked = bridge
                .request(
                    "models.validate",
                    models_validate_params(Some(&candidate)).unwrap(),
                    SLOW,
                )
                .await
                .unwrap();
            assert_eq!(checked["ok"], json!(false));
            assert!(!checked["errors"].as_array().unwrap().is_empty());
            let current = bridge
                .request(
                    "models.validate",
                    models_validate_params(None).unwrap(),
                    SLOW,
                )
                .await
                .unwrap();
            assert!(current["ok"].is_boolean());
            let bad = json!({"asr": {"fast_cpu_threads": 0}});
            let (code, _) = engine_code(
                bridge
                    .request(
                        "models.validate",
                        models_validate_params(Some(&bad)).unwrap(),
                        SLOW,
                    )
                    .await,
            );
            assert_eq!(code, "invalid_settings");
            let saved = bridge
                .request("settings.get", json!({}), Duration::from_secs(10))
                .await
                .unwrap();
            assert_eq!(saved["settings"]["asr"]["device"], json!("auto"));

            // Applying the recommendation is the engine's job; before cuBLAS it is CPU.
            let applied = bridge
                .request("settings.apply_recommendation", json!({}), SLOW)
                .await
                .unwrap();
            assert_eq!(applied["applied"]["asr"]["device"], json!("cpu"));
            assert_eq!(applied["settings"]["asr"]["device"], json!("cpu"));
            assert!(applied["recommendation"]["tier"].is_string());

            // License gate: without the flag the engine refuses and names the EULA.
            let (code, message) = engine_code(
                bridge
                    .request(
                        "models.download",
                        models_download_params(GPU_RUNTIME_ID, false).unwrap(),
                        Duration::from_secs(10),
                    )
                    .await,
            );
            assert_eq!(code, "license_not_accepted");
            assert!(message.contains(EULA_URL));
            assert!(downloads(&rec, GPU_RUNTIME_ID).is_empty());

            let started = bridge
                .request(
                    "models.download",
                    models_download_params(GPU_RUNTIME_ID, true).unwrap(),
                    Duration::from_secs(10),
                )
                .await
                .unwrap();
            assert_eq!(started["started"], json!(true));
            wait_until("the GPU runtime install to finish", || {
                downloads(&rec, GPU_RUNTIME_ID)
                    .iter()
                    .any(|p| p.state != DownloadState::Downloading)
            })
            .await;
            let last = downloads(&rec, GPU_RUNTIME_ID).pop().unwrap();
            assert_eq!(last.state, DownloadState::Done, "{:?}", last.error);

            let after = bridge
                .request("settings.apply_recommendation", json!({}), SLOW)
                .await
                .unwrap();
            assert_eq!(after["applied"]["asr"]["device"], json!("cuda"));
            assert_eq!(after["recommendation"]["gpu_runtime_missing"], json!(false));

            bridge.shutdown().await;
            wait_until("stopped", || bridge.status().phase == BridgePhase::Stopped).await;
        }

        /// macOS / ARM: there is no GPU runtime, whether or not the license was accepted.
        #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
        async fn real_engine_reports_gpu_runtime_unsupported() {
            let config = TempConfig::new("apple");
            let rec = Arc::new(Recorder::default());
            let bridge = EngineBridge::new(
                Ok(real_engine_fake_hardware(&config.0, "apple")),
                rec.clone(),
            );
            bridge
                .start()
                .await
                .expect("handshake with the real engine");
            let models = bridge
                .request("models.list", json!({}), SLOW)
                .await
                .unwrap();
            assert_eq!(models["gpu_runtime"]["supported"], json!(false));
            let (code, _) = engine_code(
                bridge
                    .request(
                        "models.download",
                        models_download_params(GPU_RUNTIME_ID, true).unwrap(),
                        Duration::from_secs(10),
                    )
                    .await,
            );
            assert_eq!(code, "gpu_runtime_unsupported");
            bridge.shutdown().await;
            wait_until("stopped", || bridge.status().phase == BridgePhase::Stopped).await;
        }

        #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
        async fn crash_is_reported_and_engine_restarts() {
            let rec = Arc::new(Recorder::default());
            let bridge =
                EngineBridge::new(Ok(fake_command(&["--crash-after", "1.5"])), rec.clone());
            bridge.start().await.expect("handshake");
            wait_until("restart", || {
                lock(&rec.phases).contains(&BridgePhase::Restarting)
            })
            .await;
            wait_until("ready again", || {
                lock(&rec.phases)
                    .iter()
                    .filter(|p| **p == BridgePhase::Ready)
                    .count()
                    >= 2
            })
            .await;
            assert!(bridge.status().restarts >= 1);
            bridge.shutdown().await;
        }
    }

    #[test]
    fn protocol_check() {
        assert!(check_protocol(&json!({"protocol_version": 1})).is_ok());
        assert!(check_protocol(&json!({"protocol_version": 2})).is_err());
        assert!(check_protocol(&json!({"protocol_version": "1"})).is_err());
        assert!(check_protocol(&json!({})).is_err());
    }
}
