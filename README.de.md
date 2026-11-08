# kikitori（聞き取り）

[繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md) | [English](README.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | **Deutsch** | [Português (Brasil)](README.pt-BR.md) | [Русский](README.ru.md) | [Tiếng Việt](README.vi.md) | [ไทย](README.th.md) | [Bahasa Indonesia](README.id.md)

*Dies ist eine Übersetzung der traditionell-chinesischen README.zh-TW.md; bei Abweichungen gilt README.zh-TW.md. Die Übersetzung wurde mit KI-Unterstützung erstellt; Korrekturen sind über [Issues](https://github.com/alianlbj23/kikitori/issues) willkommen.*

kikitori hört den Ton, den Ihr Computer gerade abspielt (oder das Mikrofon), erkennt ihn in Echtzeit als Text, übersetzt ihn in die gewählte Sprache und zeigt ihn als zweisprachige Untertitel an, die über dem Bildschirm schweben.
Spracherkennung (Whisper) und Übersetzung (lokales Ollama) laufen auf Ihrem eigenen Computer: Ton und Text werden an keinen Cloud-Dienst hochgeladen.

Der Hauptanwendungsfall sind **japanische VTuber-Livestreams** (mit BGM, Spielgeräuschen und Stream-Jargon); Modellempfehlungen und Parameter wurden anhand der Messungen dieses Projekts abgestimmt.
Beliebige andere Kombinationen der 20 Sprachen funktionieren ebenfalls.

- Plattformen: Windows 10/11 x64 (wichtigste getestete Plattform), Linux x64, macOS Apple Silicon
- Lizenz: MIT (für die Modelle gelten eigene Lizenzen, siehe „Lizenzen“)
- Aktuelle Version: 0.2.0 ([Änderungen](docs/release-notes/v0.2.0.md))

## Funktionen

| Funktion | Beschreibung |
|---|---|
| Videoübersetzung | Nimmt den vom System abgespielten Ton auf (Windows WASAPI loopback / Linux PulseAudio monitor / macOS ScreenCaptureKit) und übersetzt die Hörsprache in die Untertitelsprache |
| Live-Dolmetschen | Nimmt das Mikrofon auf; was Sie in „Meine Sprache“ sagen, wird in die Sprache des Gegenübers übersetzt, was das Gegenüber sagt, in Ihre Sprache |
| Zweistufige Untertitel: erst schnell, dann genau | Solange ein Satz noch gesprochen wird, liefert ein schnelleres Modell zunächst „Live-Untertitel“; danach berechnet ein genaueres Modell den „endgültigen Untertitel“ und ersetzt ihn |
| Stabile Live-Untertitel | Der Anfang eines Live-Untertitels, der sich nicht mehr ändert, wird normal angezeigt, das noch veränderliche Ende blass; die Übersetzung flackert nicht mehr mit dem Ende mit |
| 20 Sprachen frei kombinierbar | Hörsprache (auch „Automatisch erkennen“) und Untertitelsprache frei aus 20 Sprachen wählbar, siehe „Unterstützte Sprachen“ |
| 13 Oberflächensprachen | Folgt standardmäßig dem Betriebssystem, umstellbar in den Einstellungen |
| Nur Original anzeigen | Schalten Sie „Übersetzung anzeigen“ aus, sehen Sie nur das Original: kein Ollama nötig, keine GPU belegt; auch während des Betriebs umschaltbar |
| Furigana | Lesungen über den Kanji des japanischen Originals (SudachiDict, ca. 69 MiB, wird erst beim ersten Einschalten heruntergeladen) |
| Modellempfehlung nach Hardware | Erkennt CPU, Arbeitsspeicher, NVIDIA-Grafikkarte und Ollama und wählt automatisch eine Modellkombination; fehlende Modelle werden erst beim ersten Klick auf „Starten“ heruntergeladen |
| Glossar | 53 eingebaute japanische Stream-Begriffe (赤スパ, 推し, メン限 …) und leicht verwechselbare chinesisch-japanische Wörter für Chinesisch → Japanisch; eigene Einträge möglich, CSV-Import/-Export |
| Transkript exportieren | SRT, VTT, TXT, CSV; optional automatisches Speichern am Ende jeder Sitzung |
| OBS-Textdatei | Schreibt die aktuellen Untertitel in eine Textdatei für OBS „Text (GDI+) → Aus Datei lesen“; es wird kein Netzwerkport geöffnet |
| Untertitelfenster | Transparent, immer im Vordergrund, klickdurchlässig; Schriftgröße, Hintergrund, Position, Anzahl der Sätze, Mindestanzeigedauer pro Satz und Ausblenden nach Leerlauf einstellbar |
| Statusleiste | Pegelanzeige, Latenz (Median vom Satzende bis zum endgültigen Untertitel); Hinweis, wenn die Erkennung nicht hinterherkommt oder 15 Sekunden lang kein Ton ankommt |
| Modellverwaltung | Erkennungsmodelle herunterladen, Speicherplatz anzeigen, löschen; lädt Übersetzungsmodelle für Sie in Ollama herunter |
| Automatische Updates | Prüft beim Start GitHub Releases, lädt herunter, prüft die Signatur und installiert |

## Unterstützte Sprachen

Hör- und Untertitelsprachen (20, frei kombinierbar):

| | | | | |
|---|---|---|---|---|
| 日本語 | English | 繁體中文（台灣） | 简体中文 | 한국어 |
| Español | Français | Deutsch | Português | Русский |
| Italiano | العربية | हिन्दी | Bahasa Indonesia | Tiếng Việt |
| ไทย | Türkçe | Polski | Nederlands | Українська |

- Untertitel in traditionellem Chinesisch werden mit OpenCC `s2twp` in die in Taiwan übliche traditionelle Schrift und taiwanischen Sprachgebrauch umgewandelt (z. B. 视频 → 影片). Umgewandelt wird nur, wenn die Übersetzung vereinfachte Zeichen enthält; „台灣“ wird nicht zu „臺灣“ geändert.
- Sind Hör- und Untertitelsprache gleich, wird nicht übersetzt (zwischen traditionellem und vereinfachtem Chinesisch wird nur die Schrift umgewandelt).
- Die Qualität der Spracherkennung hängt von der Sprache ab. Liegt die Fehlerrate eines Modells für eine Sprache über 20 % (z. B. Thai, Arabisch und Hindi bei large-v3-turbo, Vietnamesisch bei large-v3), weist die Empfehlung auf eine geringere Erkennungsqualität hin; für Thai, Arabisch und Hindi verwenden auf Grafikkarten ab 8 GB auch die Live-Untertitel large-v3.

Oberflächensprachen (13): 繁體中文（台灣）, 简体中文, English, 日本語, 한국어, Español, Français, Deutsch, Português (Brasil), Русский, Tiếng Việt, ไทย, Bahasa Indonesia.
Arabisch ist als Hör- oder Untertitelsprache verfügbar, aber nicht als Oberflächensprache (die Oberfläche unterstützt kein Rechts-nach-links-Layout).

## Empfohlene Kombinationen (nach Hardware)

kikitori erkennt die Hardware und übernimmt die Kombination automatisch; die Tabelle zeigt nur, was Sie bekommen. Grundlage sind die Evaluierungen dieses Projekts (`experiments/vtuber_eval/`, `experiments/translate_eval/`); Regeln und Formeln stehen in [`docs/specs/hardware.md`](docs/specs/hardware.md).

| Ihr Computer | Live-Untertitel | Endgültige Untertitel | Übersetzung (Ollama) |
|---|---|---|---|
| NVIDIA ab 16 GB | large-v3-turbo (float16) | large-v3 (float16) | qwen3.5:9b |
| NVIDIA 12 GB (z. B. RTX 5070) | large-v3-turbo (int8_float16) | large-v3 (int8_float16) | qwen3.5:9b |
| NVIDIA 8–11 GB | large-v3-turbo (small, wenn der Grafikspeicher genau an der 8-GB-Untergrenze liegt) | large-v3 (Präzision je nach Grafikspeicher) | qwen3.5:4b |
| NVIDIA 4–8 GB | base | large-v3-turbo | qwen3.5:4b |
| NVIDIA unter 4 GB | base | small | qwen3.5:4b |
| Keine NVIDIA-GPU, RAM ≥ 16 GB, 5 oder mehr Kerne | base (CPU) | small (CPU) | qwen3.5:4b |
| Keine NVIDIA-GPU, weniger RAM oder höchstens 4 Kerne | tiny (CPU) | base (CPU) | qwen3.5:4b |
| Mac (Apple Silicon) | base (CPU) | small (CPU) | Je nach Größe des Unified Memory (16 GB: qwen3.5:9b) |

- Die Präzisionen int8_float16 und float16 lieferten in Messungen dieselbe Erkennungsqualität (kein signifikanter Unterschied), int8_float16 braucht aber nur etwa die Hälfte des Grafikspeichers. Teilen sich Erkennung und Übersetzungsmodell die Grafikkarte, gibt kikitori dem Übersetzungsmodell den Vorrang, damit es eine Stufe größer sein kann.
- Downloadgröße der Erkennungsmodelle: large-v3-turbo 1.62 GB, large-v3 3.09 GB, small 487 MB, base 148 MB, tiny 78 MB. Übersetzungsmodelle: qwen3.5:9b ca. 6.6 GB, qwen3.5:4b ca. 3.3 GB.
- NVIDIA-GPUs benötigen cuBLAS 12 (ca. 570 MB). kikitori liefert es nicht mit dem Installer aus; nach Zustimmung zur NVIDIA EULA lässt es sich auf der Seite „Modelle“ mit einem Klick herunterladen. Ohne cuBLAS wird die CPU verwendet.
- Läuft der Übersetzungsserver auf einem anderen Computer (remote), wird kein Übersetzungsmodell automatisch gewählt; wählen Sie es selbst auf der Seite „Modelle“.
- Für die Übersetzung wird immer bevorzugt Qwen3.5 (Apache-2.0) empfohlen: 9B, wenn der Grafikspeicher reicht, sonst 4B. Bei Grafikkarten unter 4 GB oder ohne NVIDIA-GPU ist es ebenfalls qwen3.5:4b; Ollama lagert den Teil, der nicht passt, auf die CPU aus, was langsamer ist (die Übersetzungslatenz wurde für diesen Fall noch nicht gemessen).
- Tencent HY-MT unterliegt regionalen Einschränkungen und kann erst empfohlen werden, nachdem Sie in den Einstellungen unter „Lizenzregionen der Übersetzungsmodelle“ bestätigt haben, dass Sie kikitori nicht in der EU, im Vereinigten Königreich oder in Südkorea verwenden. Sakura (nicht-kommerzielle Lizenz) wird nie empfohlen und kann nur manuell gewählt werden.
- Die Erkennungsgeschwindigkeit unter macOS und Linux sowie die Übersetzungsgeschwindigkeit auf der CPU wurden noch nicht gemessen (siehe „Bekannte Einschränkungen“).

## Erste Schritte

1. Laden Sie den Installer von [GitHub Releases](https://github.com/alianlbj23/kikitori/releases) herunter (Installationshinweise für jede Plattform stehen auf der Release-Seite: SmartScreen-/Gatekeeper-Warnungen wegen fehlender Signatur, fuse3 und Schriftarten unter Linux).
2. Zum Übersetzen installieren Sie [Ollama](https://ollama.com/download) und lassen es im Hintergrund laufen. Findet kikitori es nicht, bietet es „Ollama installieren“ oder „Nur Original anzeigen“ an.
3. Öffnen Sie kikitori. Beim ersten Start wird die für Ihre Hardware empfohlene Kombination automatisch übernommen.
4. Wählen Sie in den Einstellungen unter „Sprachen“ Hörsprache und Untertitelsprache. Danach werden nur die empfohlene Kombination und die Downloadgröße angezeigt; **es wird nicht sofort heruntergeladen**.
5. Wählen Sie die Audioquelle und klicken Sie auf „Starten“. Fehlende Erkennungsmodelle und Übersetzungsmodelle (bei Ollama) werden zuerst heruntergeladen, danach starten die Untertitel automatisch.

### Nur Original anzeigen

Der Schalter „Übersetzung anzeigen“ in der Werkzeugleiste der Seite „Transkript“ und im Bereich „Untertitel“ der Einstellungen (oder das Tastenkürzel Ctrl+Alt+J). Wenn er ausgeschaltet ist:

- verbindet sich die Engine überhaupt nicht mit dem Übersetzungsserver, Ollama muss nicht installiert sein und die GPU wird nicht für ein Übersetzungsmodell belegt;
- zeigt das Untertitelfenster das Original in Schriftgröße und Farbe der Hauptuntertitel;
- wirkt das Umschalten während des Betriebs sofort; bereits angezeigte Sätze werden nicht nachträglich übersetzt.

### Furigana

Einstellung „Furigana über japanischen Originaltexten anzeigen“: Ist die Hörsprache Japanisch, werden über den Kanji des Originals die Lesungen in Hiragana angezeigt (Okurigana / 送り仮名 werden nicht doppelt annotiert). Beim ersten Einschalten wird SudachiDict core (Apache-2.0, ca. 69 MiB) heruntergeladen; auf der Seite „Modelle“ können Sie den Speicherplatz einsehen und das Wörterbuch löschen. Das betrifft nur die Anzeige, exportierte Untertitel bleiben unverändert. Design: [`docs/specs/furigana.md`](docs/specs/furigana.md).

### Glossar

Einstellungen, Bereich „Glossar“:

- **Eingebaute Stream-Begriffe** („Eingebaute Livestream-Begriffe verwenden“, standardmäßig an): werden verwendet, wenn das Original Japanisch ist und die Untertitel traditionelles oder vereinfachtes Chinesisch, Englisch oder Koreanisch sind; mit englischen Untertiteln z. B. スパチャ → Super Chat, メン限 → members-only, 切り抜き → clip. Nur Begriffe, die im jeweiligen Satz tatsächlich vorkommen, gelangen in den Übersetzungsprompt (höchstens 8 pro Satz).
- **Chinesisch-japanische falsche Freunde** (folgen dem Schalter der eingebauten Begriffe): Ist das Original Chinesisch und sind die Untertitel Japanisch, wird auf Wörter hingewiesen, die im Chinesischen und Japanischen gleich oder ähnlich geschrieben werden, aber etwas anderes bedeuten, z. B. 便當 → 弁当, 節目 → 番組, 老婆 → 妻. Außerdem erhält das Modell bei Chinesisch → Japanisch seine Anweisungen auf Chinesisch, damit es chinesischen Text seltener mit japanischen Bedeutungen liest.
- **Eigene Einträge**: Original, Übersetzung, gültige Untertitelsprache (oder „Alle Sprachen“), höchstens 500 Einträge, für jede Hörsprache; bei gleicher Schreibweise haben sie Vorrang vor den eingebauten Begriffen. Der Abgleich ignoriert Groß-/Kleinschreibung sowie Voll-/Halbbreite.
- CSV-Import/-Export (UTF-8, getrennt durch Komma, Semikolon oder Tab; auch von Excel gespeicherte Dateien werden gelesen). Nach „Glossar speichern“ gilt das Glossar ab dem nächsten Start der Untertitel.

### Transkript exportieren

„Exportieren“ auf der Seite „Transkript“: SRT, VTT, TXT, CSV; als Inhalt wählbar sind „Original und Übersetzung“, „Nur Original“ oder „Nur Übersetzung“. Exportiert werden nur endgültige Untertitel (Live-Untertitel ändern sich noch).
Die Dateien werden unter `Dokumente/kikitori/transcripts/YYYY-MM-DD_HHMMSS_<Modus>.<Endung>` gespeichert; die Zeitstempel zählen ab dem Start der Untertitel.
In den Einstellungen lässt sich „Am Ende jeder Sitzung automatisch speichern (SRT und TXT)“ einschalten; nach „Stoppen“ wartet kikitori auf den endgültigen Untertitel des letzten Satzes (höchstens 2 Sekunden) und speichert dann.

### OBS-Textdatei

Einstellungen, Bereich „Transkripte und OBS“ → „Untertitel in eine Textdatei schreiben (für OBS)“: Original, Übersetzung oder beides.
kikitori überschreibt `Dokumente/kikitori/obs/subtitle.txt` mit den aktuellen Sätzen des Untertitelfensters (UTF-8, atomares Schreiben, höchstens 10-mal pro Sekunde);
fügen Sie in OBS eine Quelle „Text (GDI+)“ hinzu, aktivieren Sie „Aus Datei lesen“ und wählen Sie diese Datei. Wenn die Untertitel enden, nach Leerlauf ausgeblendet oder gelöscht werden, wird die Datei geleert.
Es wird kein Netzwerkport geöffnet, deshalb gibt es keine Lösung über eine OBS-Quelle „Browser“.

### Globale Tastenkürzel

| Windows / Linux | macOS | Aktion |
|---|---|---|
| Ctrl+Alt+K | ⌘+Option+K | Klickdurchlässigkeit des Untertitelfensters umschalten |
| Ctrl+Alt+J | ⌘+Option+J | „Übersetzung anzeigen“ umschalten |
| Ctrl+Alt+U | ⌘+Option+U | Untertitelfenster (und OBS-Textdatei) leeren; das Transkript bleibt unberührt |

Tastenkürzel, die von anderen Programmen belegt sind, werden auf der Seite „Transkript“ als nicht verfügbar gekennzeichnet; die übrigen Tastenkürzel sind davon nicht betroffen. Unter Linux Wayland lassen sich globale Tastenkürzel möglicherweise gar nicht registrieren.

## Datenschutz und Offline-Betrieb

- Ton, Erkennungsergebnisse und Übersetzungen werden nur auf Ihrem Computer verarbeitet. Die Übersetzung geht standardmäßig an das lokale Ollama (`http://localhost:11434/v1`); stellen Sie den Übersetzungsdienst auf eine andere URL um, wird der Untertiteltext dorthin gesendet.
- Netzwerkzugriffe gibt es nur für: Modell-Downloads (Erkennungsmodelle von Hugging Face, Übersetzungsmodelle über Ollama, GPU-Komponenten von PyPI; jeweils erst, wenn Sie auf Starten oder Herunterladen klicken) und die Update-Prüfung beim Start (GitHub Releases). Sind alle Modelle heruntergeladen, funktionieren die Untertitel selbst vollständig offline.
- API-Schlüssel werden nur aus Umgebungsvariablen gelesen (in den Einstellungen wird nur der Variablenname gespeichert, standardmäßig `KIKITORI_TRANSLATE_API_KEY`) und in keine Datei geschrieben.
- „Diagnose kopieren“ auf der Seite „Über“ kopiert nur in die Zwischenablage; Schlüssel, Zugangsdaten in URLs und Benutzernamen werden maskiert, vom Glossar wird nur die Anzahl der Einträge angegeben.
- Engine und Desktop-App kommunizieren nur über Standardein- und -ausgabe; es wird kein Netzwerkport geöffnet.

## Neu in 0.2.0

Zusammenfassung (vollständige Beschreibung und Hinweise zum Upgrade in [docs/release-notes/v0.2.0.md](docs/release-notes/v0.2.0.md)):

- 20 Sprachen frei kombinierbar, 13 Oberflächensprachen, Nur-Original-Anzeige, Furigana für Japanisch
- Neue Modellauswahl für japanische VTuber-Livestreams (GPU: live large-v3-turbo, endgültig large-v3; Übersetzungstemperatur 0)
- Eingebautes Glossar mit Stream-Begriffen und eigene Einträge
- Stabile Live-Untertitel, Pegelanzeige und Latenz, Hinweis bei Erkennungsrückstand
- Export als SRT/VTT/TXT/CSV, OBS-Textdatei, Einstellungen für das Lesetempo
- Fehlende Modelle werden erst beim Start heruntergeladen; Speicherplatz der Modelle anzeigen und Modelle löschen
- Neue Tastenkürzel Ctrl+Alt+J/U, „Diagnose kopieren“
- Genauere Übersetzung Chinesisch → Japanisch (Anweisungen auf Chinesisch und Liste chinesisch-japanischer falscher Freunde), Übersetzungsmodell als Dropdown-Liste, beim Beenden von kikitori wird auch das Übersetzungsmodell im lokalen Ollama entladen, um Grafikspeicher freizugeben
- Schnelleres „Starten“ (empfohlene 12-GB-Kombination mit Übersetzung: direkt nach dem Öffnen ca. 11 → 6 Sekunden, danach ca. 7.8 → 2.4 Sekunden); der Update-Hinweis zeigt die Versionshinweise
- Behoben: Modell blieb auf der GPU, wenn das Öffnen des Geräts fehlschlug; jeder Start der Untertitel belegte ca. 17 MiB zusätzlichen Grafikspeicher; der Speicher des Hauptfensters wuchs bei langem Betrieb stetig

## Bekannte Einschränkungen

- **Vollständig getestet nur unter Windows (RTX 5070)**. Echte Audioaufnahme unter macOS und Linux sowie die Übersetzungsgeschwindigkeit auf Apple Silicon und CPU wurden nicht gemessen; Linux wurde nur in einem Docker-Container abgenommen.
- **Die neuen empfohlenen Kombinationen wurden nur auf der RTX 5070 (12 GB) Ende-zu-Ende gemessen**: Laufen turbo + large-v3 (int8_float16) und qwen3.5:9b gleichzeitig, belegt die gesamte Karte höchstens 10.8 GB, die Latenz der Live-Untertitel liegt bei ca. 0.31 Sekunden, vom Satzende bis zum endgültigen Untertitel bei ca. 0.92 Sekunden (Median, siehe [`tasks/acceptance-report-v0.2.0.md`](tasks/acceptance-report-v0.2.0.md)).
- **Das Evaluationsmaterial für die Erkennungsgenauigkeit stammt nicht von VTubern**: Verwendet wurden allgemeine gesprochene Sprache von YouTube (YODAS), vorgelesene Texte und Fernsehton, jeweils mit beigemischter BGM; Daten von Gesangsstreams, Schreien oder Kollaborationen mehrerer Personen fehlen. Gesang passiert die Sprachaktivitätserkennung und kann zu seltsamen Untertiteln führen.
- Für andere Sprachen als Englisch gibt es nur öffentliche Benchmarks und Halluzinationstests mit Nicht-Sprachaudio, keine Messungen mit echter Sprache; die Liste der Halluzinationsphrasen wurde nicht von Muttersprachlern geprüft.
- Manche Abschlussfloskeln (z. B. „チャンネル登録よろしくお願いします“) werden als Whisper-Halluzination komplett herausgefiltert, auch wenn sie im Stream tatsächlich gesagt werden.
- Zum Übersetzen muss Ollama separat installiert werden; bei anderen OpenAI-kompatiblen Servern als Ollama müssen Sie die Modelle selbst installieren.
- Speicher-Langzeittests wurden unter Windows durchgeführt (Engine: 20-Minuten-Sitzung stabil, Hauptfenster: 22 Minuten, +0.7 MiB/min); macOS und Linux wurden nicht getestet.
- Es kann immer nur eine Untertitelsitzung gleichzeitig laufen; wird ein Gerät abgezogen, wird nicht automatisch neu verbunden.
- Nicht unterstützt: Wörter per Klick nachschlagen, Ton nur einer einzelnen App aufnehmen, Gesangsstream-Modus, Sprechertrennung, OBS-Quelle „Browser“ (Gründe siehe Abschnitt „Vorerst nicht umgesetzt“ („先不做“) im chinesischsprachigen Dokument [`docs/specs/competitive-features.md`](docs/specs/competitive-features.md) und [`docs/adr/`](docs/adr/)).
- Die Installer haben keine kostenpflichtige Codesignatur (Windows SmartScreen und macOS Gatekeeper zeigen Warnungen).

## Lizenzen

kikitori selbst wird unter der MIT-Lizenz veröffentlicht ([LICENSE](LICENSE)). Die vollständigen Lizenztexte der Drittkomponenten liegen im Installationsverzeichnis und lassen sich über die Seite „Über“ öffnen.

Modelle werden nicht mit dem Installer verteilt; wenn Sie sie herunterladen, gelten die jeweiligen Lizenzen:

| Modell | Lizenz | Hinweis |
|---|---|---|
| Whisper (CTranslate2-Konvertierung) | MIT | — |
| Silero VAD (mitgeliefert) | MIT | — |
| Qwen3.5 (Standard-Übersetzungsmodell) | Apache-2.0 | — |
| Tencent HY-MT1.5 | Tencent HY Community License | **Gilt nicht in der EU, im Vereinigten Königreich und in Südkorea**; kikitori empfiehlt es standardmäßig nicht, die Region muss in den Einstellungen bestätigt werden |
| Sakura-GalTransl | CC BY-NC-SA 4.0 | **Kommerzielle Nutzung verboten**; wird nie empfohlen, nur manuell wählbar |
| NVIDIA cuBLAS | NVIDIA CUDA EULA | Zustimmung vor dem Download erforderlich |

## Für Entwickler

- Architektur: [docs/architecture.md](docs/architecture.md); Modulspezifikationen: [docs/specs/](docs/specs/); Lernleitfaden: [docs/learning/](docs/learning/README.md)
- Evaluierungen und Experimente: `experiments/*/README.md`; Untersuchungsberichte: `reports/`

```text
uv sync                                       Python-Abhängigkeiten installieren
uv run python -m unittest discover -s tests   Python-Tests
uv run ruff check                             Lint
uv run kikitori                               Interaktives Menü (Engine starten, Audiogeräte auflisten …)
cd app && npm ci && npm run menu              Entwicklermenü der Desktop-App (benötigt Node und Rust)
cd app && npm test && npx tsc --noEmit        Frontend-Tests und Typprüfung
```
