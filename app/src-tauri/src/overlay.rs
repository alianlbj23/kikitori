//! Subtitle overlay window state.
//!
//! Role: the overlay window is transparent and may be click-through, so it cannot
//! host its own settings UI. The main window changes the overlay's appearance and
//! behaviour through commands that land here; this module validates those values
//! and keeps the authoritative copy so a freshly loaded overlay can ask for it.
//! Persisting the style is the main window's job (engine `settings.subtitle`).

use std::sync::Mutex;

use serde::{Deserialize, Serialize};

pub const OVERLAY_LABEL: &str = "overlay";
pub const MAIN_LABEL: &str = "main";
/// Global shortcut that toggles click-through; documented in docs/specs/ui.md.
pub const CLICK_THROUGH_SHORTCUT: &str = "CommandOrControl+Alt+K";
/// Global shortcut that toggles "顯示翻譯" (handled by the main window, which
/// owns the engine call).
///
/// 選 J／U 而不是直覺的 T／C：GNOME 的 Ctrl+Alt+T 是開終端機、Ctrl+Alt+L 是鎖定畫面，
/// 波蘭文等鍵盤的 AltGr（= Ctrl+Alt）+C／E／L／S／X 會打出字母；J、U 沒有這些衝突，
/// 而且 J 就在既有的 K 旁邊。
pub const TOGGLE_TRANSLATION_SHORTCUT: &str = "CommandOrControl+Alt+J";
/// Global shortcut that clears the subtitles on screen (overlay and OBS text).
pub const CLEAR_SUBTITLES_SHORTCUT: &str = "CommandOrControl+Alt+U";
/// Interpret mode: show "could you say that again?" in the partner's language
/// (docs/specs/v0.3-features.md G3; handled by the main window, which knows
/// the partner language and whether an interpret session runs).
///
/// R／P／H avoid the AltGr letters of Polish (A C E L N O S X Z), German
/// (Q E M) and French AZERTY (E) layouts, like J／U above.
pub const SAY_AGAIN_SHORTCUT: &str = "CommandOrControl+Alt+R";
/// Freezes / unfreezes what the overlay shows (G6; the engine keeps running).
pub const PAUSE_SUBTITLES_SHORTCUT: &str = "CommandOrControl+Alt+P";
/// Briefly shows the last few final lines on the overlay (G6).
pub const SHOW_HISTORY_SHORTCUT: &str = "CommandOrControl+Alt+H";

pub const FONT_SIZE_RANGE: (u32, u32) = (14, 96);
/// Longest UI locale tag accepted for the overlay (e.g. `zh-TW`, `pt-BR`).
pub const MAX_LOCALE_LEN: usize = 16;

/// Speaker names the main window may hand the overlay (docs/specs/speaker-labels.md):
/// index `i` names `voice` `i + 1`. The engine labels at most 6 speakers.
pub const MAX_SPEAKER_NAMES: usize = 16;
/// Longest speaker name, in characters (code points).
pub const MAX_SPEAKER_NAME_CHARS: usize = 40;
pub const MAX_LINES_RANGE: (u32, u32) = (1, 3);
/// How long a line stays before a newer one may push it out (reading rhythm).
pub const MIN_DISPLAY_MS_RANGE: (u32, u32) = (0, 10_000);
/// Seconds without new subtitles before the overlay fades out; 0 = never.
pub const IDLE_CLEAR_SECS_RANGE: (u32, u32) = (0, 600);
pub const DEFAULT_MIN_DISPLAY_MS: u32 = 1500;
pub const DEFAULT_IDLE_CLEAR_SECS: u32 = 8;
/// Widest line in em (G7 reading layout); 0 = as wide as the window.
pub const MAX_LINE_EM_RANGE: (u32, u32) = (10, 60);
/// Original line size relative to the translation, in percent (G7).
pub const ORIGINAL_SCALE_PERCENT_RANGE: (u32, u32) = (50, 150);
/// The size the overlay always used before G7 (0.62 × the translation).
pub const DEFAULT_ORIGINAL_SCALE_PERCENT: u32 = 62;

fn default_original_scale_percent() -> u32 {
    DEFAULT_ORIGINAL_SCALE_PERCENT
}

fn default_min_display_ms() -> u32 {
    DEFAULT_MIN_DISPLAY_MS
}

fn default_idle_clear_secs() -> u32 {
    DEFAULT_IDLE_CLEAR_SECS
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OverlayPosition {
    Top,
    Bottom,
}

/// Where the original sits relative to the translation (G7 reading layout).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OriginalPosition {
    #[default]
    Above,
    Below,
}

/// Appearance of the overlay; field names match `settings.subtitle` in the engine.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OverlayStyle {
    /// Translation line size in CSS px; the original line is derived from it.
    pub font_size: u32,
    /// Opacity of the backing plate behind the text, 0–1.
    pub background_opacity: f64,
    /// Which edge of the overlay window the text sticks to.
    pub position: OverlayPosition,
    /// How many utterances the overlay shows at once.
    pub max_lines: u32,
    /// A line stays at least this long before a newer one pushes it out, so
    /// fast speech does not scroll lines away before they can be read.
    /// Defaulted so a main window that predates the field is still accepted.
    #[serde(default = "default_min_display_ms")]
    pub min_display_ms: u32,
    /// Seconds after the last change before the overlay fades out and the OBS
    /// text file is emptied; 0 keeps the last lines on screen.
    #[serde(default = "default_idle_clear_secs")]
    pub idle_clear_secs: u32,
    /// G7: long lines stay longer (characters ÷ reading speed), never building a backlog.
    #[serde(default)]
    pub pace_by_length: bool,
    /// G7: widest line in em, so a long sentence wraps into two short lines; 0 = no limit.
    #[serde(default)]
    pub max_line_em: u32,
    /// G7: original line size as a percentage of the translation's (learning layout).
    #[serde(default = "default_original_scale_percent")]
    pub original_scale_percent: u32,
    /// G7: the original above (default) or below the translation.
    #[serde(default)]
    pub original_position: OriginalPosition,
    /// G7: furigana only on a word's first appearance in the session (overlay only).
    #[serde(default)]
    pub furigana_first_only: bool,
}

impl Default for OverlayStyle {
    fn default() -> Self {
        Self {
            font_size: 32,
            background_opacity: 0.45,
            position: OverlayPosition::Bottom,
            max_lines: 2,
            min_display_ms: DEFAULT_MIN_DISPLAY_MS,
            idle_clear_secs: DEFAULT_IDLE_CLEAR_SECS,
            pace_by_length: false,
            max_line_em: 0,
            original_scale_percent: DEFAULT_ORIGINAL_SCALE_PERCENT,
            original_position: OriginalPosition::Above,
            furigana_first_only: false,
        }
    }
}

impl OverlayStyle {
    pub fn validate(&self) -> Result<(), String> {
        let (lo, hi) = FONT_SIZE_RANGE;
        if !(lo..=hi).contains(&self.font_size) {
            return Err(format!(
                "font_size must be in [{lo}, {hi}], got {}",
                self.font_size
            ));
        }
        if !self.background_opacity.is_finite() || !(0.0..=1.0).contains(&self.background_opacity) {
            return Err(format!(
                "background_opacity must be in [0, 1], got {}",
                self.background_opacity
            ));
        }
        let (lo, hi) = MAX_LINES_RANGE;
        if !(lo..=hi).contains(&self.max_lines) {
            return Err(format!(
                "max_lines must be in [{lo}, {hi}], got {}",
                self.max_lines
            ));
        }
        let (lo, hi) = MIN_DISPLAY_MS_RANGE;
        if !(lo..=hi).contains(&self.min_display_ms) {
            return Err(format!(
                "min_display_ms must be in [{lo}, {hi}], got {}",
                self.min_display_ms
            ));
        }
        let (lo, hi) = IDLE_CLEAR_SECS_RANGE;
        if !(lo..=hi).contains(&self.idle_clear_secs) {
            return Err(format!(
                "idle_clear_secs must be in [{lo}, {hi}], got {}",
                self.idle_clear_secs
            ));
        }
        let (lo, hi) = MAX_LINE_EM_RANGE;
        if self.max_line_em != 0 && !(lo..=hi).contains(&self.max_line_em) {
            return Err(format!(
                "max_line_em must be 0 or in [{lo}, {hi}], got {}",
                self.max_line_em
            ));
        }
        let (lo, hi) = ORIGINAL_SCALE_PERCENT_RANGE;
        if !(lo..=hi).contains(&self.original_scale_percent) {
            return Err(format!(
                "original_scale_percent must be in [{lo}, {hi}], got {}",
                self.original_scale_percent
            ));
        }
        Ok(())
    }
}

/// What a global shortcut does.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ShortcutAction {
    ClickThrough,
    ToggleTranslation,
    ClearSubtitles,
    SayAgain,
    PauseSubtitles,
    ShowHistory,
}

impl ShortcutAction {
    pub const ALL: [ShortcutAction; 6] = [
        ShortcutAction::ClickThrough,
        ShortcutAction::ToggleTranslation,
        ShortcutAction::ClearSubtitles,
        ShortcutAction::SayAgain,
        ShortcutAction::PauseSubtitles,
        ShortcutAction::ShowHistory,
    ];

    pub fn keys(self) -> &'static str {
        match self {
            ShortcutAction::ClickThrough => CLICK_THROUGH_SHORTCUT,
            ShortcutAction::ToggleTranslation => TOGGLE_TRANSLATION_SHORTCUT,
            ShortcutAction::ClearSubtitles => CLEAR_SUBTITLES_SHORTCUT,
            ShortcutAction::SayAgain => SAY_AGAIN_SHORTCUT,
            ShortcutAction::PauseSubtitles => PAUSE_SUBTITLES_SHORTCUT,
            ShortcutAction::ShowHistory => SHOW_HISTORY_SHORTCUT,
        }
    }

    fn index(self) -> usize {
        match self {
            ShortcutAction::ClickThrough => 0,
            ShortcutAction::ToggleTranslation => 1,
            ShortcutAction::ClearSubtitles => 2,
            ShortcutAction::SayAgain => 3,
            ShortcutAction::PauseSubtitles => 4,
            ShortcutAction::ShowHistory => 5,
        }
    }
}

/// One global shortcut as the UI describes it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ShortcutInfo {
    pub action: ShortcutAction,
    pub keys: &'static str,
    /// False when another app owns the combination; the UI then says so
    /// instead of advertising a key that does nothing.
    pub registered: bool,
}

/// Snapshot sent to windows (`overlay_get_state` and `overlay://state`).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct OverlaySnapshot {
    pub style: OverlayStyle,
    pub click_through: bool,
    pub visible: bool,
    pub shortcut: &'static str,
    /// False when the global shortcut could not be registered (taken by
    /// another app); the UI then only offers the in-app toggle.
    pub shortcut_registered: bool,
    /// UI locale chosen in the main window (e.g. `zh-TW`, `en`); `None` until
    /// the main window has sent one, and the overlay then follows the OS.
    pub locale: Option<String>,
    /// Settings `translation.enabled`: false = "original only", and the overlay
    /// shows the original in the translation's (primary) style. Kept here rather
    /// than in [`OverlayStyle`] because it is not appearance: the engine stores
    /// it under `translation`, not in the opaque `subtitle` object.
    pub show_translation: bool,
    /// Every global shortcut (click-through included) and whether it could be
    /// registered. `shortcut`/`shortcut_registered` stay for older front ends.
    pub shortcuts: Vec<ShortcutInfo>,
    /// Names the user gave the speaker labels this session; index `i` is
    /// `voice` `i + 1`, and an empty string (or a missing entry) means the
    /// default "Speaker N".
    pub speaker_names: Vec<String>,
}

#[derive(Debug)]
struct Inner {
    style: OverlayStyle,
    click_through: bool,
    visible: bool,
    /// Indexed by [`ShortcutAction::index`].
    shortcuts_registered: [bool; ShortcutAction::ALL.len()],
    locale: Option<String>,
    show_translation: bool,
    speaker_names: Vec<String>,
}

/// Checks speaker names: at most [`MAX_SPEAKER_NAMES`] of at most
/// [`MAX_SPEAKER_NAME_CHARS`] characters, no control characters (they are
/// shown as text, so markup is harmless, but line breaks would break the chip).
pub fn validate_speaker_names(names: &[String]) -> Result<(), String> {
    if names.len() > MAX_SPEAKER_NAMES {
        return Err(format!(
            "at most {MAX_SPEAKER_NAMES} speaker names, got {}",
            names.len()
        ));
    }
    for name in names {
        if name.chars().count() > MAX_SPEAKER_NAME_CHARS {
            return Err(format!(
                "a speaker name is longer than {MAX_SPEAKER_NAME_CHARS} characters"
            ));
        }
        if name.chars().any(char::is_control) {
            return Err("a speaker name contains a control character".to_owned());
        }
    }
    Ok(())
}

/// Checks a UI locale tag: a 2–3 letter language subtag, optionally followed
/// by one 2–8 character alphanumeric subtag (`en`, `zh-TW`, `pt-BR`).
///
/// 字幕視窗只拿這個值來選字典與設定 `<html lang>`；這裡只擋掉格式不對的值
/// （避免任意字串經由事件廣播出去），不列舉支援的語系，前端新增語系時不必改 Rust。
pub fn validate_locale(locale: &str) -> Result<(), String> {
    let bad = || format!("locale must look like `en` or `zh-TW`, got {locale:?}");
    if locale.len() > MAX_LOCALE_LEN {
        return Err(bad());
    }
    let mut parts = locale.split('-');
    let language = parts.next().unwrap_or_default();
    if !(2..=3).contains(&language.len()) || !language.bytes().all(|b| b.is_ascii_alphabetic()) {
        return Err(bad());
    }
    if let Some(region) = parts.next()
        && (!(2..=8).contains(&region.len()) || !region.bytes().all(|b| b.is_ascii_alphanumeric()))
    {
        return Err(bad());
    }
    if parts.next().is_some() {
        return Err(bad());
    }
    Ok(())
}

/// Tauri-managed overlay state.
#[derive(Debug)]
pub struct OverlayState(Mutex<Inner>);

impl Default for OverlayState {
    fn default() -> Self {
        Self(Mutex::new(Inner {
            style: OverlayStyle::default(),
            // Start interactive so a first-time user can move the window; the
            // shortcut makes it click-through once it is in place.
            click_through: false,
            visible: false,
            shortcuts_registered: [false; ShortcutAction::ALL.len()],
            locale: None,
            // The engine's default (`translation.enabled: true`) until the main
            // window sends the saved value.
            show_translation: true,
            speaker_names: Vec::new(),
        }))
    }
}

impl OverlayState {
    fn with<T>(&self, f: impl FnOnce(&mut Inner) -> T) -> T {
        let mut guard = self.0.lock().unwrap_or_else(|p| p.into_inner());
        f(&mut guard)
    }

    pub fn snapshot(&self) -> OverlaySnapshot {
        self.with(|s| OverlaySnapshot {
            style: s.style.clone(),
            click_through: s.click_through,
            visible: s.visible,
            shortcut: CLICK_THROUGH_SHORTCUT,
            shortcut_registered: s.shortcuts_registered[ShortcutAction::ClickThrough.index()],
            locale: s.locale.clone(),
            show_translation: s.show_translation,
            shortcuts: ShortcutAction::ALL
                .iter()
                .map(|&action| ShortcutInfo {
                    action,
                    keys: action.keys(),
                    registered: s.shortcuts_registered[action.index()],
                })
                .collect(),
            speaker_names: s.speaker_names.clone(),
        })
    }

    pub fn set_speaker_names(&self, names: Vec<String>) -> Result<(), String> {
        validate_speaker_names(&names)?;
        self.with(|s| s.speaker_names = names);
        Ok(())
    }

    pub fn set_show_translation(&self, enabled: bool) {
        self.with(|s| s.show_translation = enabled);
    }

    pub fn set_locale(&self, locale: String) -> Result<(), String> {
        validate_locale(&locale)?;
        self.with(|s| s.locale = Some(locale));
        Ok(())
    }

    pub fn set_style(&self, style: OverlayStyle) -> Result<(), String> {
        style.validate()?;
        self.with(|s| s.style = style);
        Ok(())
    }

    pub fn set_click_through(&self, enabled: bool) {
        self.with(|s| s.click_through = enabled);
    }

    pub fn set_visible(&self, visible: bool) {
        self.with(|s| s.visible = visible);
    }

    pub fn set_shortcut_registered(&self, action: ShortcutAction, ok: bool) {
        self.with(|s| s.shortcuts_registered[action.index()] = ok);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_style_is_valid() {
        assert!(OverlayStyle::default().validate().is_ok());
    }

    #[test]
    fn style_ranges() {
        let bad = [
            OverlayStyle {
                font_size: 13,
                ..Default::default()
            },
            OverlayStyle {
                font_size: 97,
                ..Default::default()
            },
            OverlayStyle {
                background_opacity: -0.1,
                ..Default::default()
            },
            OverlayStyle {
                background_opacity: 1.5,
                ..Default::default()
            },
            OverlayStyle {
                background_opacity: f64::NAN,
                ..Default::default()
            },
            OverlayStyle {
                max_lines: 0,
                ..Default::default()
            },
            OverlayStyle {
                max_lines: 4,
                ..Default::default()
            },
            OverlayStyle {
                min_display_ms: 10_001,
                ..Default::default()
            },
            OverlayStyle {
                idle_clear_secs: 601,
                ..Default::default()
            },
        ];
        for style in bad {
            assert!(style.validate().is_err(), "{style:?}");
        }
    }

    #[test]
    fn reading_rhythm_fields_default_when_missing_and_accept_their_bounds() {
        // A main window that predates the fields still sends a valid style.
        let old: OverlayStyle = serde_json::from_str(
            r#"{"font_size":40,"background_opacity":0.2,"position":"top","max_lines":1}"#,
        )
        .unwrap();
        assert_eq!(old.min_display_ms, DEFAULT_MIN_DISPLAY_MS);
        assert_eq!(old.idle_clear_secs, DEFAULT_IDLE_CLEAR_SECS);
        for (ms, secs) in [(0, 0), (10_000, 600), (1500, 8)] {
            let style = OverlayStyle {
                min_display_ms: ms,
                idle_clear_secs: secs,
                ..Default::default()
            };
            assert!(style.validate().is_ok(), "{style:?}");
        }
        assert!(
            serde_json::from_str::<OverlayStyle>(
                r#"{"font_size":40,"background_opacity":0.2,"position":"top","max_lines":1,"min_display_ms":-1}"#
            )
            .is_err()
        );
    }

    #[test]
    fn shortcuts_are_listed_with_their_registration() {
        let state = OverlayState::default();
        state.set_shortcut_registered(ShortcutAction::ClickThrough, true);
        state.set_shortcut_registered(ShortcutAction::ClearSubtitles, true);
        let snap = state.snapshot();
        assert!(snap.shortcut_registered);
        let json = serde_json::to_value(&snap).unwrap();
        assert_eq!(
            json["shortcuts"],
            serde_json::json!([
                {"action": "click_through", "keys": CLICK_THROUGH_SHORTCUT, "registered": true},
                {"action": "toggle_translation", "keys": TOGGLE_TRANSLATION_SHORTCUT, "registered": false},
                {"action": "clear_subtitles", "keys": CLEAR_SUBTITLES_SHORTCUT, "registered": true},
                {"action": "say_again", "keys": SAY_AGAIN_SHORTCUT, "registered": false},
                {"action": "pause_subtitles", "keys": PAUSE_SUBTITLES_SHORTCUT, "registered": false},
                {"action": "show_history", "keys": SHOW_HISTORY_SHORTCUT, "registered": false},
            ])
        );
        let keys: std::collections::HashSet<_> =
            ShortcutAction::ALL.iter().map(|a| a.keys()).collect();
        assert_eq!(
            keys.len(),
            ShortcutAction::ALL.len(),
            "each action needs its own key combination"
        );
        for (i, action) in ShortcutAction::ALL.iter().enumerate() {
            assert_eq!(action.index(), i, "index() must follow ALL");
        }
    }

    #[test]
    fn reading_layout_fields_default_and_are_checked() {
        // A main window from before G7 sends none of the new fields: the old look.
        let old: OverlayStyle = serde_json::from_str(
            r#"{"font_size":40,"background_opacity":0.2,"position":"top","max_lines":1}"#,
        )
        .unwrap();
        assert!(!old.pace_by_length && !old.furigana_first_only);
        assert_eq!(old.max_line_em, 0);
        assert_eq!(old.original_scale_percent, DEFAULT_ORIGINAL_SCALE_PERCENT);
        assert_eq!(old.original_position, OriginalPosition::Above);
        assert!(old.validate().is_ok());
        let new: OverlayStyle = serde_json::from_str(
            r#"{"font_size":40,"background_opacity":0.2,"position":"top","max_lines":1,
                "pace_by_length":true,"max_line_em":13,"original_scale_percent":120,
                "original_position":"below","furigana_first_only":true}"#,
        )
        .unwrap();
        assert!(new.validate().is_ok());
        assert_eq!(new.original_position, OriginalPosition::Below);
        for (field, value) in [
            ("max_line_em", 5),
            ("max_line_em", 61),
            ("original_scale_percent", 49),
            ("original_scale_percent", 151),
        ] {
            let mut bad = new.clone();
            match field {
                "max_line_em" => bad.max_line_em = value,
                _ => bad.original_scale_percent = value,
            }
            assert!(bad.validate().is_err(), "{field}={value}");
        }
        assert!(
            serde_json::from_str::<OverlayStyle>(
                r#"{"font_size":40,"background_opacity":0.2,"position":"top","max_lines":1,"original_position":"left"}"#
            )
            .is_err()
        );
    }

    #[test]
    fn style_rejects_unknown_fields_and_bad_position() {
        let ok: OverlayStyle = serde_json::from_str(
            r#"{"font_size":40,"background_opacity":0.2,"position":"top","max_lines":1}"#,
        )
        .unwrap();
        assert_eq!(ok.position, OverlayPosition::Top);
        assert!(
            serde_json::from_str::<OverlayStyle>(
                r#"{"font_size":40,"background_opacity":0.2,"position":"left","max_lines":1}"#
            )
            .is_err()
        );
        assert!(serde_json::from_str::<OverlayStyle>(
            r#"{"font_size":40,"background_opacity":0.2,"position":"top","max_lines":1,"color":"red"}"#
        )
        .is_err());
    }

    #[test]
    fn state_updates() {
        let state = OverlayState::default();
        assert!(!state.snapshot().click_through);
        state.set_click_through(true);
        assert!(state.snapshot().click_through);
        state.set_visible(true);
        assert!(state.snapshot().visible);
        let bad = OverlayStyle {
            font_size: 2,
            ..Default::default()
        };
        assert!(state.set_style(bad).is_err());
        assert_eq!(state.snapshot().style, OverlayStyle::default());
    }

    #[test]
    fn show_translation_starts_on_and_follows_the_main_window() {
        let state = OverlayState::default();
        assert!(state.snapshot().show_translation);
        state.set_show_translation(false);
        assert!(!state.snapshot().show_translation);
        // The flag survives a style change (they are set by different commands).
        state
            .set_style(OverlayStyle {
                font_size: 40,
                ..Default::default()
            })
            .unwrap();
        assert!(!state.snapshot().show_translation);
        let json = serde_json::to_value(state.snapshot()).unwrap();
        assert_eq!(json["show_translation"], serde_json::json!(false));
        state.set_show_translation(true);
        assert!(state.snapshot().show_translation);
    }

    #[test]
    fn speaker_names_start_empty_and_are_validated() {
        let state = OverlayState::default();
        assert!(state.snapshot().speaker_names.is_empty());
        let names = vec!["ぺこら".to_owned(), String::new(), "<b>x</b>".to_owned()];
        state.set_speaker_names(names.clone()).unwrap();
        assert_eq!(state.snapshot().speaker_names, names);
        let json = serde_json::to_value(state.snapshot()).unwrap();
        assert_eq!(json["speaker_names"][0], serde_json::json!("ぺこら"));
        let long = "あ".repeat(MAX_SPEAKER_NAME_CHARS + 1);
        for bad in [
            vec![String::new(); MAX_SPEAKER_NAMES + 1],
            vec![long],
            vec!["a\nb".to_owned()],
            vec!["tab\there".to_owned()],
        ] {
            assert!(state.set_speaker_names(bad.clone()).is_err(), "{bad:?}");
        }
        // A rejected list keeps the old names.
        assert_eq!(state.snapshot().speaker_names, names);
        assert!(
            state
                .set_speaker_names(vec!["あ".repeat(MAX_SPEAKER_NAME_CHARS)])
                .is_ok()
        );
    }

    #[test]
    fn locale_starts_unset_and_accepts_ui_locale_tags() {
        let state = OverlayState::default();
        assert_eq!(state.snapshot().locale, None);
        for ok in ["en", "zh-TW", "pt-BR", "zh-CN", "id", "fil"] {
            assert!(validate_locale(ok).is_ok(), "{ok}");
        }
        state.set_locale("ja".to_owned()).unwrap();
        assert_eq!(state.snapshot().locale.as_deref(), Some("ja"));
    }

    #[test]
    fn locale_rejects_malformed_tags_and_keeps_the_old_value() {
        let state = OverlayState::default();
        state.set_locale("en".to_owned()).unwrap();
        for bad in [
            "",
            "e",
            "english",
            "en-",
            "en-US-x",
            "zh_TW",
            "<b>",
            "en-a",
            "en-abcdefghi",
            "ja-\u{65e5}\u{672c}",
            "aaaaaaaaaaaaaaaaa",
        ] {
            assert!(state.set_locale(bad.to_owned()).is_err(), "{bad:?}");
        }
        assert_eq!(state.snapshot().locale.as_deref(), Some("en"));
    }
}
