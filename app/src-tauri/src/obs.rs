//! OBS text output: the current subtitles mirrored into a plain text file.
//!
//! Role: OBS's "Text (GDI+) → Read from file" polls a file, so kikitori writes
//! what the overlay shows into `<Documents>/kikitori/obs/subtitle.txt` without
//! opening a network port. It runs here, on the Rust side where subtitle
//! events already arrive, rather than in a webview: a minimized or hidden
//! window has its timers throttled, and streamers often hide both windows.
//!
//! - [`ObsLines`] applies the same rules as `app/src/lib/subtitles.ts`
//!   (in-place partial → final, late partials ignored, blank final clears,
//!   newest `max_lines`, newer session starts afresh) — pure and tested.
//! - [`ObsOutput`] owns a writer thread: writes are coalesced and throttled to
//!   at most one per [`MIN_WRITE_INTERVAL`], atomic (temp + rename), and the
//!   file is emptied on session end, the "clear subtitles" shortcut, idle
//!   (`idle_clear_secs`) and when the output is switched off. Nothing touches
//!   the disk until the user turns the output on.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::output::write_atomic;
use crate::overlay::OverlayStyle;
use crate::protocol::SubtitlePayload;

/// At most 10 writes per second: OBS re-reads the file about once a second
/// anyway, and partial results can arrive many times per second.
pub const MIN_WRITE_INTERVAL: Duration = Duration::from_millis(100);

/// Which text goes into the file (`settings.subtitle.obs_output`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ObsContent {
    #[default]
    Off,
    Original,
    Translation,
    Both,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Line {
    id: u64,
    original: String,
    translation: String,
    is_final: bool,
}

/// The lines on screen, following the overlay's rules (`lib/subtitles.ts`).
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct ObsLines {
    session_id: Option<u64>,
    lines: Vec<Line>,
    /// Highest utterance id finalised in this session (survives clearing).
    last_final_id: u64,
}

fn is_blank(ev: &SubtitlePayload) -> bool {
    ev.original.trim().is_empty() && ev.translation.trim().is_empty()
}

impl ObsLines {
    /// Applies one subtitle event; returns whether the lines changed.
    pub fn apply(&mut self, ev: &SubtitlePayload, max_lines: usize) -> bool {
        let max_lines = max_lines.max(1);
        if let Some(sid) = ev.session_id
            && self.session_id != Some(sid)
        {
            if self.session_id.is_some_and(|cur| sid < cur) {
                return false; // late event from a session that already ended
            }
            *self = ObsLines {
                session_id: Some(sid),
                ..ObsLines::default()
            };
        }
        let index = self.lines.iter().position(|l| l.id == ev.utterance_id);
        if !ev.is_final {
            let finalised = index.is_some_and(|i| self.lines[i].is_final);
            if finalised || ev.utterance_id <= self.last_final_id || is_blank(ev) {
                return false;
            }
        } else {
            self.last_final_id = self.last_final_id.max(ev.utterance_id);
            if is_blank(ev) {
                // The engine decided it was not speech: remove the line.
                return match index {
                    Some(i) => {
                        self.lines.remove(i);
                        true
                    }
                    None => false,
                };
            }
        }
        let line = Line {
            id: ev.utterance_id,
            original: ev.original.clone(),
            translation: ev.translation.clone(),
            is_final: ev.is_final,
        };
        match index {
            Some(i) if self.lines[i] == line => return false,
            Some(i) => self.lines[i] = line,
            None => {
                let at = self.lines.partition_point(|l| l.id < line.id);
                self.lines.insert(at, line);
            }
        }
        if self.lines.len() > max_lines {
            let excess = self.lines.len() - max_lines;
            self.lines.drain(..excess);
        }
        true
    }

    /// Removes every line (session end, idle, the clear shortcut). Old partials
    /// stay ignored because `last_final_id` is kept.
    pub fn clear(&mut self) -> bool {
        let had = !self.lines.is_empty();
        self.lines.clear();
        had
    }

    pub fn is_empty(&self) -> bool {
        self.lines.is_empty()
    }

    /// Keeps only the newest `max_lines` (the user lowered "lines on screen").
    pub fn trim(&mut self, max_lines: usize) {
        let max_lines = max_lines.max(1);
        if self.lines.len() > max_lines {
            let excess = self.lines.len() - max_lines;
            self.lines.drain(..excess);
        }
    }

    /// The file's text. One utterance per paragraph; with `Both` the
    /// translation follows its original on the next line. Without translations
    /// ("顯示翻譯" off, or not translated yet) the original stands in, so the
    /// file is never empty while something is on screen.
    pub fn text(&self, content: ObsContent, show_translation: bool) -> String {
        let one_line = |s: &str| s.split_whitespace().collect::<Vec<_>>().join(" ");
        let mut out: Vec<String> = Vec::new();
        for line in &self.lines {
            let original = one_line(&line.original);
            let translation = if show_translation {
                one_line(&line.translation)
            } else {
                String::new()
            };
            match content {
                ObsContent::Off => {}
                ObsContent::Original => out.push(original),
                ObsContent::Translation if translation.is_empty() => out.push(original),
                ObsContent::Translation => out.push(translation),
                ObsContent::Both => {
                    out.push(original);
                    if !translation.is_empty() {
                        out.push(translation);
                    }
                }
            }
        }
        out.retain(|s| !s.is_empty());
        out.join("\n")
    }
}

/// What the user chose plus what the overlay currently uses.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Config {
    content: ObsContent,
    show_translation: bool,
    max_lines: usize,
    /// `None` = never clear on idle.
    idle_clear: Option<Duration>,
}

impl Default for Config {
    fn default() -> Self {
        let style = OverlayStyle::default();
        Self {
            content: ObsContent::Off,
            show_translation: true,
            max_lines: style.max_lines as usize,
            idle_clear: idle_from_secs(style.idle_clear_secs),
        }
    }
}

fn idle_from_secs(secs: u32) -> Option<Duration> {
    (secs > 0).then(|| Duration::from_secs(u64::from(secs)))
}

#[derive(Debug)]
struct Inner {
    lines: ObsLines,
    config: Config,
    last_change: Instant,
    shutdown: bool,
    /// Last write failure, for the settings page.
    last_error: Option<String>,
}

impl Inner {
    fn desired(&self) -> String {
        self.lines
            .text(self.config.content, self.config.show_translation)
    }
}

#[derive(Debug)]
struct Shared {
    inner: Mutex<Inner>,
    wake: Condvar,
    /// `None` when the Documents folder could not be resolved.
    path: Option<PathBuf>,
    /// Successful writes, for tests of the throttle.
    writes: AtomicU64,
}

impl Shared {
    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|p| p.into_inner())
    }
}

/// Status for the settings page (`obs_set_output`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ObsStatus {
    pub content: ObsContent,
    /// Full path of the text file; `None` when the Documents folder is unknown.
    pub path: Option<String>,
    pub error: Option<String>,
}

/// Tauri-managed OBS text output with its writer thread.
#[derive(Debug, Clone)]
pub struct ObsOutput(Arc<Shared>);

impl ObsOutput {
    /// Starts the writer thread for `path` (`None`: output unavailable).
    pub fn spawn(path: Option<PathBuf>) -> Self {
        let shared = Arc::new(Shared {
            inner: Mutex::new(Inner {
                lines: ObsLines::default(),
                config: Config::default(),
                last_change: Instant::now(),
                shutdown: false,
                last_error: None,
            }),
            wake: Condvar::new(),
            path,
            writes: AtomicU64::new(0),
        });
        let worker = Arc::clone(&shared);
        if let Err(e) = thread::Builder::new()
            .name("obs-text".to_owned())
            .spawn(move || writer_loop(&worker))
        {
            log::error!("cannot start the OBS text writer: {e}");
            shared.lock().last_error = Some(format!("cannot start the writer: {e}"));
        }
        Self(shared)
    }

    fn update(&self, f: impl FnOnce(&mut Inner) -> bool) {
        let mut g = self.0.lock();
        if f(&mut g) {
            g.last_change = Instant::now();
            drop(g);
            self.0.wake.notify_all();
        }
    }

    pub fn on_subtitle(&self, ev: &SubtitlePayload) {
        self.update(|g| {
            let max = g.config.max_lines;
            g.lines.apply(ev, max)
        });
    }

    /// Empties the file (session end, engine restart, the clear shortcut).
    pub fn clear(&self) {
        self.update(|g| g.lines.clear());
    }

    pub fn set_content(&self, content: ObsContent) -> ObsStatus {
        self.update(|g| {
            let changed = g.config.content != content;
            g.config.content = content;
            changed
        });
        self.status()
    }

    pub fn set_style(&self, style: &OverlayStyle) {
        self.update(|g| {
            g.config.max_lines = style.max_lines as usize;
            g.config.idle_clear = idle_from_secs(style.idle_clear_secs);
            g.lines.trim(g.config.max_lines);
            true
        });
    }

    pub fn set_show_translation(&self, enabled: bool) {
        self.update(|g| {
            let changed = g.config.show_translation != enabled;
            g.config.show_translation = enabled;
            changed
        });
    }

    pub fn status(&self) -> ObsStatus {
        let g = self.0.lock();
        ObsStatus {
            content: g.config.content,
            path: self.0.path.as_deref().map(crate::commands::display_path),
            error: if self.0.path.is_none() {
                Some("the Documents folder could not be found".to_owned())
            } else {
                g.last_error.clone()
            },
        }
    }

    /// Stops the writer and empties the file if the output is on, so OBS does
    /// not keep showing the last line after kikitori has quit. Blocking but
    /// tiny (one small write); called once on exit.
    pub fn finish(&self) {
        let enabled = {
            let mut g = self.0.lock();
            g.shutdown = true;
            g.config.content != ObsContent::Off
        };
        self.0.wake.notify_all();
        if enabled
            && let Some(path) = &self.0.path
            && let Err(e) = write_file(path, "")
        {
            log::warn!("cannot empty the OBS text file on exit: {e}");
        }
    }

    #[cfg(test)]
    fn writes(&self) -> u64 {
        self.0.writes.load(Ordering::Relaxed)
    }
}

fn write_file(path: &std::path::Path, text: &str) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    write_atomic(path, text.as_bytes())
}

/// Waits for a change, throttles, writes the newest text; clears on idle.
fn writer_loop(shared: &Shared) {
    // None: nothing written yet — the file is not created until the user turns
    // the output on (switching it off later writes one empty file).
    let mut written: Option<String> = None;
    let mut last_write: Option<Instant> = None;
    loop {
        {
            let mut g = shared.lock();
            loop {
                if g.shutdown {
                    return;
                }
                if let Some(idle) = g.config.idle_clear
                    && !g.lines.is_empty()
                    && g.last_change.elapsed() >= idle
                {
                    g.lines.clear();
                    g.last_change = Instant::now();
                }
                let desired = g.desired();
                let needs_write = match &written {
                    None => g.config.content != ObsContent::Off,
                    Some(prev) => *prev != desired,
                };
                if needs_write {
                    break;
                }
                let idle_left = match g.config.idle_clear {
                    Some(idle) if !g.lines.is_empty() => {
                        Some(idle.saturating_sub(g.last_change.elapsed()))
                    }
                    _ => None,
                };
                g = match idle_left {
                    Some(wait) => {
                        shared
                            .wake
                            .wait_timeout(g, wait.max(Duration::from_millis(1)))
                            .unwrap_or_else(|p| p.into_inner())
                            .0
                    }
                    None => shared.wake.wait(g).unwrap_or_else(|p| p.into_inner()),
                };
            }
        }
        // Throttle outside the lock; changes made meanwhile are coalesced into
        // the text read below.
        if let Some(at) = last_write {
            let wait = MIN_WRITE_INTERVAL.saturating_sub(at.elapsed());
            if !wait.is_zero() {
                thread::sleep(wait);
            }
        }
        let text = {
            let g = shared.lock();
            if g.shutdown {
                return;
            }
            g.desired()
        };
        last_write = Some(Instant::now());
        let result = match &shared.path {
            Some(path) => write_file(path, &text).map_err(|e| e.to_string()),
            None => Err("the Documents folder could not be found".to_owned()),
        };
        // Recorded even on failure so a broken path does not spin; the next
        // change retries.
        written = Some(text);
        let mut g = shared.lock();
        match result {
            Ok(()) => {
                shared.writes.fetch_add(1, Ordering::Relaxed);
                g.last_error = None;
            }
            Err(e) => {
                if g.last_error.as_deref() != Some(e.as_str()) {
                    log::error!("cannot write the OBS text file: {e}");
                }
                g.last_error = Some(e);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::output::tests::scratch_dir;

    fn ev(id: u64, original: &str, translation: &str, is_final: bool) -> SubtitlePayload {
        SubtitlePayload {
            utterance_id: id,
            original: original.to_owned(),
            translation: translation.to_owned(),
            source_language: "ja".to_owned(),
            target_language: "zh-TW".to_owned(),
            is_final,
            start_sample: 0,
            end_sample: 16_000,
            session_id: Some(1),
            stable_chars: None,
            translation_stable_chars: None,
            speaker: None,
            voice: None,
            latency_ms: None,
            ruby: None,
        }
    }

    #[test]
    fn lines_follow_the_overlay_rules() {
        let mut l = ObsLines::default();
        assert!(l.apply(&ev(1, "a", "", false), 2));
        assert!(l.apply(&ev(1, "ab", "A", true), 2));
        // A late partial never downgrades a final.
        assert!(!l.apply(&ev(1, "a", "", false), 2));
        assert!(l.apply(&ev(2, "b", "B", true), 2));
        assert!(l.apply(&ev(3, "c", "C", true), 2));
        assert_eq!(l.text(ObsContent::Original, true), "b\nc");
        // A blank final removes its line.
        assert!(l.apply(&ev(3, " ", "", true), 2));
        assert_eq!(l.text(ObsContent::Both, true), "b\nB");
        // Same event twice: no change, so no write.
        assert!(!l.apply(&ev(2, "b", "B", true), 2));
        // Older session dropped; newer one starts afresh.
        let mut old = ev(9, "x", "X", true);
        old.session_id = Some(0);
        assert!(!l.apply(&old, 2));
        let mut newer = ev(1, "n", "N", false);
        newer.session_id = Some(2);
        assert!(l.apply(&newer, 2));
        assert_eq!(l.text(ObsContent::Original, true), "n");
    }

    #[test]
    fn text_by_content_and_translation_switch() {
        let mut l = ObsLines::default();
        l.apply(
            &ev(
                1,
                "\u{3053}\u{3093}\u{306b}\u{3061}\u{306f}\n\u{4e16}\u{754c}",
                "hello  world",
                true,
            ),
            3,
        );
        l.apply(&ev(2, "partial", "", false), 3);
        assert_eq!(l.text(ObsContent::Off, true), "");
        assert_eq!(
            l.text(ObsContent::Both, true),
            "\u{3053}\u{3093}\u{306b}\u{3061}\u{306f} \u{4e16}\u{754c}\nhello world\npartial"
        );
        // Not translated yet: the original stands in.
        assert_eq!(
            l.text(ObsContent::Translation, true),
            "hello world\npartial"
        );
        // "顯示翻譯" off: originals only, whatever the choice.
        assert_eq!(
            l.text(ObsContent::Translation, false),
            "\u{3053}\u{3093}\u{306b}\u{3061}\u{306f} \u{4e16}\u{754c}\npartial"
        );
        assert!(l.clear());
        assert_eq!(l.text(ObsContent::Both, true), "");
        // Clearing keeps last_final_id: an old partial does not come back.
        assert!(!l.apply(&ev(1, "late", "", false), 3));
    }

    fn wait_for(path: &std::path::Path, expected: &str) {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if std::fs::read_to_string(path).is_ok_and(|s| s == expected) {
                return;
            }
            assert!(Instant::now() < deadline, "file never became {expected:?}");
            thread::sleep(Duration::from_millis(10));
        }
    }

    #[test]
    fn writes_only_when_enabled_and_clears_on_off() {
        let dir = scratch_dir("obs");
        let path = dir.join("obs").join("subtitle.txt");
        let obs = ObsOutput::spawn(Some(path.clone()));
        obs.on_subtitle(&ev(1, "a", "A", true));
        thread::sleep(Duration::from_millis(150));
        assert!(!dir.exists(), "nothing is written while the output is off");
        let status = obs.set_content(ObsContent::Both);
        assert_eq!(status.content, ObsContent::Both);
        assert!(status.path.unwrap().ends_with("subtitle.txt"));
        wait_for(&path, "a\nA");
        obs.on_subtitle(&ev(2, "b", "B", true));
        wait_for(&path, "a\nA\nb\nB");
        obs.clear();
        wait_for(&path, "");
        obs.on_subtitle(&ev(3, "c", "C", true));
        wait_for(&path, "c\nC");
        obs.set_content(ObsContent::Off);
        wait_for(&path, "");
        obs.finish();
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn throttles_bursts_and_writes_the_newest_text() {
        let dir = scratch_dir("obs-burst");
        let path = dir.join("subtitle.txt");
        let obs = ObsOutput::spawn(Some(path.clone()));
        obs.set_content(ObsContent::Original);
        wait_for(&path, "");
        let start = Instant::now();
        let before = obs.writes();
        for i in 0..200 {
            obs.on_subtitle(&ev(1, &format!("partial {i}"), "", false));
            thread::sleep(Duration::from_millis(2));
        }
        wait_for(&path, "partial 199");
        let elapsed = start.elapsed().as_secs_f64();
        let writes = (obs.writes() - before) as f64;
        // ≤ 10 per second, plus the first write that may happen immediately.
        assert!(
            writes <= elapsed * 10.0 + 2.0,
            "{writes} writes in {elapsed:.2}s"
        );
        obs.finish();
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn idle_clear_empties_the_file() {
        let dir = scratch_dir("obs-idle");
        let path = dir.join("subtitle.txt");
        let obs = ObsOutput::spawn(Some(path.clone()));
        obs.set_content(ObsContent::Translation);
        {
            // Sub-second idle for the test; settings only allow whole seconds.
            let mut g = obs.0.lock();
            g.config.idle_clear = Some(Duration::from_millis(300));
        }
        // The idle countdown starts when the subtitle arrives (`last_change`), not when
        // this thread notices it in the file: timing from before `on_subtitle` gives a
        // bound that holds however late the poll sees "A" on a slow CI runner.
        let sent = Instant::now();
        obs.on_subtitle(&ev(1, "a", "A", true));
        wait_for(&path, "A");
        wait_for(&path, "");
        assert!(sent.elapsed() >= Duration::from_millis(300));
        obs.finish();
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn finish_empties_the_file_and_reports_missing_documents() {
        let dir = scratch_dir("obs-exit");
        let path = dir.join("subtitle.txt");
        let obs = ObsOutput::spawn(Some(path.clone()));
        obs.set_content(ObsContent::Both);
        obs.on_subtitle(&ev(1, "a", "A", true));
        wait_for(&path, "a\nA");
        obs.finish();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "");
        std::fs::remove_dir_all(&dir).unwrap();

        let nowhere = ObsOutput::spawn(None);
        let status = nowhere.set_content(ObsContent::Both);
        assert_eq!(status.path, None);
        assert!(status.error.is_some());
        nowhere.finish();
    }
}
