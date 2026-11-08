"""v0.2.0 的桌面程式驗收情境（WebDriver），由 `gui_acceptance.py --batch v020-*` 呼叫。

暫時性實驗，不屬於正式程式碼。沿用 `gui_acceptance.py` 的 `AppSession`（tauri-driver +
msedgedriver、只用標準庫）。每個情境都用自己的設定資料夾（`KIKITORI_CONFIG_DIR`）；需要空的
模型快取時把 app 與引擎的 `LOCALAPPDATA` 指到結果資料夾（`asr/cache.py`），不動使用者的快取。

情境：
- `v020-locales`（a）：13 個介面語系逐一切換，主視窗與字幕視窗沒有空白文字、沒有原始的字典鍵；
  zh-TW、en、ja 截圖；最後切回「跟隨系統」。同一次啟動接著跑（f）術語表編輯器。
- `v020-languages`（b＋g）：隔離的模型快取 → 語言選 ko → es，預覽列出模型與下載大小、
  選完與儲存都不下載 → 開始 → 下載面板 → session 開始；之後在模型頁刪除一個模型。
- `v020-original`（c）：只顯示原文、翻譯服務指到一個只收連線不回應的本機埠（引擎視為連不上，
  並記下每個請求）→ 開始、字幕視窗顯示放大的原文、0 個翻譯請求 → 執行中打開翻譯 → 引導訊息。
- `v020-session`（d＋e＋h＋i）：匯出 SRT／VTT／TXT／CSV、自動存檔、OBS 文字檔、穩定前綴、
  音量表與延遲、全域快速鍵 Ctrl+Alt+J／U（以 `keybd_event` 送出）。結束時只刪除本測試建立的檔案。

截圖只拍 kikitori 自己的介面文字（本專案的英文 TTS 句子），放 `screenshots/`。
"""

from __future__ import annotations

import ctypes
import json
import logging
import re
import shutil
import socket
import threading
import time
from pathlib import Path
from typing import Any

from acceptance import EN_LINES, EN_VOICE, tts_clip
from e2e_common import ROOT, Player
from gui_acceptance import (
    SCREENSHOTS,
    AppSession,
    Run,
    finish,
    leftover_processes,
    play_async,
    read_settings,
)

logger = logging.getLogger("e2e.gui.v020")

LOCALES = (
    "zh-TW",
    "zh-CN",
    "en",
    "ja",
    "ko",
    "es",
    "fr",
    "de",
    "pt-BR",
    "ru",
    "vi",
    "th",
    "id",
)
OVERLAY_TITLE_ZH = "kikitori 字幕"
LISTENING = "正在聆聽"
"""狀態列的「執行中」（zh-TW）：模型載入完、開始擷取。"""
DOCUMENTS = Path.home() / "Documents"
OUTPUT_ROOT = DOCUMENTS / "kikitori"
TRANSCRIPTS = OUTPUT_ROOT / "transcripts"
OBS_FILE = OUTPUT_ROOT / "obs" / "subtitle.txt"


def dictionary_keys() -> list[str]:
    """zh-TW 字典（基準）的所有鍵：畫面上出現其中任何一個就是沒翻到的原始鍵。"""
    text = (ROOT / "app" / "src" / "locales" / "zh-TW.ts").read_text("utf-8")
    return sorted(set(re.findall(r'^\s*"([A-Za-z0-9_.]+)":', text, re.MULTILINE)))


I18N_JS = """
const keys = new Set(arguments[0]);
const blanks = [], raw = [];
for (const e of document.querySelectorAll('[data-i18n]')) {
  const k = e.dataset.i18n, v = e.textContent.trim();
  if (!v) blanks.push(k);
  if (keys.has(v)) raw.push(k);
}
for (const [attr, prop] of [['data-i18n-title', 'title'], ['data-i18n-placeholder', 'placeholder'],
                            ['data-i18n-aria-label', 'aria-label']]) {
  for (const e of document.querySelectorAll('[' + attr + ']')) {
    const k = e.getAttribute(attr), v = (e.getAttribute(prop) || '').trim();
    if (!v) blanks.push(attr + ':' + k);
    if (keys.has(v)) raw.push(attr + ':' + k);
  }
}
const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
let n;
while ((n = w.nextNode())) { const s = n.textContent.trim(); if (keys.has(s)) raw.push('text:' + s); }
for (const e of document.querySelectorAll('[title],[placeholder],[aria-label]')) {
  for (const a of ['title', 'placeholder', 'aria-label']) {
    const v = (e.getAttribute(a) || '').trim(); if (keys.has(v)) raw.push(a + ':' + v);
  }
}
const nav = Array.from(document.querySelectorAll('.nav-item')).map(b => b.textContent.trim());
return {lang: document.documentElement.lang, blanks, raw: Array.from(new Set(raw)), nav,
        status: (document.getElementById('status-label') || {}).textContent || null,
        nodes: document.getElementsByTagName('*').length};
"""


# --------------------------------------------------------------------------- 設定


def gpu_settings(**extra: Any) -> dict[str, Any]:
    """預先寫好的設定（不是第一次啟動）：這台電腦的 v0.2.0 推薦組合、影片翻譯 ja → zh-TW。"""
    settings: dict[str, Any] = {
        "schema_version": 3,
        "mode": "media",
        "source_language": "ja",
        "target_language": "zh-TW",
        "asr": {
            "fast_model": "large-v3-turbo",
            "final_model": "large-v3",
            "device": "cuda",
            "fast_compute_type": "int8_float16",
            "final_compute_type": "int8_float16",
        },
        "translation": {
            "enabled": True,
            "base_url": "http://localhost:11434/v1",
            "fast_model": "qwen3.5:9b",
            "final_model": "qwen3.5:9b",
        },
    }
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(settings.get(key), dict):
            settings[key] = {**settings[key], **value}
        else:
            settings[key] = value
    return settings


def write_settings(config_dir: Path, settings: dict[str, Any]) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.json").write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), "utf-8"
    )


def set_value(app: AppSession, css: str, value: str) -> None:
    """設定表單元件的值並送出 input／change（與使用者操作相同的事件）。"""
    ok = app.wd.script(
        "const e = document.querySelector(arguments[0]); if (!e) return false;"
        "e.value = arguments[1];"
        "e.dispatchEvent(new Event('input', {bubbles: true}));"
        "e.dispatchEvent(new Event('change', {bubbles: true}));"
        "return e.value === arguments[1];",
        css,
        value,
    )
    if not ok:
        raise RuntimeError(f"could not set {css} to {value!r}")


def notice_text(app: AppSession, key: str) -> str | None:
    n = app.notice(key)
    return n["text"] if n else None


def path_from_notice(text: str) -> Path:
    """「已儲存字幕紀錄：C:\\…」之類訊息中的路徑（全形冒號之後）。"""
    return Path(text.split("：", 1)[1].strip())


# --------------------------------------------------------------------------- (a) 語系


def scenario_locales(run: Run, app: AppSession) -> None:
    wd = app.wd
    keys = dictionary_keys()
    overlay = app.window(OVERLAY_TITLE_ZH)
    main = app.window("kikitori")
    wd.switch(main)
    app.wait_ready()
    # 各頁先開過一次，讓模型頁、語言清單、關於等動態內容都畫出來
    for page in ("models", "settings", "transcript"):
        app.show_page(page)
        time.sleep(2)
    options = wd.script(
        "return Array.from(document.getElementById('f-ui-locale').options).map(o => o.value);"
    )
    run.check(
        "locale menu lists system + 13 locales",
        options[:1] == ["system"] and sorted(options[1:]) == sorted(LOCALES),
        options,
    )
    results: dict[str, Any] = {}
    navs: dict[str, tuple[str, ...]] = {}
    for locale in LOCALES:
        app.show_page("settings")
        set_value(app, "#f-ui-locale", locale)
        wd.wait(
            f"html lang for {locale}",
            lambda: wd.script("return document.documentElement.lang;") != "",
            timeout=10,
        )
        time.sleep(0.8)
        per_page = {}
        for page in ("transcript", "models", "settings"):
            app.show_page(page)
            time.sleep(0.4)
            per_page[page] = wd.script(I18N_JS, keys)
        wd.switch(overlay)
        ov = wd.wait(
            f"overlay relabelled for {locale}",
            lambda lang=per_page["transcript"]["lang"]: (
                (r := wd.script(I18N_JS, keys)) and r["lang"] == lang and r
            ),
            timeout=10,
        )
        ov_title = wd.title()
        wd.switch(main)
        blanks = sorted({b for r in per_page.values() for b in r["blanks"]})
        raw = sorted({k for r in per_page.values() for k in r["raw"]})
        navs[locale] = tuple(per_page["transcript"]["nav"])
        results[locale] = {
            "lang": per_page["transcript"]["lang"],
            "blanks": blanks,
            "raw_keys": raw,
            "overlay_blanks": ov["blanks"],
            "overlay_raw": ov["raw"],
            "overlay_title": ov_title,
            "nav": navs[locale],
            "status": per_page["transcript"]["status"],
        }
        print(
            f"    {locale}: lang={results[locale]['lang']} blanks={len(blanks)} raw={len(raw)} "
            f"overlay blanks={len(ov['blanks'])} raw={len(ov['raw'])} nav={navs[locale]}"
        )
        if locale in ("zh-TW", "en", "ja"):
            app.show_page("transcript")
            time.sleep(0.5)
            wd.screenshot(SCREENSHOTS / f"20-locale-{locale}.png")
        saved = read_settings(app_config(app)).get("ui", {}).get("locale")
        results[locale]["saved"] = saved
    run.check(
        "every locale: no blank text and no raw dictionary keys (main + overlay)",
        all(
            not r["blanks"]
            and not r["raw_keys"]
            and not r["overlay_blanks"]
            and not r["overlay_raw"]
            for r in results.values()
        ),
        {
            k: {
                kk: v[kk]
                for kk in (
                    "lang",
                    "blanks",
                    "raw_keys",
                    "overlay_blanks",
                    "overlay_raw",
                )
            }
            for k, v in results.items()
        },
    )
    run.check(
        "every locale relabels the UI (13 distinct nav label sets) and is saved to ui.locale",
        len(set(navs.values())) == len(LOCALES)
        and all(results[loc]["saved"] == loc for loc in LOCALES),
        {
            loc: {
                "nav": results[loc]["nav"],
                "saved": results[loc]["saved"],
                "overlay_title": results[loc]["overlay_title"],
            }
            for loc in LOCALES
        },
    )
    app.show_page("settings")
    set_value(app, "#f-ui-locale", "system")
    time.sleep(1.5)
    run.check(
        "back to 'follow system' (zh-TW on this machine), saved as system",
        read_settings(app_config(app)).get("ui", {}).get("locale") == "system"
        and wd.script("return document.documentElement.lang;") == "zh-Hant-TW",
        wd.script("return document.documentElement.lang;"),
    )


def app_config(app: AppSession) -> Path:
    return Path(app.env["KIKITORI_CONFIG_DIR"])


# --------------------------------------------------------------------------- (f) 術語表

GLOSSARY_ROWS_JS = """
return Array.from(document.querySelectorAll('#glossary-rows tr[data-row]')).map(tr => ({
  source: tr.querySelector('[data-field=source]').value,
  target: tr.querySelector('[data-field=target]').value,
  language: tr.querySelector('[data-field=target_language]').value,
  errors: Object.fromEntries(Array.from(tr.querySelectorAll('[data-error-for]'))
           .map(p => [p.dataset.errorFor, p.textContent])),
  invalid: Array.from(tr.querySelectorAll('[aria-invalid=true]')).map(e => e.dataset.field),
}));
"""

FILL_ROW_JS = """
const tr = document.querySelectorAll('#glossary-rows tr[data-row]')[arguments[0]];
for (const [f, v] of [['source', arguments[1]], ['target', arguments[2]], ['target_language', arguments[3]]]) {
  const e = tr.querySelector('[data-field=' + f + ']');
  e.value = v;
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
}
return true;
"""


def scenario_glossary(run: Run, app: AppSession, created: list[Path]) -> None:
    wd = app.wd
    app.show_page("settings")
    wd.wait(
        "glossary form",
        lambda: wd.script("return !!document.getElementById('glossary-add');"),
    )
    good = [
        ("推し", "本命", "zh-TW"),
        ("スパチャ", "Super Chat", ""),
        ("=危険", "+注意", "en"),  # 匯出時的公式防護（'），匯入要拿掉
    ]
    bad = [
        ("推し", "推", "zh-TW"),  # 與第 1 列重複（同一個字幕語言）
        (" 枠", "直播", ""),  # 開頭有空白
    ]
    for i, (src, tgt, lang) in enumerate(good + bad):
        wd.click("#glossary-add")
        wd.script(FILL_ROW_JS, i, src, tgt, lang)
    before = read_settings(app_config(app))
    wd.click("#glossary-save")
    time.sleep(1.0)
    rows = wd.script(GLOSSARY_ROWS_JS)
    status = wd.text("#glossary-status")
    after = read_settings(app_config(app))
    run.check(
        "glossary: invalid rows are marked on the right row/field and nothing is saved",
        bool(rows[3]["errors"].get("source"))
        and bool(rows[4]["errors"].get("source"))
        and not any(rows[i]["errors"].get("source") for i in range(3))
        and before.get("translation", {}).get("glossary")
        == after.get("translation", {}).get("glossary"),
        {"status": status, "row4": rows[3]["errors"], "row5": rows[4]["errors"]},
    )
    # 刪掉兩個錯的列（從後面刪，列號才不會變）
    for idx in (4, 3):
        wd.script(
            "document.querySelectorAll('#glossary-rows tr[data-row]')[arguments[0]]"
            ".querySelector('.glossary-actions button').click();",
            idx,
        )
    wd.click("#glossary-save")
    wd.wait(
        "glossary saved",
        lambda: wd.text("#glossary-status").startswith("已儲存"),
        timeout=20,
    )
    saved = read_settings(app_config(app))["translation"]["glossary"]
    expected = [
        {"source": s, "target": t, "target_language": lang or None}
        for s, t, lang in good
    ]
    run.check(
        "glossary: valid rows saved to translation.glossary.entries",
        saved.get("entries") == expected,
        saved,
    )
    # 匯出 → 清空 → 匯入 → 與原本相同
    wd.click("#glossary-export")
    text = wd.wait(
        "glossary exported",
        lambda: (t := notice_text(app, "export")) and "術語表已匯出" in t and t,
        timeout=20,
    )
    csv_path = path_from_notice(text)
    created.append(csv_path)
    raw = csv_path.read_bytes()
    run.check(
        "glossary CSV export: BOM, header, formula guard",
        raw.startswith(b"\xef\xbb\xbf")
        and raw[3:].decode("utf-8").splitlines()[0] == "source,target,target_language"
        and "'=危険" in raw.decode("utf-8-sig"),
        {"path": str(csv_path), "content": raw.decode("utf-8-sig")},
    )
    for _ in range(len(good)):
        wd.script(
            "document.querySelector('#glossary-rows tr[data-row] .glossary-actions button').click();"
        )
    run.check(
        "glossary table emptied before import",
        wd.script(GLOSSARY_ROWS_JS) == [],
        None,
    )
    file_input = wd.find("#glossary-file")
    try:
        wd.s("POST", f"/element/{file_input}/value", {"text": str(csv_path)})
    except Exception:  # noqa: BLE001 - 隱藏的 file input 被拒時改成先顯示再送
        logger.warning("hidden file input rejected; unhiding it for the test")
        wd.script("document.getElementById('glossary-file').hidden = false;")
        wd.s("POST", f"/element/{file_input}/value", {"text": str(csv_path)})
    status = wd.wait(
        "glossary imported",
        lambda: (t := wd.text("#glossary-status")) and "已匯入" in t and t,
        timeout=20,
    )
    imported = [
        (r["source"], r["target"], r["language"]) for r in wd.script(GLOSSARY_ROWS_JS)
    ]
    wd.click("#glossary-save")
    wd.wait(
        "glossary saved again",
        lambda: wd.text("#glossary-status").startswith("已儲存"),
        timeout=20,
    )
    resaved = read_settings(app_config(app))["translation"]["glossary"]["entries"]
    run.check(
        "glossary CSV round trip: import restores the same entries",
        imported == list(good) and resaved == expected,
        {"status": status, "imported": imported},
    )
    wd.screenshot(SCREENSHOTS / "21-glossary.png")


def run_ui() -> int:
    """(a) 語系＋(f) 術語表：同一次啟動，不需要 session。"""
    run = Run("v020-ui")
    config_dir = run.dir / "config"
    write_settings(config_dir, gpu_settings(ui={"locale": "system"}))
    created: list[Path] = []
    out_existed = OUTPUT_ROOT.exists()
    print(f"results: {run.dir}")
    try:
        with AppSession(run, config_dir, "ui") as app:
            scenario_locales(run, app)
            scenario_glossary(run, app, created)
    except Exception:
        logger.exception("v020 ui acceptance aborted")
        run.steps["aborted"] = {"passed": False, "detail": "exception (see log)"}
    finally:
        cleanup_outputs(run, created, out_existed)
    run.check(
        "no leftover processes",
        not any(leftover_processes().values()),
        leftover_processes(),
    )
    return finish(run)


def cleanup_outputs(run: Run, created: list[Path], out_existed: bool) -> None:
    """只刪本測試建立的檔案；`文件\\kikitori` 是本測試建立的話（之前不存在）整個移除。"""
    removed = []
    for path in created:
        if path.is_file() and OUTPUT_ROOT in path.parents:
            path.unlink()
            removed.append(str(path))
    if not out_existed and OUTPUT_ROOT.exists():
        leftovers = [p for p in OUTPUT_ROOT.rglob("*") if p.is_file()]
        # 只剩本測試可能產生的檔案（OBS 文字檔、空資料夾）才整個移除
        if all(p == OBS_FILE for p in leftovers):
            shutil.rmtree(OUTPUT_ROOT)
            removed.append(str(OUTPUT_ROOT))
        else:
            logger.warning(
                "not removing %s: unexpected files %s", OUTPUT_ROOT, leftovers
            )
    run.steps.setdefault(
        "cleanup (files this test created)", {"passed": True, "detail": removed}
    )


# --------------------------------------------------------------------------- (b)＋(g) 語言與下載


def tree_files(root: Path) -> list[str]:
    return (
        sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
        if root.exists()
        else []
    )


def run_languages() -> int:
    run = Run("v020-languages")
    local = run.dir / "localappdata"
    local.mkdir()
    models_dir = local / "kikitori" / "models"
    config_dir = run.dir / "config"
    config_dir.mkdir()
    print(f"results: {run.dir}")
    user_cache = Path.home() / "AppData" / "Local" / "kikitori" / "models"
    user_before = sorted(p.name for p in user_cache.iterdir())
    try:
        with AppSession(
            run, config_dir, "languages", extra_env={"LOCALAPPDATA": str(local)}
        ) as app:
            wd = app.wd
            app.wait_ready()
            time.sleep(4)  # 第一次啟動的自動套用與缺少模型的檢查
            app.show_page("settings")
            wd.wait(
                "language options",
                lambda: wd.script(
                    "return document.querySelectorAll('#f-source option').length > 10"
                    " && document.querySelectorAll('#f-target option').length > 10;"
                ),
                timeout=60,
            )
            set_value(app, "#f-source", "ko")
            set_value(app, "#f-target", "es")
            preview = wd.wait(
                "language preview",
                lambda: wd.script(
                    "const p = document.getElementById('language-preview');"
                    "const dl = p.querySelector('dl.facts'); if (!dl) return null;"
                    "return {rows: Array.from(dl.querySelectorAll('dt')).map((dt, i) =>"
                    " [dt.textContent, dl.querySelectorAll('dd')[i].textContent]),"
                    " text: p.innerText};"
                ),
                timeout=60,
            )
            time.sleep(3)
            files_after_preview = tree_files(models_dir)
            run.check(
                "ko -> es preview lists models with download sizes",
                len(preview["rows"]) >= 3
                and any("第一次使用時下載，約" in dd for _, dd in preview["rows"])
                and ("共需下載約" in preview["text"]),
                preview,
            )
            wd.screenshot(SCREENSHOTS / "22-language-preview-ko-es.png")
            wd.click("#languages-save")
            status = wd.wait(
                "languages saved",
                lambda: (
                    (t := wd.text("#languages-status")) and t.startswith("已儲存") and t
                ),
                timeout=30,
            )
            time.sleep(3)
            settings = read_settings(config_dir)
            run.check(
                "save stores ko -> es (+ recommended models) and nothing downloads",
                settings["source_language"] == "ko"
                and settings["target_language"] == "es"
                and files_after_preview == []
                and tree_files(models_dir) == [],
                {
                    "status": status,
                    "asr": settings["asr"],
                    "translation": settings["translation"],
                    "files_after_preview": files_after_preview,
                    "files_after_save": tree_files(models_dir),
                },
            )
            device = app.choose_virtual_loopback()
            wd.click("#start")
            panel: list[Any] = []
            t0 = time.monotonic()

            def running() -> bool:
                v = wd.script(
                    "const p = document.getElementById('start-downloads');"
                    "return {hidden: p.hidden, items: Array.from(p.querySelectorAll('li')).map(l => l.innerText),"
                    " message: document.getElementById('startdl-message').textContent,"
                    " state: document.getElementById('start').dataset.state,"
                    " status: document.getElementById('status-label').textContent};"
                )
                if not v["hidden"] and (not panel or panel[-1]["items"] != v["items"]):
                    panel.append({"t": round(time.monotonic() - t0, 1), **v})
                    if len(panel) == 2:
                        wd.screenshot(SCREENSHOTS / "23-start-downloads.png")
                return v["status"] == LISTENING

            wd.wait(
                "session running after downloads", running, timeout=900, interval=0.5
            )
            secs = round(time.monotonic() - t0, 1)
            run.check(
                "Start -> missing-models panel downloads -> session starts by itself",
                bool(panel)
                and any("語音辨識模型" in i for p in panel for i in p["items"]),
                {
                    "device": device,
                    "secs_to_running": secs,
                    "panel_first": panel[:2],
                    "panel_last": panel[-1:] if panel else None,
                    "panel_updates": len(panel),
                    "models_dir": sorted(p.name for p in models_dir.iterdir()),
                },
            )
            time.sleep(5)
            wd.click("#start")
            wd.wait("idle", lambda: wd.text("#status-label") == "待命中", timeout=30)
            # (g) 刪除模型（隔離的快取）
            settings = read_settings(config_dir)
            victim = settings["asr"]["fast_model"]
            app.show_page("models")
            wd.wait(
                "models table",
                lambda: wd.script(
                    "return Array.from(document.querySelectorAll('#asr-models tr'))"
                    ".some(tr => tr.querySelector('.model-disk'));"
                ),
                timeout=60,
            )
            before_dirs = sorted(p.name for p in models_dir.iterdir())
            row_js = (
                "const tr = Array.from(document.querySelectorAll('#asr-models tr')).find(r =>"
                " r.querySelector('.model-id') && r.querySelector('.model-id').textContent === arguments[0]);"
                "if (!tr) return {row: null};"
                "const b = Array.from(tr.querySelectorAll('button')).find(x => x.textContent.trim() === '刪除');"
                "return {row: tr.innerText, button: !!b, disabled: b ? b.disabled : null, title: b ? b.title : null};"
            )
            row = {}

            def deletable() -> bool:
                nonlocal row
                row = wd.script(row_js, victim)
                return bool(row.get("button")) and row.get("disabled") is False

            try:
                wd.wait("delete button enabled", deletable, timeout=60, interval=1.0)
            finally:
                print(f"    model row for {victim}: {row}")
            clicked = wd.script(
                "const tr = Array.from(document.querySelectorAll('#asr-models tr')).find(r =>"
                " r.querySelector('.model-id') && r.querySelector('.model-id').textContent === arguments[0]);"
                "const b = Array.from(tr.querySelectorAll('button')).find(x => x.textContent.trim() === '刪除');"
                "b.click(); return true;",
                victim,
            )
            confirm = wd.wait(
                "delete confirmation",
                lambda: wd.script(
                    "const c = document.querySelector('#asr-models .delete-confirm');"
                    "return c ? c.innerText : null;"
                ),
                timeout=10,
            )
            wd.screenshot(SCREENSHOTS / "24-model-delete-confirm.png")
            wd.script(
                "document.querySelector('#asr-models .delete-confirm button.danger').click();"
            )
            deleted = wd.wait(
                "deleted notice",
                lambda: next(
                    (
                        n["text"]
                        for n in app.notices()
                        if n["text"].startswith(f"已刪除 {victim}")
                    ),
                    None,
                ),
                timeout=60,
            )
            time.sleep(1)
            after_dirs = sorted(p.name for p in models_dir.iterdir())
            run.check(
                "model delete (isolated cache): confirm text, freed notice, folder removed",
                clicked is True
                and f"要刪除 {victim} 嗎" in confirm
                and "下次開始時會重新下載" in confirm
                and victim in before_dirs
                and victim not in after_dirs,
                {
                    "confirm": confirm,
                    "notice": deleted,
                    "before": before_dirs,
                    "after": after_dirs,
                },
            )
    except Exception:
        logger.exception("v020 languages acceptance aborted")
        run.steps["aborted"] = {"passed": False, "detail": "exception (see log)"}
    user_after = sorted(p.name for p in user_cache.iterdir())
    run.check("user's model cache untouched", user_after == user_before, user_after)
    run.check(
        "no leftover processes",
        not any(leftover_processes().values()),
        leftover_processes(),
    )
    return finish(run)


# --------------------------------------------------------------------------- (c) 只顯示原文


class BlackHole:
    """只收連線、記下請求行、不回應就關閉的本機埠：引擎視為「連不上」，但每個請求都看得到。"""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.requests: list[tuple[float, str]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self.sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                try:
                    head = conn.recv(4096).decode("latin-1", "replace")
                except OSError:
                    head = ""
                self.requests.append((time.monotonic(), head.split("\r\n", 1)[0]))

    def chat_requests(self, since: float = 0.0) -> list[str]:
        return [r for t, r in self.requests if t >= since and "chat/completions" in r]

    def close(self) -> None:
        self._stop.set()
        self.sock.close()


def run_original() -> int:
    run = Run("v020-original")
    hole = BlackHole()
    config_dir = run.dir / "config"
    write_settings(
        config_dir,
        gpu_settings(
            source_language="en",  # 本專案自己寫的英文 TTS 句子
            translation={
                "enabled": False,
                "base_url": f"http://127.0.0.1:{hole.port}/v1",
            },
        ),
    )
    print(f"results: {run.dir} (dead translation server on port {hole.port})")
    player = Player()
    try:
        with AppSession(run, config_dir, "original") as app:
            wd = app.wd
            overlay = app.window(OVERLAY_TITLE_ZH)
            main = app.window("kikitori")
            wd.switch(main)
            app.wait_ready()
            time.sleep(3)
            guide_before = notice_text(app, "translation-guide")
            app.choose_virtual_loopback()
            t_start = time.monotonic()
            wd.click("#start")
            wd.wait(
                "running",
                # `data-state=running` 在載入模型時就成立；「正在聆聽」才是擷取開始
                lambda: wd.text("#status-label") == LISTENING,
                timeout=180,
            )
            clip = tts_clip("tts_en", EN_LINES, EN_VOICE, "en")
            th = play_async(player, clip.label, clip.audio)
            th.join()
            time.sleep(4)
            wd.switch(overlay)
            ov = wd.script(
                "return Array.from(document.querySelectorAll('#lines .line')).map(l => ({"
                " cls: l.className, orig: l.querySelector('.orig').textContent,"
                " tr: l.querySelector('.tr') ? l.querySelector('.tr').textContent : null,"
                " origSize: getComputedStyle(l.querySelector('.orig')).fontSize}));"
            )
            wd.switch(main)
            rows = wd.script(
                "return Array.from(document.querySelectorAll('#transcript li.utt')).map(li =>"
                " ({cls: li.className, orig: li.querySelector('.utt-orig').textContent,"
                " tr: li.querySelector('.utt-tr').textContent}));"
            )
            chats = hole.chat_requests(t_start)
            run.check(
                "original only: session runs, overlay shows promoted original, 0 translation requests",
                bool(ov)
                and all(
                    "is-original-only" in line["cls"] and line["tr"] is None
                    for line in ov
                )
                and len(rows) > 0
                and not chats,
                {
                    "overlay": ov,
                    "rows": len(rows),
                    "requests": hole.requests,
                    "guide_before": guide_before,
                },
            )
            wd.screenshot(SCREENSHOTS / "25-original-only.png")
            t_toggle = time.monotonic()
            wd.click("#show-translation")
            guide = wd.wait(
                "translation guide notice after enabling translation",
                lambda: notice_text(app, "translation-guide"),
                timeout=30,
            )
            time.sleep(3)
            notices = app.notices()
            run.check(
                "toggle translation on mid-session -> guide notice (server unreachable)",
                "連不上翻譯服務" in guide
                and read_settings(config_dir)["translation"]["enabled"] is True
                and wd.text("#status-label") == LISTENING,
                {
                    "guide": guide,
                    "notices": notices,
                    "requests_after_toggle": [
                        r for t, r in hole.requests if t >= t_toggle
                    ],
                },
            )
            wd.screenshot(SCREENSHOTS / "26-translation-guide.png")
            wd.click("#start")
            wd.wait("idle", lambda: wd.text("#status-label") == "待命中", timeout=30)
    except Exception:
        logger.exception("v020 original-only acceptance aborted")
        run.steps["aborted"] = {"passed": False, "detail": "exception (see log)"}
    finally:
        player.close()
        hole.close()
    run.check(
        "no leftover processes",
        not any(leftover_processes().values()),
        leftover_processes(),
    )
    return finish(run)


# --------------------------------------------------------------------------- (d)(e)(h)(i) session

KEYEVENTF_KEYUP = 0x2
VK_CONTROL, VK_MENU = 0x11, 0x12


def send_hotkey(letter: str) -> None:
    """以 keybd_event 送出 Ctrl+Alt+<letter>（全域快速鍵由 RegisterHotKey 接收）。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    vk = ord(letter.upper())
    for key in (VK_CONTROL, VK_MENU, vk):
        user32.keybd_event(key, 0, 0, 0)
        time.sleep(0.03)
    for key in (vk, VK_MENU, VK_CONTROL):
        user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)


SESSION_JS = """
const m = document.getElementById('metrics');
const rows = Array.from(document.querySelectorAll('#transcript li.utt'));
return {
  state: document.getElementById('start').dataset.state,
  metricsHidden: m.hidden,
  level: document.getElementById('level-meter').getAttribute('aria-valuenow'),
  levelText: document.getElementById('level-meter').getAttribute('aria-valuetext'),
  fill: document.getElementById('level-fill').style.cssText,
  latency: document.getElementById('latency').textContent,
  partialSplit: rows.filter(li => li.classList.contains('is-partial')).map(li => ({
    stable: (li.querySelector('.utt-stable') || {}).textContent || '',
    unstable: (li.querySelector('.utt-unstable') || {}).textContent || '',
    unstableOpacity: li.querySelector('.utt-unstable') ? getComputedStyle(li.querySelector('.utt-unstable')).opacity : null,
  })),
  rows: rows.length,
  finals: rows.filter(li => !li.classList.contains('is-partial')).length,
  showTranslation: document.getElementById('show-translation').checked,
};
"""


def read_obs() -> str | None:
    try:
        return OBS_FILE.read_text("utf-8")
    except OSError:
        return None


def run_session() -> int:
    run = Run("v020-session")
    config_dir = run.dir / "config"
    write_settings(
        config_dir,
        gpu_settings(
            source_language="en",  # 本專案自己寫的英文 TTS 句子
            ui={"auto_save_transcripts": True},
            subtitle={"obs_output": "both", "idle_clear_secs": 30},
        ),
    )
    out_existed = OUTPUT_ROOT.exists()
    transcripts_before = set(tree_files(TRANSCRIPTS))
    created: list[Path] = []
    print(f"results: {run.dir}")
    player = Player()
    try:
        with AppSession(run, config_dir, "session") as app:
            wd = app.wd
            overlay = app.window(OVERLAY_TITLE_ZH)
            main = app.window("kikitori")
            wd.switch(main)
            app.wait_ready()
            shortcut_note = wd.text("#shortcut-note")
            app.choose_virtual_loopback()
            wd.click("#start")
            wd.wait(
                "running",
                lambda: wd.text("#status-label") == LISTENING,
                timeout=180,
            )
            clip = tts_clip("tts_en", EN_LINES, EN_VOICE, "en")
            obs_seen: list[str] = []
            samples: list[dict[str, Any]] = []
            overlay_split: list[Any] = []
            shot = False
            for _ in range(2):
                th = play_async(player, clip.label, clip.audio)
                while th.is_alive():
                    s = wd.script(SESSION_JS)
                    samples.append(s)
                    if (
                        s["partialSplit"]
                        and any(
                            p["stable"] and p["unstable"] for p in s["partialSplit"]
                        )
                        and not shot
                    ):
                        wd.screenshot(SCREENSHOTS / "27-stable-prefix-and-meter.png")
                        shot = True
                    obs = read_obs()
                    if obs and (not obs_seen or obs_seen[-1] != obs):
                        obs_seen.append(obs)
                    wd.switch(overlay)
                    overlay_split.extend(
                        wd.script(
                            "return Array.from(document.querySelectorAll('#lines .line.is-partial')).map(l => ({"
                            "stable: (l.querySelector('.stable')||{}).textContent||'',"
                            "unstable: (l.querySelector('.unstable')||{}).textContent||''}));"
                        )
                    )
                    wd.switch(main)
                    time.sleep(0.25)
            time.sleep(5)
            final = wd.script(SESSION_JS)
            levels = [
                float(s["level"]) for s in samples if s["level"] not in (None, "")
            ]
            split = [p for s in samples for p in s["partialSplit"]]
            run.check(
                "(h) stable prefix rendered (stable + faded unstable spans) in transcript and overlay",
                any(p["stable"] and p["unstable"] for p in split)
                and any(p["unstableOpacity"] not in (None, "1") for p in split)
                and any(p["stable"] for p in overlay_split),
                {
                    "transcript_examples": [
                        p for p in split if p["stable"] and p["unstable"]
                    ][:3],
                    "overlay_examples": [p for p in overlay_split if p["stable"]][:3],
                },
            )
            run.check(
                "(h) level meter and latency shown while running",
                sum(1 for s in samples[4:] if s["metricsHidden"]) == 0
                and levels
                and max(levels) > -60
                and re.match(r"延遲 [\d.]+ 秒", final["latency"]) is not None,
                {
                    "level_range": [min(levels), max(levels)] if levels else None,
                    "hidden_samples": [
                        i for i, s in enumerate(samples) if s["metricsHidden"]
                    ],
                    "samples": len(samples),
                    "latency": final["latency"],
                    "fill_example": samples[-1]["fill"] if samples else None,
                },
            )
            run.check(
                "(e) OBS text file updated during the session",
                len(obs_seen) >= 2 and all(s.strip() for s in obs_seen),
                {
                    "path": str(OBS_FILE),
                    "distinct_contents": len(obs_seen),
                    "last": obs_seen[-1:] if obs_seen else None,
                },
            )
            # (i) 全域快速鍵
            wd.switch(overlay)
            ov_before = wd.script(
                "return document.querySelectorAll('#lines .line').length;"
            )
            wd.switch(main)
            send_hotkey("U")
            time.sleep(1.0)
            wd.switch(overlay)
            ov_after = wd.script(
                "return document.querySelectorAll('#lines .line').length;"
            )
            wd.switch(main)
            obs_after_clear = read_obs()
            run.check(
                "(i) Ctrl+Alt+U clears the overlay and the OBS file (transcript kept)",
                ov_before > 0
                and ov_after == 0
                and obs_after_clear == ""
                and wd.script(SESSION_JS)["rows"] == final["rows"],
                {
                    "overlay_before": ov_before,
                    "overlay_after": ov_after,
                    "obs": obs_after_clear,
                    "shortcut_note": shortcut_note,
                },
            )
            send_hotkey("J")
            off = wd.wait(
                "translation toggled off",
                lambda: (
                    (not wd.script(SESSION_JS)["showTranslation"])
                    and read_settings(config_dir)["translation"]["enabled"] is False
                ),
                timeout=10,
            )
            send_hotkey("J")
            on = wd.wait(
                "translation toggled on",
                lambda: (
                    wd.script(SESSION_JS)["showTranslation"]
                    and read_settings(config_dir)["translation"]["enabled"] is True
                ),
                timeout=10,
            )
            run.check(
                "(i) Ctrl+Alt+J toggles show-translation (off, then on)",
                bool(off and on),
                None,
            )
            # (d) 匯出四種格式
            wd.script("document.getElementById('export-menu').open = true;")
            exports: dict[str, Any] = {}
            for fmt in ("srt", "vtt", "txt", "csv"):
                prev = notice_text(app, "export")
                wd.click(f'#export-formats button[data-format="{fmt}"]')
                text = wd.wait(
                    f"export {fmt}",
                    lambda prev=prev, fmt=fmt: (
                        (t := notice_text(app, "export"))
                        and t != prev
                        and t.endswith(f".{fmt}")
                        and t
                    ),
                    timeout=20,
                )
                path = path_from_notice(text)
                created.append(path)
                body = path.read_bytes()
                exports[fmt] = {
                    "path": str(path),
                    "bytes": len(body),
                    "head": body[:160].decode("utf-8", "replace"),
                }
                wd.script("document.getElementById('export-menu').open = true;")
            srt = Path(exports["srt"]["path"]).read_bytes().decode("utf-8")
            vtt = Path(exports["vtt"]["path"]).read_text("utf-8")
            csv = Path(exports["csv"]["path"]).read_bytes()
            run.check(
                "(d) export SRT/VTT/TXT/CSV into Documents\\kikitori\\transcripts",
                all(Path(e["path"]).parent == TRANSCRIPTS for e in exports.values())
                and "-->" in srt
                and "\r\n" in srt
                and vtt.startswith("WEBVTT")
                and csv.startswith(b"\xef\xbb\xbf")
                and b"original" in csv.splitlines()[0],
                exports,
            )
            wd.screenshot(SCREENSHOTS / "28-export-menu.png")
            wd.script("document.getElementById('export-menu').open = false;")
            # 停止 → 自動存檔、OBS 清空
            wd.click("#start")
            auto = wd.wait(
                "auto-save notice",
                lambda: next(
                    (
                        n["text"]
                        for n in app.notices()
                        if n["text"].startswith("已自動儲存")
                    ),
                    None,
                ),
                timeout=30,
            )
            time.sleep(1.5)
            auto_srt = path_from_notice(auto)
            auto_txt = auto_srt.with_suffix(".txt")
            created.extend([auto_srt, auto_txt])
            run.check(
                "(d) auto-save on stop writes SRT + TXT",
                auto_srt.is_file() and auto_txt.is_file(),
                {"notice": auto, "files": [str(auto_srt), str(auto_txt)]},
            )
            obs_end = read_obs()
            run.check(
                "(e) OBS text file cleared after the session ends",
                obs_end == "",
                {"content": obs_end},
            )
            wd.wait("idle", lambda: wd.text("#status-label") == "待命中", timeout=30)
            hidden = wd.script("return document.getElementById('metrics').hidden;")
            run.check("(h) meter hidden after stop", hidden is True, hidden)
    except Exception:
        logger.exception("v020 session acceptance aborted")
        run.steps["aborted"] = {"passed": False, "detail": "exception (see log)"}
    finally:
        player.close()
        new_files = set(tree_files(TRANSCRIPTS)) - transcripts_before
        created.extend(
            TRANSCRIPTS / f for f in new_files if (TRANSCRIPTS / f) not in created
        )
        cleanup_outputs(run, created, out_existed)
    run.check(
        "no leftover processes",
        not any(leftover_processes().values()),
        leftover_processes(),
    )
    return finish(run)


BATCH = {
    "v020-ui": run_ui,
    "v020-languages": run_languages,
    "v020-original": run_original,
    "v020-session": run_session,
}
