//! Files kikitori writes for the user: exported transcripts and the OBS text.
//!
//! Role: the only place that turns webview input into a path on disk. Content
//! is produced by the front end (`lib/export.ts`, pure and tested); this module
//! decides *where* it goes — always inside `<Documents>/kikitori/<folder>` —
//! validates the file name strictly so nothing from the webview can name
//! another location, and writes atomically (temp file + rename) so a crash or a
//! reader (OBS polls the text file) never sees a half-written file.
//!
//! 資料夾位置由 Tauri 的 path API（`document_dir`）決定，不新增 dialog／fs 外掛：
//! 前端不需要任何檔案系統權限。

use std::fs;
use std::io::{self, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use serde::Deserialize;

/// Folder under the user's Documents folder that holds everything below.
pub const OUTPUT_DIR: &str = "kikitori";
pub const TRANSCRIPTS_DIR: &str = "transcripts";
pub const OBS_DIR: &str = "obs";
pub const OBS_FILE: &str = "subtitle.txt";
/// Transcript formats `lib/export.ts` produces.
pub const TRANSCRIPT_EXTENSIONS: [&str; 4] = ["srt", "vtt", "txt", "csv"];
/// Generated names are like `2026-11-06_153000_media.srt` (≈ 30 characters).
pub const MAX_FILE_NAME_CHARS: usize = 100;
/// A whole session of finals is far below this (≈ 200 bytes per line).
pub const MAX_TRANSCRIPT_BYTES: usize = 32 * 1024 * 1024;

/// Which output folder to open in the file manager. The webview only picks a
/// name; the path is always computed here.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OutputFolder {
    Transcripts,
    Obs,
}

impl OutputFolder {
    fn dir_name(self) -> &'static str {
        match self {
            OutputFolder::Transcripts => TRANSCRIPTS_DIR,
            OutputFolder::Obs => OBS_DIR,
        }
    }
}

/// `<documents>/kikitori/<folder>`.
pub fn folder_path(documents: &Path, folder: OutputFolder) -> PathBuf {
    documents.join(OUTPUT_DIR).join(folder.dir_name())
}

/// `<documents>/kikitori/obs/subtitle.txt`.
pub fn obs_file_path(documents: &Path) -> PathBuf {
    folder_path(documents, OutputFolder::Obs).join(OBS_FILE)
}

/// Device names Windows reserves in every folder (`CON.srt` opens the console).
const WINDOWS_RESERVED: [&str; 22] = [
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
    "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];

/// Checks a transcript file name from the webview.
///
/// Accepted: `<stem>.<ext>` where the stem is 1+ ASCII letters, digits, `_`
/// or `-` starting with a letter or digit, `ext` is one of
/// [`TRANSCRIPT_EXTENSIONS`] (lower case), and the whole name is at most
/// [`MAX_FILE_NAME_CHARS`]. That excludes separators, `..`, drive letters,
/// hidden files, trailing dots/spaces (Windows strips them) and every
/// non-ASCII character, so the name can only ever mean one file in the folder.
pub fn validate_transcript_name(name: &str) -> Result<(), String> {
    if name.is_empty() || name.len() > MAX_FILE_NAME_CHARS {
        return Err(format!(
            "file name must be 1–{MAX_FILE_NAME_CHARS} characters"
        ));
    }
    let Some((stem, ext)) = name.rsplit_once('.') else {
        return Err("file name needs an extension".to_owned());
    };
    if !TRANSCRIPT_EXTENSIONS.contains(&ext) {
        return Err(format!(
            "extension must be one of {}",
            TRANSCRIPT_EXTENSIONS.join(", ")
        ));
    }
    let mut chars = stem.bytes();
    let starts_ok = chars.next().is_some_and(|b| b.is_ascii_alphanumeric());
    if !starts_ok || !chars.all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-') {
        return Err(
            "file name may only use letters, digits, `_` and `-`, starting with a letter or digit"
                .to_owned(),
        );
    }
    if WINDOWS_RESERVED
        .iter()
        .any(|r| r.eq_ignore_ascii_case(stem))
    {
        return Err("file name is reserved on Windows".to_owned());
    }
    Ok(())
}

/// `dir/name` after checking that the result is a direct child of `dir`.
///
/// [`validate_transcript_name`] already makes escaping impossible; this second
/// check is on the joined path itself, so a future change to the name rules
/// cannot silently turn into a path traversal.
pub fn contained_path(dir: &Path, name: &str) -> Result<PathBuf, String> {
    validate_transcript_name(name)?;
    let path = dir.join(name);
    let mut rest = path
        .strip_prefix(dir)
        .map_err(|_| "file would be outside the output folder".to_owned())?
        .components();
    match (rest.next(), rest.next()) {
        (Some(Component::Normal(only)), None) if only == name => Ok(path),
        _ => Err("file would be outside the output folder".to_owned()),
    }
}

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

/// Writes `contents` to `path` atomically: a temp file in the same folder is
/// written and flushed, then renamed over `path` (same volume, so the rename
/// replaces the old file in one step on Windows, macOS and Linux).
pub fn write_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let dir = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "path has no parent folder"))?;
    let name = path
        .file_name()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "path has no file name"))?;
    let unique = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    let temp = dir.join(format!(
        ".{}.{}-{unique}.tmp",
        name.to_string_lossy(),
        std::process::id()
    ));
    let result = (|| {
        let mut file = fs::File::create(&temp)?;
        file.write_all(contents)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temp, path)
    })();
    if result.is_err()
        && let Err(e) = fs::remove_file(&temp)
        && e.kind() != io::ErrorKind::NotFound
    {
        log::warn!("cannot remove temp file {}: {e}", temp.display());
    }
    result
}

/// Saves a transcript into `dir` (created if missing) and returns its path.
pub fn save_transcript(dir: &Path, name: &str, contents: &str) -> Result<PathBuf, String> {
    if contents.len() > MAX_TRANSCRIPT_BYTES {
        return Err(format!(
            "transcript is larger than {} MiB",
            MAX_TRANSCRIPT_BYTES / 1024 / 1024
        ));
    }
    let path = contained_path(dir, name)?;
    fs::create_dir_all(dir).map_err(|e| {
        log::error!("cannot create {}: {e}", dir.display());
        format!("cannot create the transcripts folder: {e}")
    })?;
    write_atomic(&path, contents.as_bytes()).map_err(|e| {
        log::error!("cannot write {}: {e}", path.display());
        format!("cannot write the transcript: {e}")
    })?;
    Ok(path)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    /// A fresh folder under the system temp dir (no `tempfile` crate).
    pub(crate) fn scratch_dir(tag: &str) -> PathBuf {
        let unique = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
        let dir = std::env::temp_dir().join(format!(
            "kikitori-test-{tag}-{}-{unique}",
            std::process::id()
        ));
        if dir.exists() {
            fs::remove_dir_all(&dir).unwrap();
        }
        dir
    }

    #[test]
    fn accepts_generated_names() {
        for ok in [
            "2026-11-06_153000_media.srt",
            "2026-11-06_153000_interpret.vtt",
            "a.txt",
            "transcript.csv",
        ] {
            assert!(validate_transcript_name(ok).is_ok(), "{ok}");
        }
    }

    #[test]
    fn rejects_paths_traversal_and_odd_names() {
        let long = format!("{}.srt", "a".repeat(MAX_FILE_NAME_CHARS));
        for bad in [
            "",
            "srt",
            ".srt",
            "..srt",
            "../evil.srt",
            "..\\evil.srt",
            "a/b.srt",
            "a\\b.srt",
            "C:evil.srt",
            "C:\\evil.srt",
            "/etc/passwd.txt",
            "a..b.srt",
            "a.b.srt",
            "_hidden.srt",
            "-flag.srt",
            "name.SRT",
            "name.exe",
            "name.srt.exe",
            "name .srt",
            "name.srt ",
            "name.srt.",
            "na\u{0}me.srt",
            "\u{5b57}\u{5e55}.srt",
            "CON.srt",
            "nul.txt",
            "Com1.csv",
            long.as_str(),
        ] {
            assert!(validate_transcript_name(bad).is_err(), "{bad:?}");
            assert!(contained_path(Path::new("out"), bad).is_err(), "{bad:?}");
        }
    }

    #[test]
    fn contained_path_is_a_direct_child() {
        let dir = Path::new("docs").join("kikitori").join("transcripts");
        let path = contained_path(&dir, "2026-11-06_153000_media.srt").unwrap();
        assert_eq!(path.parent(), Some(dir.as_path()));
        assert_eq!(path.file_name().unwrap(), "2026-11-06_153000_media.srt");
    }

    #[test]
    fn folders_live_under_documents_kikitori() {
        let docs = Path::new("Docs");
        assert_eq!(
            folder_path(docs, OutputFolder::Transcripts),
            docs.join("kikitori").join("transcripts")
        );
        assert_eq!(
            obs_file_path(docs),
            docs.join("kikitori").join("obs").join("subtitle.txt")
        );
        assert_eq!(
            serde_json::from_value::<OutputFolder>(serde_json::json!("obs")).unwrap(),
            OutputFolder::Obs
        );
        for bad in [
            serde_json::json!("../x"),
            serde_json::json!("Transcripts"),
            serde_json::json!(1),
        ] {
            assert!(serde_json::from_value::<OutputFolder>(bad).is_err());
        }
    }

    #[test]
    fn saves_atomically_and_overwrites() {
        let dir = scratch_dir("transcripts");
        let path = save_transcript(&dir, "s.srt", "first").unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "first");
        save_transcript(&dir, "s.srt", "second \u{65e5}").unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "second \u{65e5}");
        // Only the file itself remains: no temp files left behind.
        let names: Vec<_> = fs::read_dir(&dir)
            .unwrap()
            .map(|e| e.unwrap().file_name())
            .collect();
        assert_eq!(names, vec![std::ffi::OsString::from("s.srt")]);
        assert!(save_transcript(&dir, "../s.srt", "x").is_err());
        assert!(!dir.parent().unwrap().join("s.srt").exists());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn rejects_oversized_transcripts_without_creating_anything() {
        let dir = scratch_dir("big");
        let big = "x".repeat(MAX_TRANSCRIPT_BYTES + 1);
        assert!(save_transcript(&dir, "big.txt", &big).is_err());
        assert!(!dir.exists());
    }
}
