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

pub const FONT_SIZE_RANGE: (u32, u32) = (14, 96);
/// Longest UI locale tag accepted for the overlay (e.g. `zh-TW`, `pt-BR`).
pub const MAX_LOCALE_LEN: usize = 16;
pub const MAX_LINES_RANGE: (u32, u32) = (1, 3);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OverlayPosition {
    Top,
    Bottom,
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
}

impl Default for OverlayStyle {
    fn default() -> Self {
        Self {
            font_size: 32,
            background_opacity: 0.45,
            position: OverlayPosition::Bottom,
            max_lines: 2,
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
        Ok(())
    }
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
}

#[derive(Debug)]
struct Inner {
    style: OverlayStyle,
    click_through: bool,
    visible: bool,
    shortcut_registered: bool,
    locale: Option<String>,
    show_translation: bool,
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
            shortcut_registered: false,
            locale: None,
            // The engine's default (`translation.enabled: true`) until the main
            // window sends the saved value.
            show_translation: true,
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
            shortcut_registered: s.shortcut_registered,
            locale: s.locale.clone(),
            show_translation: s.show_translation,
        })
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

    pub fn set_shortcut_registered(&self, ok: bool) {
        self.with(|s| s.shortcut_registered = ok);
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
        ];
        for style in bad {
            assert!(style.validate().is_err(), "{style:?}");
        }
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
