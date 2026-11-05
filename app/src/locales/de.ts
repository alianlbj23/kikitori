/**
 * German UI dictionary (Sie-form).
 *
 * Role: one interface locale, typed as `Messages`, so it must have exactly the
 * base (zh-TW) keys and the plural categories German uses (one/other).
 */

import type { Messages } from "./types";

export const de: Messages = {
  // ------------------------------------------------------------ common
  "common.unknown": "Unbekannt",
  "common.unknownReason": "unbekannter Grund",
  "common.unknownCause": "unbekannte Ursache",
  "common.auto": "Automatisch",
  "common.loading": "Wird geladen",
  "common.saving": "Wird gespeichert",
  "common.redownload": "Erneut herunterladen",
  "common.recommendedFor": "Empfohlen: {roles}",
  "common.parenthetical": "{text} ({note})",
  "common.withReason": "{context}: {reason}",
  "common.listSeparator": ", ",
  "common.clauseSeparator": ", ",
  "common.strongSeparator": "; ",
  "common.altSeparator": " / ",
  "common.sentenceEnd": ".",
  "common.approxGb": "etwa {gb} GB",

  // ------------------------------------------------------------ rail
  "rail.mode": "Modus",
  "rail.device": "Audioquelle",
  "mode.media.name": "Videoübersetzung",
  "mode.media.note": "{source} nach {target}",
  "mode.media.noteAuto": "Jede erkannte Sprache nach {target}",
  "mode.media.noteOriginal": "{source} (nur Original)",
  "mode.interpret.noteOriginal": "{own} ⇄ {partner} (nur Original)",
  "mode.interpret.name": "Live-Dolmetschen",
  "mode.interpret.note": "{own} ⇄ {partner}",
  "device.hint.media": "Die Videoübersetzung nimmt den Ton auf, den Ihr Computer gerade wiedergibt.",
  "device.hint.interpret": "Das Live-Dolmetschen verwendet das Mikrofon.",
  "device.hint.missing":
    "Das zuvor gewählte Gerät ist nicht verfügbar; stattdessen wird der Systemstandard verwendet.",
  "device.defaultNamed": "Standard: {name}",
  "device.systemDefault": "Systemstandard",
  "start.start": "Starten",
  "start.stop": "Stoppen",
  "nav.label": "Seiten",
  "nav.transcript": "Transkript",
  "nav.models": "Modelle",
  "nav.settings": "Einstellungen",
  "engine.restart": "Engine neu starten",
  "engine.failedNotice": "Die Engine konnte nicht starten: {reason}",
  "engine.log": "Hinweis der Engine: {message}",
  // ------------------------------------------------------------ engine message codes (hardware.md; params localized in lib/messages.ts)
  "engine.asr_gpu":
    "{gpu_name} ({vram_mb} MiB VRAM): {fast_model} für Live-Untertitel, {final_model} für endgültige Untertitel ({compute_type})",
  "engine.asr_gpu_int8_saves_vram":
    "int8_float16 halbiert den VRAM-Bedarf der Spracherkennung und lässt mehr für das Übersetzungsmodell übrig",
  "engine.asr_final_no_speech_unreliable":
    "{final_model} kann Sprache nicht von Stille unterscheiden (no_speech); stattdessen filtern das no_speech von {fast_model} und der Sprachaktivitätsdetektor alles heraus, was keine Sprache ist",
  "engine.asr_large_v3_remote_translation":
    "Die Übersetzung läuft nicht auf diesem Computer, daher verwenden die endgültigen Untertitel {final_model} (ähnliche Qualität, zuverlässiges no_speech, etwa 0,33 s pro Satz)",
  "engine.asr_apple_silicon":
    "Apple Silicon: Unter macOS läuft die Spracherkennung auf der CPU; {fast_model} für Live-Untertitel, {final_model} für endgültige Untertitel",
  "engine.asr_apple_silicon_untested":
    "Die Latenz auf Apple Silicon wurde noch nicht gemessen; es werden dieselben Modelle wie bei einer CPU mit 16 GB RAM oder mehr verwendet",
  "engine.asr_cpu":
    "Die Spracherkennung läuft auf der CPU (16 GB RAM oder mehr): {fast_model} für Live-Untertitel, {final_model} für endgültige Untertitel",
  "engine.asr_cpu_large_models_slow":
    "medium und größere Modelle brauchen auf einer CPU 3,6 s oder mehr pro Satz – zu langsam für Live-Untertitel",
  "engine.asr_cpu_low":
    "Leichte Modelle halten Latenz und Speicherbedarf gering: {fast_model} für Live-Untertitel, {final_model} für endgültige Untertitel",
  "engine.cpu_low_ram": "{ram_gb} GB RAM sind weniger als 16 GB",
  "engine.cpu_few_cores": "{physical_cores} physische CPU-Kerne (höchstens {max_cores})",
  "engine.cpu_info_incomplete": "Die Hardwareinformationen sind unvollständig",
  "engine.multiple_gpus":
    "{gpu_count} NVIDIA-GPUs gefunden; verwendet wird {gpu_name}, die GPU mit dem meisten VRAM",
  "engine.cuda_platform_unsupported":
    "CTranslate2 hat für diese Plattform keinen CUDA-Build (nur Windows und Linux x86-64); die Spracherkennung verwendet die CPU",
  "engine.cuda_driver_too_old":
    "Der NVIDIA-Treiber {driver_version} ist zu alt (CUDA 12 benötigt {min_driver} oder neuer); die Spracherkennung verwendet die CPU, bis der Treiber aktualisiert ist",
  "engine.cuda_compute_capability_unknown":
    "Die Compute Capability der GPU kann nicht gelesen werden (der Treiber ist möglicherweise zu alt); die Spracherkennung verwendet die CPU",
  "engine.cuda_compute_capability_unsupported":
    "{gpu_name} (Compute Capability {compute_capability}) unterstützt die float16/int8-Beschleunigung von CTranslate2 nicht; die Spracherkennung verwendet die CPU",
  "engine.gpu_runtime_missing":
    "cuBLAS ist nicht installiert: Die GPU-Beschleunigung erfordert den Download der GPU-Laufzeitumgebung (etwa 570 MB); bis dahin verwendet die Spracherkennung die CPU",
  "engine.asr_vram_insufficient":
    "Nicht genug VRAM für die Spracherkennungsmodelle (etwa {need_mb} MiB benötigt); die Spracherkennung verwendet die CPU",
  "engine.memory_unknown":
    "Die Speichergröße kann nicht gelesen werden; es werden die leichtesten Spracherkennungsmodelle verwendet",
  "engine.asr_low_quality_model": "{model} macht deutlich mehr Erkennungsfehler",
  "engine.asr_language_quality_low":
    "{model} erkennt {language} schlecht (Fehlerrate über 20 % in öffentlichen Benchmarks)",
  "engine.translation_remote":
    "Der Übersetzungsserver befindet sich nicht auf diesem Computer ({base_url}); wählen Sie eines seiner Modelle in der benutzerdefinierten Kombination auf der Seite „Modelle“",
  "engine.translation_cpu":
    "Keine nutzbare GPU: Das Übersetzungsmodell läuft auf der CPU, daher wird das kleinste Modell gewählt (CPU-Geschwindigkeit nicht gemessen)",
  "engine.translation_budget_vram":
    "Nach der Spracherkennung und einer Reserve von 1 GB bleiben etwa {budget_gb} GB VRAM für das Übersetzungsmodell",
  "engine.translation_budget_unified":
    "Nach der Spracherkennung und der Systemreserve bleiben etwa {budget_gb} GB Unified Memory für das Übersetzungsmodell",
  "engine.translation_same_model":
    "Live- und endgültige Übersetzung verwenden dasselbe Modell: Jeder Satz dauert auf einer GPU unter 0,3 s, ein Modellwechsel in Ollama dagegen 1,5–18 s",
  "engine.translation_different_models": "Live- und endgültige Übersetzung verwenden unterschiedliche Modelle",
  "engine.translation_better_model_available":
    "{model} wurde aus den installierten Modellen gewählt; mit {better_model} erhalten Sie eine bessere Qualität",
  "engine.translation_not_needed":
    "{source_language} und {target_language} sind dieselbe gesprochene Sprache: Es ist kein Übersetzungsmodell nötig, nur eine Schriftumwandlung",
  "engine.source_language_auto":
    "Die Hörsprache wird automatisch erkannt: Das Übersetzungsmodell wird so gewählt, dass es jede Sprache nach {target_language} übersetzt",
  "engine.translation_server_unreachable":
    "Keine Verbindung zum Übersetzungsserver {base_url} (läuft Ollama?); installierte Modelle können nicht geprüft werden",
  "engine.translation_model_list_unavailable": "Die Modellliste des Übersetzungsservers ist nicht abrufbar: {error}",
  "engine.low_ram_for_translation":
    "Weniger als 8 GB RAM: Übersetzungsmodell und Spracherkennung passen möglicherweise nicht gleichzeitig in den Speicher",
  "engine.translation_model_not_installed": "Das empfohlene Übersetzungsmodell {model} ist noch nicht installiert",
  "engine.translation_model_exceeds_budget":
    "{model} benötigt etwa {model_gb} GB, mehr als die verfügbaren {budget_gb} GB; Ollama führt einen Teil davon auf der CPU aus, was langsamer ist",
  "engine.translation_unsupported":
    "Kein empfohlenes Übersetzungsmodell unterstützt {source_language} → {target_language}",
  "engine.combination_not_object": "Die Modellkombination muss ein Objekt sein",
  "engine.asr_path_not_object": "{path} muss ein Objekt sein",
  "engine.asr_model_unknown": "{path}: Unbekanntes Spracherkennungsmodell {model} (verfügbar: {available})",
  "engine.asr_device_invalid": "{path}: Das Gerät muss „Automatisch“, CPU oder CUDA sein",
  "engine.asr_compute_type_not_string": "{path}: Die Präzision muss Text oder leer sein",
  "engine.asr_cpu_threads_invalid": "{path}: Die CPU-Threads müssen eine ganze Zahl von 0 bis 256 sein",
  "engine.asr_cuda_no_gpu": "{path}: CUDA ist ausgewählt, aber es wurde keine NVIDIA-GPU gefunden",
  "engine.asr_cuda_platform_unsupported":
    "{path}: CUDA ist ausgewählt, aber CTranslate2 hat für diese Plattform keinen CUDA-Build (nur Windows und Linux x86-64)",
  "engine.asr_cuda_driver_too_old":
    "{path}: CUDA ist ausgewählt, aber der NVIDIA-Treiber {driver_version} ist zu alt (CUDA 12 benötigt {min_driver} oder neuer)",
  "engine.asr_cuda_compute_capability_unknown":
    "{path}: CUDA ist ausgewählt, aber die Compute Capability der GPU ist unbekannt (der Treiber ist möglicherweise zu alt)",
  "engine.asr_cuda_compute_capability_unsupported":
    "{path}: CUDA ist ausgewählt, aber {gpu_name} (Compute Capability {compute_capability}) unterstützt die float16/int8-Beschleunigung von CTranslate2 nicht",
  "engine.asr_cuda_cublas_missing":
    "{path}: CUDA ist ausgewählt, aber cuBLAS ist nicht installiert; laden Sie zuerst die GPU-Laufzeitumgebung herunter",
  "engine.asr_compute_type_unknown": "{path}: Unbekannte Präzision {compute_type} (verfügbar: {available})",
  "engine.translation_model_name_invalid":
    "{field} muss ein Modellname mit 1–{max_chars} Zeichen oder leer sein",
  "engine.asr_auto_uses_cpu":
    "{path}: „Automatisch“ verwendet die CPU (die GPU kann die Spracherkennung derzeit nicht ausführen)",
  "engine.asr_compute_type_cpu_fallback":
    "{path}: Die CPU unterstützt {compute_type} nicht; CTranslate2 verwendet eine andere Präzision (meist float32, langsamer)",
  "engine.asr_compute_type_gpu_fallback":
    "{path}: Compute Capability {compute_capability} unterstützt {compute_type} nicht; CTranslate2 verwendet float32 oder int8_float32 (langsamer, mehr VRAM)",
  "engine.asr_model_slow_on_cpu":
    "{path}: {model} braucht auf einer CPU 3,6 s oder mehr pro Satz; die Untertitel hängen deutlich hinterher",
  "engine.asr_model_not_downloaded":
    "{path}: Das Spracherkennungsmodell {model} ist noch nicht heruntergeladen; es wird vor dem Start heruntergeladen",
  "engine.asr_model_language_unsupported": "{path}: {model} kann {language} nicht erkennen",
  "engine.asr_fast_larger_than_final":
    "Das Live-Modell {fast_model} ist größer als das endgültige Modell {final_model}: Live-Untertitel können langsamer sein als die endgültigen",
  "engine.asr_no_speech_unavailable":
    "Weder {fast_model} noch {final_model} kann mit no_speech herausfiltern, was keine Sprache ist; Gesang und Geräusche werden eher transkribiert",
  "engine.translation_model_not_on_server": "Der Übersetzungsserver hat {model} nicht",
  "engine.translation_model_language_unsupported":
    "{model} unterstützt {source_language} → {target_language} nicht",
  "engine.translation_license_non_commercial": "Die Lizenz von {model} ({license}) verbietet die kommerzielle Nutzung",
  "engine.translation_license_region_restricted": "Die Lizenz von {model} ({license}) gilt nicht in: {regions}",
  "engine.translation_models_differ":
    "Live- und endgültige Übersetzung verwenden unterschiedliche Modelle: Das lokale Ollama braucht bei jedem Modellwechsel 1,5–18 s",
  "engine.vram_overflow":
    "Spracherkennung und Übersetzung benötigen etwa {need_mb} MiB VRAM, mehr als die auf {gpu_name} verfügbaren {available_mb} MiB; sie können langsam sein oder nicht geladen werden",
  "engine.asr_ram_over_half": "Die Spracherkennungsmodelle auf der CPU belegen mehr als die Hälfte des RAM",
  "engine.translation_disabled":
    "Die Übersetzung ist ausgeschaltet: Nur der Originaltext wird angezeigt, und es ist kein Übersetzungsmodell nötig",
  "engineParam.fast_asr": "Erkennung für Live-Untertitel",
  "engineParam.final_asr": "Erkennung für endgültige Untertitel",
  "engineParam.translation_fast": "Das Live-Übersetzungsmodell",
  "engineParam.translation_final": "Das endgültige Übersetzungsmodell",

  // ------------------------------------------------------------ status
  "status.starting": "Engine wird gestartet",
  "status.restarting": "Die Engine wurde unerwartet beendet; Neustart läuft",
  "status.failed": "Die Engine konnte nicht starten",
  "status.stopped": "Engine gestoppt",
  "status.loading": "Modelle werden geladen",
  "status.running": "Hört zu",
  "status.error": "Nach einem Fehler gestoppt",
  "status.idle": "Bereit",
  "status.code.model_not_downloaded":
    "Das Modell ist noch nicht heruntergeladen. Laden Sie es auf der Seite „Modelle“ herunter und starten Sie dann erneut.",
  "status.code.unknown_model":
    "Das Modell in Ihren Einstellungen existiert nicht. Wählen Sie auf der Seite „Modelle“ ein anderes.",
  "status.code.device_not_found": "Das ausgewählte Audiogerät wurde nicht gefunden. Wählen Sie erneut ein Gerät.",
  "status.code.permission_denied":
    "kikitori darf das Mikrofon nicht verwenden oder keinen Ton aufnehmen. Erlauben Sie dies in den Systemeinstellungen.",
  "status.code.device_lost":
    "Die Verbindung zum Audiogerät wurde getrennt. Prüfen Sie die Verbindung und starten Sie erneut.",
  "status.code.capture_unavailable": "Dieser Computer kann diese Art von Audioquelle nicht aufnehmen.",
  "status.code.capture_error": "Bei der Audioaufnahme ist ein Fehler aufgetreten.",
  "status.code.pipeline_error": "Bei der Sprachverarbeitung ist ein Fehler aufgetreten.",
  "status.code.load_error": "Das Modell konnte nicht geladen werden.",
  "status.code.translation_model_missing": "Der Übersetzungsserver hat das Übersetzungsmodell noch nicht.",
  "status.code.translation_server_auth_failed":
    "Der Übersetzungsserver hat die Anfrage abgelehnt – prüfen Sie den API-Schlüssel.",
  "status.errorDetail": "Ein Fehler ist aufgetreten: {detail}",
  "status.errorUnknown": "Ein unbekannter Fehler ist aufgetreten.",

  // ------------------------------------------------------------ notices
  "notice.close": "Schließen",
  "notice.closeLabel": "Diese Meldung schließen",
  "notice.settingsFieldsUnreadable": {
    one: "{count} Feld der Einstellungsdatei konnte nicht gelesen werden; vorerst wird der Standardwert verwendet: {fields}",
    other:
      "{count} Felder der Einstellungsdatei konnten nicht gelesen werden; vorerst werden die Standardwerte verwendet: {fields}",
  },
  "notice.initFailed": "Das Fenster konnte nicht initialisiert werden: {reason}",

  // ------------------------------------------------------------ failure contexts
  "error.saveMode": "Der Modus konnte nicht gespeichert werden",
  "error.saveDevice": "Die Audioquelle konnte nicht gespeichert werden",
  "error.start": "Starten fehlgeschlagen",
  "error.stop": "Stoppen fehlgeschlagen",
  "error.showOverlay": "Das Untertitelfenster konnte nicht angezeigt werden",
  "error.hideOverlay": "Das Untertitelfenster konnte nicht ausgeblendet werden",
  "error.toggleClickThrough": "„Klicks durchlassen“ konnte nicht umgeschaltet werden",
  "error.applyRecommendation": "Die empfohlene Konfiguration konnte nicht übernommen werden",
  "error.readSettings": "Die Einstellungen konnten nicht gelesen werden",
  "error.saveDismissal": "„Nicht mehr erinnern“ konnte nicht gespeichert werden",
  "error.saveUiLocale": "Die Oberflächensprache konnte nicht gespeichert werden",
  "error.setOverlayLocale": "Die Sprache des Untertitelfensters konnte nicht gewechselt werden",
  "error.readModels": "Die Modellliste konnte nicht gelesen werden",
  "error.detectHardware": "Die Hardware konnte nicht erkannt werden",
  "error.downloadModel": "{model} konnte nicht heruntergeladen werden",
  "error.downloadGpuRuntime": "Die GPU-Beschleunigungskomponente konnte nicht heruntergeladen werden",
  "error.openEula":
    "Die Lizenzbedingungen konnten nicht geöffnet werden; kopieren Sie die URL in Ihren Browser",
  "error.applyStyle": "Das Aussehen der Untertitel konnte nicht übernommen werden",
  "error.openLicenses":
    "Der Lizenzordner konnte nicht geöffnet werden; öffnen Sie ihn über den oben angegebenen Pfad",
  "error.listDevices": "Die Audiogeräte konnten nicht aufgelistet werden",
  "error.restartEngine": "Die Engine konnte nicht neu gestartet werden",
  "error.readOverlayState": "Der Zustand des Untertitelfensters konnte nicht gelesen werden",
  "error.readBridge": "Der Zustand der Engine konnte nicht gelesen werden",
  "error.readLanguages": "Die Sprachliste konnte nicht gelesen werden",
  "error.setTranslation": "„Übersetzung anzeigen“ konnte nicht umgeschaltet werden",
  "error.previewLanguages": "Die empfohlene Kombination konnte nicht abgerufen werden",

  // ------------------------------------------------------------ engine error codes
  "error.code.invalid_settings": "Ungültige Einstellung",
  "error.code.settings_io_error":
    "Die Einstellungsdatei konnte nicht gespeichert werden; prüfen Sie Speicherplatz und Zugriffsrechte",
  "error.code.session_active": "Läuft bereits; stoppen Sie die Sitzung zuerst",
  "error.code.device_not_found": "Das ausgewählte Audiogerät wurde nicht gefunden; wählen Sie es erneut",
  "error.code.capture_unavailable": "Dieser Computer kann diese Art von Audioquelle nicht aufnehmen",
  "error.code.capture_error": "Bei der Audioaufnahme ist ein Fehler aufgetreten",
  "error.code.unknown_model": "Die Engine kennt dieses Modell nicht",
  "error.code.download_in_progress": "Dieses Element wird bereits heruntergeladen; warten Sie, bis der Download fertig ist",
  "error.code.license_not_accepted":
    "Stimmen Sie dem NVIDIA CUDA EULA zu, bevor Sie die GPU-Beschleunigungskomponente herunterladen",
  "error.code.gpu_runtime_unsupported":
    "Diese Plattform unterstützt die GPU-Beschleunigungskomponente nicht (nur Windows und Linux auf x86-64)",
  "error.code.pull_unsupported":
    "Der Übersetzungsserver ist nicht Ollama, daher kann kikitori keine Übersetzungsmodelle für Sie herunterladen",
  "error.code.translation_server_unreachable": "Keine Verbindung zum Übersetzungsserver",
  "error.code.translation_server_auth_failed":
    "Der Übersetzungsserver hat die Anfrage abgelehnt – prüfen Sie den API-Schlüssel",
  "error.code.unknown_method":
    "Die Engine-Version passt nicht zur App; dieser Vorgang wird nicht unterstützt",
  "error.code.invalid_params": "An die Engine wurden ungültige Parameter gesendet",
  "error.code.invalid_request": "An die Engine wurde eine fehlerhafte Anfrage gesendet",
  "error.code.internal_error": "Interner Fehler der Engine",
  "error.code.other": "Die Engine hat einen Fehler gemeldet ({code})",
  "error.engineSentence": "{head}.",
  "error.engineDetail": "{head}: {message}",
  "error.timeout": {
    one: "Die Engine hat nicht innerhalb von {count} Sekunde geantwortet. Versuchen Sie es später erneut oder starten Sie die Engine neu.",
    other:
      "Die Engine hat nicht innerhalb von {count} Sekunden geantwortet. Versuchen Sie es später erneut oder starten Sie die Engine neu.",
  },
  "error.disconnected":
    "Die Engine läuft nicht. Warten Sie, bis sie neu gestartet ist, oder klicken Sie im Statusbereich auf „Engine neu starten“.",
  "error.invalidInput": "Ungültiger Wert: {message}",
  "error.generic": "Ein Fehler ist aufgetreten: {message}",

  // ------------------------------------------------------------ transcript
  "transcript.title": "Transkript",
  "transcript.lede":
    "Live-Untertitel erscheinen zunächst blass und wechseln zur endgültigen Übersetzung, sobald der Satz abgeschlossen ist.",
  "transcript.overlayVisible": "Untertitelfenster anzeigen",
  "transcript.clickThrough": "Klicks durchlassen",
  "transcript.clear": "Leeren",
  "transcript.showTranslation": "Übersetzung anzeigen",
  "transcript.noTranslation": "(keine Übersetzung)",
  "transcript.translating": "Wird übersetzt",
  "transcript.emptyTitle": "Noch keine Untertitel",
  "transcript.emptyBody":
    "Wählen Sie einen Modus und eine Audioquelle und klicken Sie dann auf „Starten“. Jeder gehörte Satz wird hier aufgeführt, oben das Original und darunter die Übersetzung.",
  "transcript.langTitle": "{source} nach {target}",
  "transcript.shortcutCtrl": "Strg (⌘ unter macOS)",
  "transcript.shortcutOn":
    "Wenn „Klicks durchlassen“ aktiv ist, gehen Klicks durch das Untertitelfenster hindurch. Mit {keys} können Sie es jederzeit umschalten.",
  "transcript.shortcutTaken":
    "Das Tastenkürzel {keys} wird von einer anderen App verwendet; schalten Sie „Klicks durchlassen“ mit dem Schalter hier um.",

  // ------------------------------------------------------------ models page
  "models.title": "Modelle",
  "models.lede":
    "Die Spracherkennung läuft auf diesem Computer; die Übersetzung geht an den Dienst, der auf der Seite „Einstellungen“ festgelegt ist.",
  "models.refresh": "Erneut erkennen",

  "hw.title": "Dieser Computer",
  "hw.status": "Status",
  "hw.detecting": "Wird erkannt",
  "hw.os": "Betriebssystem",
  "hw.cpu": "Prozessor",
  "hw.memory": "Arbeitsspeicher",
  "hw.gpu": "Grafikkarte",
  "hw.cublas": "cuBLAS (GPU-Erkennung)",
  "hw.server": "Übersetzungsdienst",
  "hw.cpuValue": "{model}, {cores}",
  "hw.cores": "{physical} Kerne, {logical} Threads",
  "hw.threads": { one: "{count} Thread", other: "{count} Threads" },
  "hw.coresUnknown": "Kernanzahl unbekannt",
  "hw.appleSilicon": "Apple Silicon (Speicher mit dem Prozessor geteilt)",
  "hw.gpuVram": "{size} Grafikspeicher",
  "hw.gpuDriver": "Treiber {version}",
  "hw.gpuCompute": "Compute Capability {capability}",
  "hw.noNvidia": "Keine NVIDIA-Grafikkarte erkannt",
  "hw.cublasNotUsed": "Auf dieser Plattform nicht verwendet",
  "hw.cublasAvailable": "Verfügbar ({source})",
  "hw.cublasSourceUnknown": "unbekannte Quelle",
  "hw.cublasMissing": "Nicht installiert",
  "hw.cublasSource.runtime": "von kikitori heruntergeladene GPU-Beschleunigungskomponente",
  "hw.cublasSource.configured": "in der benutzerdefinierten Konfiguration festgelegter Ordner",
  "hw.cublasSource.system": "CUDA-Installation des Systems",
  "hw.serverNotDetected": "Nicht erkannt",
  "hw.serverLocal": "lokal",
  "hw.serverRemote": "entfernt",
  "hw.serverModelsUnknown": "Modellliste unbekannt",
  "hw.serverModelCount": { one: "{count} Modell", other: "{count} Modelle" },
  "hw.serverReachable": "{url} ({where}, {models})",
  "hw.serverUnreachable": "{url} (nicht erreichbar)",
  "hw.serverUnreachableError": "{url} (nicht erreichbar: {error})",
  "hw.memoryAvailable": "{total} ({available} verfügbar)",
  "hw.incomplete": "Erkennung unvollständig: {error}",

  "rec.title": "Empfohlene Konfiguration",
  "rec.pending": "Empfehlungen erscheinen hier, sobald die Erkennung abgeschlossen ist.",
  "rec.tier": "Hardwarestufe",
  "rec.fastAsr": "Erkennung für Live-Untertitel",
  "rec.finalAsr": "Erkennung für die endgültige Übersetzung",
  "rec.fastTranslation": "Übersetzung der Live-Untertitel",
  "rec.finalTranslation": "Endgültige Übersetzung",
  "rec.budget": "Für die Übersetzung verfügbarer Speicher",
  "rec.notLocal":
    "Keine Empfehlung (der Übersetzungsdienst ist nicht lokal); wählen Sie eines in der benutzerdefinierten Konfiguration",
  "rec.gpuRuntimeMissing":
    "Sobald Sie unten die GPU-Beschleunigungskomponente heruntergeladen haben, verwendet die Empfehlung die Grafikkarte.",
  "rec.apply": "Empfohlene Konfiguration übernehmen",
  "rec.applyNote":
    "Setzt die Erkennungsmodelle, das Gerät, die Präzision, die Threads und die Übersetzungsmodelle.",
  "rec.applied":
    "Empfohlene Konfiguration übernommen: Live-Untertitel {fast}, endgültige Übersetzung {final} ({device}).",
  "rec.appliedWhileRunning":
    "Empfohlene Konfiguration übernommen: Live-Untertitel {fast}, endgültige Übersetzung {final} ({device}). Sie wird beim nächsten Start wirksam.",
  "tier.nvidia_8gb": "NVIDIA-Grafikkarte (8 GB oder mehr)",
  "tier.nvidia_4gb": "NVIDIA-Grafikkarte (4–8 GB)",
  "tier.nvidia_lt4gb": "NVIDIA-Grafikkarte (unter 4 GB)",
  "tier.cpu": "CPU (16 GB Arbeitsspeicher oder mehr)",
  "tier.cpu_low": "CPU (leichte Konfiguration)",
  "tier.apple_silicon": "Apple Silicon (CPU)",
  "threads.auto": "Threads automatisch",
  "threads.count": { one: "{count} Thread", other: "{count} Threads" },
  "computeType.auto": "Präzision automatisch",

  "machine.gpu": "{name}, {gb} GB",
  "machine.ram": "{gb} GB Arbeitsspeicher",
  "machine.named": "{name}, {ram}",
  "machine.thisCpu": "der Prozessor dieses Computers",
  "machine.thisCpuRam": "der Prozessor dieses Computers, {ram}",
  "summary.asrSameDevice": "live {fast}, endgültig {final} ({device})",
  "summary.asrSplit": "live {fast} ({fastDevice}), endgültig {final} ({finalDevice})",
  "summary.translationKept": "Übersetzungsmodelle unverändert (der Übersetzungsdienst ist nicht lokal)",
  "summary.translationSame": "Übersetzung {model}",
  "summary.translationSplit": "Live-Übersetzung {fast}, endgültige Übersetzung {final}",
  "summary.full": "{asr}; {translation}",
  "firstRun.applied": "Die empfohlene Konfiguration für diesen Computer ({machine}) wurde übernommen: {summary}.",
  "firstRun.appliedGpuMissing":
    "Die empfohlene Konfiguration für diesen Computer ({machine}) wurde übernommen: {summary}. Für die Grafikkarte fehlt die GPU-Beschleunigungskomponente, daher wird vorerst die CPU verwendet; laden Sie sie auf der Seite „Modelle“ herunter, um die Grafikkarte zu nutzen.",
  "firstRun.applyFailed":
    "Die empfohlene Konfiguration konnte nicht übernommen werden: {reason} Prüfen Sie die aktuelle Konfiguration auf der Seite „Modelle“ und übernehmen Sie die Empfehlung dort bei Bedarf.",
  "firstRun.detectFailed":
    "Die Hardware dieses Computers konnte nicht erkannt werden: {reason} Vorerst wird die Standardkonfiguration verwendet (live {fast}, endgültig {final}); Sie können auf der Seite „Modelle“ erneut erkennen und die Empfehlung übernehmen.",
  "firstRun.review": "Auf der Seite „Modelle“ prüfen",
  "offer.text": "Für diesen Computer gibt es eine bessere Konfiguration: {summary}.",
  "offer.apply": "Übernehmen",
  "offer.dismiss": "Nicht mehr erinnern",
  "missing.itemFailed": "{model} konnte nicht heruntergeladen werden: {error}",
  "missing.downloading": "Download läuft: {models}; danach können Sie starten.",
  "missing.failed": "{items}. Sie können den Download wiederholen.",
  "missing.offer": "Vor dem Start muss Folgendes heruntergeladen werden: {models} (insgesamt etwa {size}).",
  "missing.done": "Die Modelle sind heruntergeladen. Sie können jetzt auf „Starten“ klicken.",
  "missing.download": "Fehlende Modelle herunterladen",
  "startdl.title": "Modelle werden vor dem Start heruntergeladen",
  "startdl.itemAsr": "Spracherkennungsmodell {model}",
  "startdl.itemTranslation": "Übersetzungsmodell {model}",
  "startdl.pending": "Wartet",
  "startdl.preparing": "Download wird vorbereitet",
  "startdl.progress": "{percent} %, {done} von {total}",
  "startdl.done": "Fertig",
  "startdl.failed": "Download fehlgeschlagen: {error}",
  "startdl.stillMissing": "{model} wurde bereits heruntergeladen, aber die Engine meldet es weiterhin als fehlend.",
  "startdl.downloading": "Die Untertitel starten automatisch, sobald die Downloads abgeschlossen sind.",
  "startdl.starting": "Downloads abgeschlossen; wird gestartet.",
  "startdl.failedHelp":
    "Ein Download ist fehlgeschlagen. Klicken Sie auf „Wiederholen“ oder wählen Sie auf der Seite „Modelle“ andere Modelle.",
  "startdl.pullUnsupported":
    "Der Übersetzungsserver {url} ist nicht Ollama, daher kann kikitori keine Übersetzungsmodelle für Sie herunterladen. Installieren Sie {models} selbst auf diesem Server oder wählen Sie auf der Seite „Modelle“ ein installiertes Modell. Sie können auch „Übersetzung anzeigen“ ausschalten, um nur das Original zu sehen.",
  "startdl.serverUnreachable":
    "Keine Verbindung zum Übersetzungsserver {url}. Wenn Sie Ollama verwenden, starten Sie es zuerst (öffnen Sie die Ollama-App oder führen Sie ollama serve in einem Terminal aus) und klicken Sie dann auf „Wiederholen“. Sie können auch „Übersetzung anzeigen“ ausschalten, um nur das Original zu sehen.",
  "startdl.authFailed":
    "Der Übersetzungsserver {url} hat die Anfrage abgelehnt – prüfen Sie den API-Schlüssel (die in den Einstellungen angegebene Umgebungsvariable) und klicken Sie dann auf „Wiederholen“.",
  "startdl.retry": "Wiederholen",
  "startdl.cancel": "Abbrechen",
  "startdl.cancelLabel": "Nicht mehr auf die Downloads warten und nicht automatisch starten",

  "gpu.title": "GPU-Beschleunigungskomponente",
  "gpu.lede":
    "Um die Spracherkennung mit einer NVIDIA-Grafikkarte zu beschleunigen, wird die cuBLAS-Bibliothek von NVIDIA benötigt. kikitori liefert sie nicht mit; sie wird von PyPI heruntergeladen, nachdem Sie ihre Lizenz akzeptiert haben.",
  "gpu.name": "GPU-Beschleunigungskomponente",
  "gpu.unsupported":
    "Die Plattform dieses Computers verwendet diese Komponente nicht: GPU-Beschleunigung gibt es nur unter Windows und Linux auf x86-64 mit einer NVIDIA-Grafikkarte. Auf anderen Plattformen läuft die Spracherkennung immer auf der CPU.",
  "gpu.progressLabel": "Downloadfortschritt der GPU-Beschleunigungskomponente",
  "gpu.finishing": "Heruntergeladen; wird geprüft und entpackt (einige Sekunden)",
  "gpu.progress": "{percent} %, {done} / {total}",
  "gpu.installed": "{what} installiert.",
  "gpu.installedAt": "{what} installiert in {path}.",
  "gpu.noCublas":
    "Installiert, aber es wird kein nutzbares cuBLAS erkannt. Starten Sie die Engine neu und klicken Sie dann auf „Erneut erkennen“.",
  "gpu.sizeApprox": "etwa {size}",
  "gpu.sizeUnknown": "einige hundert MB",
  "gpu.situationMissing":
    "Die NVIDIA-Grafikkarte dieses Computers kann die Erkennung beschleunigen, aber diese Komponente fehlt, daher wird vorerst die CPU verwendet.",
  "gpu.situationNoGpu": "Keine NVIDIA-Grafikkarte erkannt; ohne eine ist dieser Download nicht nötig.",
  "gpu.readLicense": "Lizenz lesen",
  "gpu.download": "GPU-Beschleunigungskomponente herunterladen",
  "gpu.consentNotice":
    "Dadurch wird NVIDIA cuBLAS ({package}, {size}) von PyPI heruntergeladen. Mit der Nutzung stimmen Sie dem NVIDIA CUDA EULA ({license}) zu.",
  "gpu.lastFailed": "Der letzte Download ist fehlgeschlagen: {error}",
  "gpu.agree": "Ich stimme dem NVIDIA CUDA EULA zu",
  "gpu.installedRestarting":
    "GPU-Beschleunigungskomponente installiert. Die Engine wird neu gestartet, um die GPU zu verwenden.",
  "gpu.installedBusy":
    "GPU-Beschleunigungskomponente installiert. Da gerade eine Aufgabe läuft, wurde die Engine nicht automatisch neu gestartet; starten Sie sie danach neu, um die GPU zu verwenden.",

  "asr.title": "Spracherkennungsmodelle",
  "asr.lede":
    "Live-Untertitel brauchen Geschwindigkeit, endgültige Übersetzungen Genauigkeit; für beide kann ein anderes Modell verwendet werden.",
  "asr.colModel": "Modell",
  "asr.colSize": "Größe",
  "asr.colSpeed": "Geschwindigkeit",
  "asr.colMemory": "Speicher",
  "asr.colLicense": "Lizenz",
  "asr.colStatus": "Status",
  "asr.inUse": "In Verwendung: {roles}",
  "asr.noSpeech": "Keine Erkennung von Nicht-Sprache",
  "asr.noSpeechHelp":
    "Dieses Modell erkennt nicht zuverlässig, wann keine Sprache vorliegt; für endgültige Übersetzungen wird die Einschätzung des Live-Untertitelmodells genutzt, um Geräusche herauszufiltern.",
  "asr.memoryCpu": "CPU {size}",
  "asr.memoryGpu": "GPU {size}",
  "asr.progressLabel": "Downloadfortschritt von {model}",
  "asr.progress": "{percent} %, {done}",
  "asr.downloaded": "Heruntergeladen",
  "asr.download": "Herunterladen",
  "asr.downloadFailed": "Download fehlgeschlagen: {error}",
  "role.fast": "live",
  "role.final": "endgültig",
  "speed.fastest": "Am schnellsten",
  "speed.fast": "Schnell",
  "speed.medium": "Mittel",
  "speed.slow": "Langsam",
  "speed.slowest": "Am langsamsten",
  "download.done": "{model} heruntergeladen.",
  "download.failed": "{what} konnte nicht heruntergeladen werden: {error}",

  "tr.title": "Übersetzungsmodelle",
  "tr.unreachable":
    "Der Übersetzungsdienst unter {url} ist nicht erreichbar. Wenn Sie ein lokales Ollama verwenden, starten Sie es zuerst.",
  "tr.unreachableError":
    "Der Übersetzungsdienst unter {url} ist nicht erreichbar ({error}). Wenn Sie ein lokales Ollama verwenden, starten Sie es zuerst.",
  "tr.installedCount": {
    one: "{count} Modell auf dem Übersetzungsdienst unter {url} installiert.",
    other: "{count} Modelle auf dem Übersetzungsdienst unter {url} installiert.",
  },
  "tr.installedCountLocal": {
    one: "{count} Modell auf dem Übersetzungsdienst unter {url} (lokal) installiert.",
    other: "{count} Modelle auf dem Übersetzungsdienst unter {url} (lokal) installiert.",
  },
  "tr.nonCommercial": "Nicht kommerziell",
  "tr.regionExcluded": "Lizenz schließt aus: {regions}",
  "tr.license": "Lizenz {license}",
  "tr.unlisted": "Nicht in der Liste von kikitori; prüfen Sie die Lizenz selbst",
  "tr.stateUnknown": "Installationsstatus unbekannt",
  "tr.stateInstalled": "Installiert",
  "tr.stateMissing": "Nicht installiert",

  "combo.title": "Benutzerdefinierte Konfiguration",
  "combo.lede":
    "Änderungen werden sofort für diesen Computer geprüft; eine Konfiguration mit Fehlern kann nicht gespeichert werden. Eine neue Konfiguration wird beim nächsten Klick auf „Starten“ wirksam.",
  "combo.fastModel": "Erkennungsmodell für Live-Untertitel",
  "combo.finalModel": "Erkennungsmodell für endgültige Übersetzungen",
  "combo.device": "Rechengerät",
  "combo.deviceAuto": "Automatisch (NVIDIA-GPU, falls verfügbar, sonst die CPU)",
  "combo.deviceCpu": "CPU",
  "combo.deviceCuda": "NVIDIA-GPU (CUDA)",
  "combo.deviceHelp":
    "Beide Pfade teilen sich ein Gerät: Fällt die GPU während einer Sitzung aus, wird sie nicht erneut versucht.",
  "combo.translationFast": "Übersetzungsmodell für Live-Untertitel",
  "combo.translationFinal": "Übersetzungsmodell für endgültige Übersetzungen",
  "combo.advanced": "Erweitert",
  "combo.advancedHelp":
    "Präzision und Thread-Anzahl werden meist der empfohlenen Konfiguration überlassen. Wenn leer oder „Automatisch“, entscheidet die Engine anhand des tatsächlich verwendeten Geräts (int8 auf der CPU, float16 auf der GPU).",
  "combo.fastComputeType": "Präzision für Live-Untertitel",
  "combo.finalComputeType": "Präzision für endgültige Übersetzungen",
  "combo.fastThreads": "CPU-Threads für Live-Untertitel",
  "combo.finalThreads": "CPU-Threads für endgültige Übersetzungen",
  "combo.cudaDirs": "Selbst installierte cuBLAS-12-Ordner",
  "combo.cudaDirsPlaceholder":
    "Ein vollständiger Pfad pro Zeile, z. B. C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v12.8\\bin",
  "combo.cudaDirsHelp":
    "Nur nötig, wenn Sie das CUDA Toolkit selbst installiert haben. Die oben heruntergeladene GPU-Beschleunigungskomponente wird automatisch verwendet und muss nicht aufgeführt werden.",
  "combo.save": "Konfiguration speichern",
  "combo.reset": "Auf aktuelle Einstellungen zurücksetzen",
  "combo.notInList": "{value} (nicht in der Liste)",
  "combo.notDownloaded": "{id} (nicht heruntergeladen)",
  "combo.saved": "Konfiguration gespeichert.",
  "combo.savedNextStart": "Gespeichert; wird beim nächsten Start wirksam.",
  "check.fixFirst": "Korrigieren Sie zuerst die markierten Felder.",
  "check.checking": "Diese Konfiguration wird geprüft",
  "check.okWithWarnings": "Dieser Computer kann diese Konfiguration verwenden, aber beachten Sie:",
  "check.ok": "Dieser Computer kann diese Konfiguration verwenden.",
  "check.notOk": "Diese Konfiguration kann derzeit nicht verwendet werden:",
  "check.rejected": "Die Engine akzeptiert diese Einstellung nicht: {message}",
  "check.unavailable":
    "Die Konfiguration kann derzeit nicht geprüft werden ({reason}). Sie können trotzdem speichern; die Engine prüft beim Speichern das Format der Einstellungen.",
  "check.engineNotReady": "Die Engine ist noch nicht bereit",
  "form.fixFields": "Korrigieren Sie die markierten Felder.",
  "form.engineRejected": "Die Engine hat diesen Wert abgelehnt: {message}",

  // ------------------------------------------------------------ settings page
  "settings.title": "Einstellungen",
  "settings.path": "Einstellungsdatei: {path}",
  "settings.uiLocaleLegend": "Oberflächensprache",
  "settings.uiLocaleLabel": "Oberflächensprache",
  "settings.uiLocaleSystem": "Systemeinstellung verwenden",
  "settings.uiLocaleHelp":
    "Gilt sofort für das Haupt- und das Untertitelfenster. „Systemeinstellung verwenden“ nutzt die Sprache Ihres Betriebssystems oder Englisch, falls diese nicht verfügbar ist.",
  "settings.translationLegend": "Übersetzungsdienst",
  "settings.translationLede":
    "Jede OpenAI-kompatible API funktioniert. Die Standard-URL eines lokalen Ollama ist http://localhost:11434/v1.",
  "settings.baseUrl": "Dienst-URL",
  "settings.apiKeyEnv": "Umgebungsvariable für den API-Schlüssel",
  "settings.apiKeyEnvHelp":
    "Geben Sie nur den Variablennamen ein. Legen Sie den Schlüssel selbst als Systemumgebungsvariable fest; kikitori speichert niemals Schlüssel. Ein lokales Ollama braucht keinen Schlüssel.",
  "settings.translationModelsHelp":
    "Übersetzungsmodelle werden in der benutzerdefinierten Konfiguration auf der Seite „Modelle“ gewählt, wo auch die Lizenz und die Auslastung dieses Computers geprüft werden.",
  "settings.regionLegend": "Lizenzregionen der Übersetzungsmodelle",
  "settings.regionLede":
    "Einige Übersetzungsmodelle (zum Beispiel Tencent HY-MT) sind nicht für die EU, das Vereinigte Königreich oder Südkorea lizenziert. kikitori kann nicht wissen, wo Sie sich befinden, und empfiehlt sie daher standardmäßig nicht.",
  "settings.regionConsent":
    "Ich bestätige, dass ich kikitori nicht in der EU, im Vereinigten Königreich oder in Südkorea verwende, und stimme den Lizenzbedingungen dieser Übersetzungsmodelle zu.",
  "settings.regionHelp":
    "Wenn aktiviert, berücksichtigt die empfohlene Konfiguration auch regional eingeschränkte Modelle. Wenn nicht aktiviert, können Sie sie trotzdem in der benutzerdefinierten Konfiguration auswählen, mit einer Warnung.",
  "settings.languageLegend": "Sprachen",
  "settings.languageLede":
    "Wählen Sie die Sprache, die Sie hören, und die Untertitelsprache; die dafür empfohlenen Modelle werden unten aufgeführt. Jetzt wird nichts heruntergeladen: Fehlende Modelle werden beim ersten Klick auf „Starten“ heruntergeladen.",
  "settings.languageModeMedia": "Dies sind die Sprachen für die Videoübersetzung.",
  "settings.languageModeInterpret": "Dies sind die Sprachen für das Live-Dolmetschen.",
  "settings.source": "Hörsprache",
  "settings.sourceHelp":
    "Die im Video gesprochene Sprache. Bei „Automatisch erkennen“ wird die Sprache jedes Satzes zuerst bestimmt; sehr kurze Sätze können falsch bestimmt werden.",
  "settings.own": "Meine Sprache",
  "settings.ownHelp": "Die Sprache, die Sie sprechen; was Ihr Gegenüber sagt, wird in diese Sprache übersetzt.",
  "settings.languageFilter": "Zum Filtern tippen",
  "settings.languageFilterLabel": "{field} filtern",
  "settings.applyRecommended": "Auch zu den empfohlenen Modellen wechseln",
  "settings.applyRecommendedHelp":
    "Ändert die Erkennungsmodelle, das Gerät und die Übersetzungsmodelle; wenn nicht aktiviert, ändern sich nur die Sprachen.",
  "settings.saveLanguages": "Sprachen speichern",
  "settings.languagesSaved": "Sprachen gespeichert.",
  "settings.languagesSavedWithModels": "Sprachen gespeichert; die empfohlenen Modelle werden jetzt verwendet.",
  "settings.languagesSavedNoPreview":
    "Sprachen gespeichert; die empfohlene Kombination war noch nicht bereit, daher wurden die Modelle nicht geändert.",
  "settings.languagesSavedNextStart": "Gespeichert; wird beim nächsten Start wirksam.",
  "settings.languagesUnavailable": "Die Sprachen können gewählt werden, sobald die Engine bereit ist.",
  "language.auto": "Automatisch erkennen",
  "preview.title": "Empfohlene Kombination",
  "preview.checking": "Die empfohlene Kombination für diese Sprachen wird ermittelt",
  "preview.unavailable": "Die empfohlene Kombination ist derzeit nicht verfügbar: {reason}",
  "preview.translation": "Übersetzungsmodell",
  "preview.installed": "Installiert",
  "preview.downloadOnFirstUse": "Wird bei der ersten Verwendung heruntergeladen, etwa {size}",
  "preview.downloadUnknownSize": "Wird bei der ersten Verwendung heruntergeladen (Größe unbekannt)",
  "preview.installedUnknown":
    "Unklar, ob es installiert ist (der Übersetzungsserver hat nicht geantwortet)",
  "preview.notNeeded": "Keine Übersetzung nötig",
  "preview.translationOff": "„Übersetzung anzeigen“ ist ausgeschaltet, daher ist kein Übersetzungsmodell nötig",
  "preview.unsupported": "Kein Übersetzungsmodell unterstützt diese Sprachkombination",
  "preview.remote":
    "Der Übersetzungsserver befindet sich nicht auf diesem Computer; wählen Sie sein Modell in der benutzerdefinierten Kombination auf der Seite „Modelle“",
  "preview.allInstalled": "Alle benötigten Modelle sind installiert.",
  "preview.total": "Beim ersten Start werden etwa {size} heruntergeladen.",
  "preview.totalUnknown":
    "Beim ersten Start werden Modelle heruntergeladen, teils unbekannter Größe (etwa {size} bekannt).",
  "settings.target": "Untertitelsprache",
  "settings.targetHelp":
    "Die Sprache der Untertitel. Ist sie dieselbe gesprochene Sprache wie die Hörsprache, wird nichts übersetzt; Chinesisch wird nur zwischen den Schriftsystemen umgewandelt.",
  "settings.partner": "Sprache des Gegenübers",
  "settings.partnerHelp":
    "Was Sie sagen, wird in diese Sprache übersetzt, und was Ihr Gegenüber sagt, in Ihre. Die beiden müssen unterscheidbar sein und dürfen daher nicht gleich sein (traditionelles und vereinfachtes Chinesisch gelten als gleich).",
  "settings.styleLegend": "Untertitel",
  "settings.showTranslation": "Übersetzung anzeigen",
  "settings.showTranslationHelp":
    "Wenn ausgeschaltet, wird nur das Original in Größe und Farbe der Hauptuntertitel angezeigt: Die Engine übersetzt nichts, braucht keinen Übersetzungsserver und lässt die GPU frei. Ein Umschalten während des Betriebs gilt sofort; bereits angezeigte Zeilen werden nachträglich nicht übersetzt.",
  "settings.previewOriginal": "次の駅で降りましょう。",
  "settings.previewTranslation": "Lass uns an der nächsten Station aussteigen.",
  "settings.fontSize": "Schriftgröße der Übersetzung",
  "settings.opacity": "Deckkraft des Hintergrunds",
  "settings.position": "Position der Untertitel",
  "settings.positionBottom": "Unten im Fenster",
  "settings.positionTop": "Oben im Fenster",
  "settings.lines": "Gleichzeitig angezeigte Sätze",
  "settings.linesOption": { one: "{count} Satz", other: "{count} Sätze" },
  "settings.save": "Einstellungen speichern",
  "settings.saved": "Einstellungen gespeichert.",
  "settings.savedRegion":
    "Einstellungen gespeichert. Die empfohlene Konfiguration wird für die neue Lizenzregion neu berechnet (siehe Seite „Modelle“).",

  // ------------------------------------------------------------ validation
  "validation.urlScheme": "Die URL muss mit http:// oder https:// beginnen.",
  "validation.urlEmpty": "Geben Sie die URL des Übersetzungsdienstes ein.",
  "validation.urlInvalid": "Dies ist keine gültige URL.",
  "validation.envPattern": "Nur Buchstaben, Ziffern und Unterstriche verwenden; nicht mit einer Ziffer beginnen.",
  "validation.envTooLong": "Der Name ist zu lang (höchstens {max} Zeichen).",
  "validation.partner":
    "Die Sprache des Gegenübers muss sich von Ihrer unterscheiden (traditionelles und vereinfachtes Chinesisch gelten als dieselbe Sprache).",
  "validation.language": "Wählen Sie eine Sprache aus der Liste.",
  "validation.fontSize": "Die Schriftgröße muss zwischen {min} und {max} liegen.",
  "validation.opacity": "Die Deckkraft des Hintergrunds muss zwischen 0 % und 100 % liegen.",
  "validation.position": "Wählen Sie oben oder unten.",
  "validation.lines": "Die Anzahl der Sätze muss zwischen {min} und {max} liegen.",
  "validation.asrModel": "Wählen Sie ein Spracherkennungsmodell.",
  "validation.device": "Wählen Sie ein Rechengerät.",
  "validation.translationEmpty": "Geben Sie den Namen eines Übersetzungsmodells ein.",
  "validation.translationTooLong": "Modellnamen haben höchstens {max} Zeichen.",
  "validation.computeType": "Wählen Sie eine Präzision.",
  "validation.threads":
    "Die Thread-Anzahl muss zwischen {min} und {max} liegen; für „Automatisch“ leer lassen.",
  "validation.cudaDirsTooMany": "Höchstens {max} Ordner.",
  "validation.cudaDirNotAbsolute": "„{path}“ ist kein vollständiger Pfad (zum Beispiel C:\\CUDA\\bin).",

  // ------------------------------------------------------------ about
  "about.title": "Über",
  "about.engine": "Engine {version}, Protokollversion {protocol}.",
  "about.engineFake":
    "Engine {version}, Protokollversion {protocol}; derzeit wird die simulierte Engine verwendet (Entwicklungsmodus).",
  "about.engineOffline": "Die Engine ist noch nicht verbunden.",
  "about.license": "kikitori wird unter der Lizenz {license} veröffentlicht.",
  "about.notice":
    "Drittanbieterkomponenten in diesem Programm, etwa Intel® OpenMP, werden unter ihren eigenen Lizenzbedingungen bereitgestellt; Reverse Engineering ist nicht gestattet.",
  "about.thirdPartyHead": "Vollständige Lizenztexte der Drittanbieterkomponenten:",
  "about.thirdPartyApp": "Die App selbst (Tauri/Rust-Crates, Frontend-Pakete und die LICENSE von kikitori): ",
  "about.thirdPartyEngine": "Sprach-Engine (Python-Pakete): ",
  "about.thirdPartyDev":
    "Entwicklungs-Builds enthalten keine Lizenzordner. In Release-Builds befinden sich die Lizenztexte in den Ordnern licenses (die App) und kikitori-engine/THIRD_PARTY_LICENSES (die Sprach-Engine) im Installationsverzeichnis.",
  "about.openAppLicenses": "Drittanbieter-Lizenzordner der App öffnen",
  "about.openEngineLicenses": "Drittanbieter-Lizenzordner der Engine öffnen",
  "about.checkUpdate": "Nach Updates suchen",

  // ------------------------------------------------------------ updates
  "update.available": "kikitori {version} steht zum Herunterladen und Installieren bereit.",
  "update.install": "Herunterladen und installieren",
  "update.downloading": "Das Update wird heruntergeladen.",
  "update.progress": "Das Update wird heruntergeladen: {percent} %",
  "update.installed": "kikitori {version} ist installiert und wird nach einem Neustart wirksam.",
  "update.restartNow": "Jetzt neu starten",
  "update.checking": "Wird gesucht",
  "update.found": "Neue Version {version} verfügbar.",
  "update.latest": "Sie verwenden die neueste Version.",
  "update.checkFailed": "Die Suche nach Updates ist fehlgeschlagen: {reason}",
  "update.failed": "Update fehlgeschlagen: {reason}",
  "update.relaunchFailed":
    "Automatischer Neustart fehlgeschlagen; schließen Sie kikitori und öffnen Sie es erneut. ({reason})",

  // ------------------------------------------------------------ overlay
  "overlay.title": "kikitori-Untertitel",
  "overlay.drag": "Zum Verschieben ziehen",
  "overlay.hide": "Ausblenden",
  "overlay.hint":
    "Hier erscheinen die Untertitel. Ziehen Sie das Fenster, um es zu verschieben; ziehen Sie die untere rechte Ecke, um die Größe zu ändern.",

  // ------------------------------------------------------------ transcript export, reading rhythm, OBS text file, first use and diagnostics
  "export.button": "Exportieren",
  "export.content": "Inhalt",
  "export.contentBoth": "Original und Übersetzung",
  "export.contentOriginal": "Nur Original",
  "export.contentTranslation": "Nur Übersetzung",
  "export.help": "Exportiert alle vollständigen Sätze dieser Sitzung (nicht nur die noch angezeigten) nach kikitori/transcripts in Ihrem Dokumente-Ordner. CSV lässt sich direkt in Excel öffnen.",
  "export.empty": "Es gibt noch keine vollständigen Sätze zum Exportieren.",
  "export.saved": "Transkript gespeichert: {path}",
  "export.savedAuto": "Das Transkript dieser Sitzung wurde automatisch gespeichert (SRT und TXT): {path}",
  "export.openFolder": "Ordner öffnen",
  "error.export": "Das Transkript konnte nicht gespeichert werden",
  "error.autoSave": "Das Transkript konnte nicht automatisch gespeichert werden",
  "error.openFolder": "Der Ordner konnte nicht geöffnet werden; öffnen Sie ihn über den in den Einstellungen angezeigten Pfad",
  "error.applyObs": "Die Einstellung für die OBS-Textdatei konnte nicht übernommen werden",
  "error.openOllama": "Die Downloadseite von Ollama konnte nicht geöffnet werden; öffnen Sie https://ollama.com/download im Browser",
  "settings.minDisplay": "Jede Zeile mindestens anzeigen (Sekunden)",
  "settings.minDisplayHelp": "Bei schnellem Sprechen wartet eine neue Zeile, bis die vorherige so lange angezeigt wurde, bevor sie sie ersetzt – so können Sie zu Ende lesen. 0 ersetzt sofort.",
  "settings.idleClear": "Ausblenden ohne neue Untertitel (Sekunden)",
  "settings.idleClearHelp": "So lange nach der letzten Zeile wird das Untertitelfenster ausgeblendet und die OBS-Textdatei geleert. 0 lässt die Untertitel stehen.",
  "output.legend": "Transkripte und OBS",
  "output.autoSave": "Am Ende jeder Sitzung automatisch speichern (SRT und TXT)",
  "output.autoSaveHelp": "Wenn Sie auf „Stoppen“ klicken oder die Engine anhält, wird das Transkript der Sitzung (Original und Übersetzung) im Ordner unten gespeichert.",
  "output.transcriptsPath": "Speicherort: {path}",
  "output.noDocuments": "Der Dokumente-Ordner wurde nicht gefunden; Speichern ist nicht möglich.",
  "output.openTranscripts": "Transkript-Ordner öffnen",
  "output.obs": "Untertitel in eine Textdatei schreiben (für OBS)",
  "output.obsOff": "Aus",
  "output.obsOriginal": "Original",
  "output.obsTranslation": "Übersetzung",
  "output.obsBoth": "Original und Übersetzung",
  "output.obsHelp": "Wenn aktiviert, werden die Untertitel aus dem Untertitelfenster laufend in eine Textdatei geschrieben. Fügen Sie in OBS eine Quelle „Text (GDI+)“ hinzu, aktivieren Sie „Aus Datei lesen“ und wählen Sie diese Datei. Es wird kein Netzwerkport geöffnet.",
  "output.obsPath": "Textdatei: {path}",
  "output.obsError": "Die OBS-Textdatei konnte nicht geschrieben werden: {error}",
  "output.openObs": "Ordner der OBS-Textdatei öffnen",
  "validation.minDisplay": "Muss zwischen {min} und {max} Sekunden liegen.",
  "validation.idleClear": "Muss zwischen {min} und {max} Sekunden liegen; 0 lässt die Untertitel stehen.",
  "validation.obsOutput": "Wählen Sie, was geschrieben werden soll.",
  "guide.local": "Der Übersetzungsdienst unter {url} ist nicht erreichbar. Um Übersetzungen zu sehen, installieren und starten Sie Ollama (kostenlos, läuft auf diesem Computer) und klicken Sie dann auf „Erneut erkennen“. Sie können vorerst auch nur das Original anzeigen.",
  "guide.remote": "Der Übersetzungsdienst unter {url} ist nicht erreichbar. Prüfen Sie, ob er läuft und die Adresse stimmt, und klicken Sie dann auf „Erneut erkennen“ – oder zeigen Sie vorerst nur das Original an.",
  "guide.install": "Ollama installieren",
  "guide.originalOnly": "Nur Original anzeigen",
  "guide.recheck": "Erneut erkennen",
  "transcript.shortcutTranslation": "Mit {keys} schalten Sie „Übersetzung anzeigen“ ein oder aus.",
  "transcript.shortcutClear": "Mit {keys} löschen Sie die Untertitel im Untertitelfenster und in der OBS-Textdatei.",
  "transcript.shortcutActionTaken": "Das Tastenkürzel {keys} ({action}) wird von einer anderen App verwendet und ist nicht verfügbar.",
  "shortcut.toggleTranslation": "Übersetzung umschalten",
  "shortcut.clearSubtitles": "Untertitel löschen",
  "about.copyDiagnostics": "Diagnose kopieren",
  "about.diagnosticsHelp": "Fügen Sie dies einer Problemmeldung bei: Versionen, Betriebssystem, Hardwareerkennung, Einstellungen (beim API-Schlüssel nur der Name der Umgebungsvariable, nie der Schlüssel) und die letzten Meldungen der Engine. Benutzernamen in Pfaden werden ausgeblendet.",
  "about.diagnosticsCopied": "In die Zwischenablage kopiert.",
  "about.diagnosticsManual": "Die Zwischenablage ist nicht verfügbar; kopieren Sie den Text unten von Hand.",
  // ------------------------------------------------------------ 術語表（F3）、狀態列音量與延遲（F4）、刪除模型（F7）、新引擎訊息
  "glossary.legend": "Glossar",
  "glossary.lede": "Legen Sie fest, wie Namen und Livestream-Begriffe übersetzt werden: Enthält das Original einen davon, verwendet die Übersetzung den hier angegebenen Text. Ihre Einträge haben Vorrang vor der eingebauten Liste.",
  "glossary.builtin": "Eingebaute Livestream-Begriffe verwenden",
  "glossary.builtinHelp": "Häufige Begriffe aus japanischen Livestreams (z. B. スパチャ → Super Chat); nur verwendet, wenn das Original Japanisch ist und die Untertitel traditionelles oder vereinfachtes Chinesisch, Englisch oder Koreanisch sind.",
  "glossary.colSource": "Original",
  "glossary.colTarget": "Übersetzung",
  "glossary.colLanguage": "Untertitelsprache",
  "glossary.colActions": "Aktionen",
  "glossary.allLanguages": "Alle Sprachen",
  "glossary.sourceLabel": "Original, Zeile {row}",
  "glossary.targetLabel": "Übersetzung, Zeile {row}",
  "glossary.languageLabel": "Untertitelsprache, Zeile {row}",
  "glossary.remove": "Entfernen",
  "glossary.removeLabel": "Zeile {row} entfernen",
  "glossary.empty": "Noch keine eigenen Einträge.",
  "glossary.add": "Eintrag hinzufügen",
  "glossary.import": "CSV importieren",
  "glossary.export": "CSV exportieren",
  "glossary.csvHelp": "CSV-Spalten: source (Original), target (Übersetzung), target_language (Code der Untertitelsprache, z. B. de; leer = alle Sprachen). In Excel bearbeitbar. Importierte Einträge werden am Ende der Tabelle angefügt und gelten, sobald Sie auf „Glossar speichern“ klicken; exportierte Dateien landen im Transkript-Ordner.",
  "glossary.save": "Glossar speichern",
  "glossary.reset": "Änderungen verwerfen",
  "glossary.saved": "Gespeichert.",
  "glossary.savedNextStart": "Gespeichert; wird beim nächsten Start wirksam.",
  "glossary.exported": "Glossar exportiert: {path}",
  "glossary.imported": "Importierte Einträge: {added}. Übersprungene Duplikate: {skipped}. Klicken Sie auf „Glossar speichern“, um sie zu übernehmen.",
  "glossary.importBadLines": "Zeilen ohne Original oder Übersetzung wurden nicht importiert: {lines}.",
  "glossary.importTooLarge": "Die Datei ist zu groß (Grenze {size}); prüfen Sie, ob es eine Glossar-CSV ist.",
  "glossary.error.empty": "Pflichtfeld.",
  "glossary.error.spaces": "Entfernen Sie Leerzeichen am Anfang oder Ende.",
  "glossary.error.tooLong": "Höchstens {max} Zeichen.",
  "glossary.error.control": "Zeilenumbrüche, Tabulatoren und unsichtbare Steuerzeichen sind nicht erlaubt.",
  "glossary.error.language": "Wählen Sie eine Sprache aus der Liste.",
  "glossary.error.duplicate": "Gleich wie Zeile {row} (gleiche Untertitelsprache).",
  "glossary.error.tooMany": "Höchstens {max} Einträge (derzeit {count}).",
  "error.exportGlossary": "Das Glossar konnte nicht exportiert werden",
  "error.importGlossary": "Die CSV-Datei konnte nicht gelesen werden",
  "metrics.levelLabel": "Eingangspegel",
  "metrics.levelValue": "{db} dBFS",
  "metrics.levelUnknown": "Noch kein Pegelwert",
  "metrics.latency": "Verzögerung {seconds} s",
  "metrics.latencyUnknown": "Verzögerung —",
  "metrics.latencyHelp": "Zeit vom Satzende bis zum endgültigen Untertitel (Median der letzten Sätze)",
  "metrics.backlog": "Die Erkennung kommt nicht hinterher: Mehrere Sätze warten auf endgültige Untertitel. Wählen Sie auf der Seite „Modelle“ besser ein kleineres Modell.",
  "metrics.openModels": "Zu „Modelle“",
  "metrics.noAudio": "Seit 15 Sekunden kommt kein Ton an. Prüfen Sie, ob die richtige Audioquelle gewählt ist, das Video läuft oder das Mikrofon nicht stummgeschaltet ist.",
  "asr.diskUse": "{size} auf der Festplatte",
  "asr.delete": "Löschen",
  "asr.deleting": "Wird gelöscht",
  "asr.deleteYes": "Ja, löschen",
  "asr.deleteNo": "Abbrechen",
  "asr.deleteConfirm": "{model} löschen? Dadurch werden {size} frei.",
  "asr.deleteSelected": "Ihre Einstellungen nutzen es für Untertitel ({roles}); es wird beim nächsten Start erneut heruntergeladen.",
  "asr.deleteInUse": "Die laufende Sitzung verwendet dieses Modell; stoppen Sie sie zuerst.",
  "asr.deleteDownloading": "Wird heruntergeladen; Löschen ist danach möglich.",
  "asr.deleted": "{model} gelöscht; {size} freigegeben.",
  "error.deleteModel": "{model} konnte nicht gelöscht werden",
  "error.code.model_not_downloaded": "Dieses Modell ist nicht heruntergeladen",
  "error.code.model_in_use": "Das Modell wird verwendet; zuerst stoppen",
  "engineParam.compute_type_pair": "live {fast}, endgültig {final}",
  "engine.asr_bgm_robust_final": "{final_model} für endgültige Untertitel kommt in Livestreams am besten mit Hintergrundmusik und Spielsound zurecht (kikitoris Auswertung japanischer Livestreams)",
  "engine.asr_live_model_for_language": "{fast_model} ist bei {language} deutlich ungenauer, daher nutzen auch die Live-Untertitel {model} (einmal geladen und gemeinsam genutzt)",
};
