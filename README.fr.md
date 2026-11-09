# kikitori（聞き取り）

[繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md) | [English](README.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | **Français** | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md) | [Русский](README.ru.md) | [Tiếng Việt](README.vi.md) | [ไทย](README.th.md) | [Bahasa Indonesia](README.id.md)

*Ceci est une traduction du [README.zh-TW.md](README.zh-TW.md) en chinois traditionnel. En cas de divergence, README.zh-TW.md fait foi. Traduit avec l’aide de l’IA ; les corrections sont les bienvenues via les [issues](https://github.com/alianlbj23/kikitori/issues).*

kikitori écoute le son que votre ordinateur est en train de lire (ou le micro), le transcrit en temps réel, le traduit dans la langue de votre choix et l’affiche sous forme de sous-titres bilingues flottant à l’écran.
La reconnaissance vocale (Whisper) et la traduction (Ollama en local) s’exécutent sur votre propre ordinateur : le son et le texte ne sont envoyés à aucun service cloud.

Le cas d’usage principal est **regarder des lives de VTubers en japonais** (avec BGM, effets sonores de jeux et vocabulaire propre aux lives) ; les modèles recommandés et les paramètres ont été réglés d’après les mesures de ce projet.
Toute autre combinaison entre les 20 langues fonctionne aussi.

- Plateformes : Windows 10/11 x64 (principale plateforme testée), Linux x64, macOS Apple Silicon
- Licence : MIT (les modèles ont leurs propres licences, voir « Licence »)
- Version actuelle : 0.3.1 ([notes de version](docs/release-notes/v0.3.1.md))

## Fonctionnalités

| Fonctionnalité | Description |
|---|---|
| Traduction de vidéos | Capture le son lu par le système (Windows WASAPI loopback / Linux PulseAudio monitor / macOS ScreenCaptureKit) et traduit la langue écoutée dans la langue des sous-titres |
| Interprétation en direct | Capture le micro ; quand vous parlez dans « Ma langue », c’est traduit dans la « Langue de l’interlocuteur », et quand votre interlocuteur parle, c’est traduit dans la vôtre |
| Sous-titres en deux passes : rapides d’abord, précis ensuite | Pendant que la phrase est encore prononcée, un modèle plus rapide produit des « sous-titres en direct » ; une fois la phrase finie, un modèle plus précis recalcule des « sous-titres définitifs » qui les remplacent |
| Sous-titres en direct stables | Dans l’original comme dans la traduction du sous-titre en direct, le début qui ne changera plus s’affiche normalement et la fin qui change encore s’affiche estompée ; les nouveaux mots apparaissent en fondu et le bloc ne saute plus de haut en bas |
| Étiquettes de locuteur | Lors des collaborations à plusieurs, indique « Locuteur 1, 2… » devant chaque phrase ; le nombre de locuteurs peut être détecté automatiquement ou fixé (2–6), et un clic sur une étiquette permet de la renommer ; en mode interprétation, indique « Vous / L’autre personne » |
| Toute combinaison de 20 langues | La langue écoutée (« Détecter automatiquement » possible) et la langue des sous-titres se choisissent parmi 20 langues, voir « Langues prises en charge » |
| 13 langues d’interface | Suit le système d’exploitation par défaut ; modifiable sur la page Paramètres |
| N’afficher que l’original | Désactivez « Afficher la traduction » pour ne voir que l’original : pas besoin d’Ollama, le GPU n’est pas utilisé ; modifiable aussi en cours d’exécution |
| Furigana | Affiche la lecture au-dessus des kanji de l’original japonais (SudachiDict, environ 69 Mio, téléchargé uniquement la première fois que vous l’activez) |
| Modèles recommandés selon le matériel | Détecte le CPU, la mémoire, la carte graphique NVIDIA et Ollama, et choisit automatiquement une combinaison de modèles ; les modèles manquants ne sont téléchargés que la première fois que vous cliquez sur « Démarrer » |
| Glossaire | 53 termes intégrés du vocabulaire des lives japonais (赤スパ, 推し, メン限…) et des mots sino-japonais faciles à confondre pour le chinois vers le japonais ; vous pouvez aussi ajouter vos propres entrées et importer/exporter en CSV |
| Corrections de la reconnaissance et infos du live | Corrige automatiquement les mots sur lesquels la reconnaissance se trompe souvent ; renseignez le nom du streamer, le nom du jeu ou la description du live pour que les noms soient toujours traduits pareil |
| Export des transcriptions | SRT, VTT, TXT, CSV ; enregistrement automatique configurable à la fin de chaque session |
| Fichier texte pour OBS | Écrit les sous-titres en cours dans un fichier texte, à utiliser avec l’option « Lire à partir d’un fichier » d’une source OBS « Texte (GDI+) » ; aucun port réseau n’est ouvert |
| Fenêtre des sous-titres | Transparente, toujours au premier plan, laisse passer les clics ; taille du texte, fond, position, nombre de phrases, durée d’affichage minimale de chaque phrase, délai d’inactivité avant le fondu, taille et position de l’original et largeur de ligne sont réglables ; mise en pause et rappel des sous-titres récents possibles |
| Barre d’état | Vumètre, latence (médiane entre la fin de la parole et le sous-titre définitif) ; avertit quand la reconnaissance n’arrive pas à suivre, quand il n’y a aucun son pendant 15 secondes ou quand le son de fond est trop fort |
| Gestion des modèles | Télécharge les modèles de reconnaissance, affiche l’espace disque utilisé et les supprime ; télécharge pour vous les modèles de traduction dans Ollama |
| Mise à jour automatique | Au démarrage, vérifie GitHub Releases, télécharge la mise à jour, vérifie sa signature puis l’installe |

## Langues prises en charge

Langues écoutées et langues des sous-titres (20, combinables librement) :

| | | | | |
|---|---|---|---|---|
| 日本語 | English | 繁體中文（台灣） | 简体中文 | 한국어 |
| Español | Français | Deutsch | Português | Русский |
| Italiano | العربية | हिन्दी | Bahasa Indonesia | Tiếng Việt |
| ไทย | Türkçe | Polski | Nederlands | Українська |

- Les sous-titres en chinois traditionnel sont convertis avec OpenCC `s2twp` vers les caractères traditionnels standard de Taïwan et l’usage lexical taïwanais (p. ex. 视频 → 影片). La conversion n’a lieu que si la traduction contient des caractères simplifiés, et elle ne remplace pas 「台灣」 par 「臺灣」.
- Quand la langue écoutée et celle des sous-titres sont identiques, rien n’est traduit (entre chinois traditionnel et simplifié, seule l’écriture est convertie).
- La qualité de la reconnaissance vocale dépend de la langue. Quand le taux d’erreur d’un modèle pour une langue dépasse 20 % (p. ex. thaï, arabe et hindi avec large-v3-turbo, vietnamien avec large-v3), les notes de la recommandation signalent que ce modèle reconnaît mal cette langue ; pour le thaï, l’arabe et le hindi, sur les cartes graphiques de 8 Go ou plus, les sous-titres en direct passent aussi à large-v3.

Langues de l’interface (13) : 繁體中文（台灣）, 简体中文, English, 日本語, 한국어, Español, Français, Deutsch, Português (Brasil), Русский, Tiếng Việt, ไทย, Bahasa Indonesia.
L’arabe peut servir de langue écoutée ou de langue des sous-titres, mais il n’y a pas d’interface en arabe (l’interface ne prend pas en charge les mises en page de droite à gauche).

## Combinaisons recommandées (selon le matériel)

kikitori les détecte et les applique automatiquement ; le tableau sert seulement à savoir ce que vous obtiendrez. Elles reposent sur les évaluations de ce projet (`experiments/vtuber_eval/`, `experiments/translate_eval/`) ; les règles et formules sont dans [`docs/specs/hardware.md`](docs/specs/hardware.md).

| Votre ordinateur | Sous-titres en direct | Sous-titres définitifs | Traduction (Ollama) |
|---|---|---|---|
| NVIDIA 16 Go ou plus | large-v3-turbo (float16) | large-v3 (float16) | qwen3.5:9b |
| NVIDIA 12 Go (p. ex. RTX 5070) | large-v3-turbo (int8_float16) | large-v3 (int8_float16) | qwen3.5:9b |
| NVIDIA 8–11 Go | large-v3-turbo (small si la VRAM est pile au minimum de 8 Go) | large-v3 (précision choisie selon la VRAM) | qwen3.5:4b |
| NVIDIA 4–8 Go | base | large-v3-turbo | qwen3.5:4b |
| NVIDIA moins de 4 Go | base | small | qwen3.5:4b |
| Pas de GPU NVIDIA, RAM ≥ 16 Go, 5 cœurs ou plus | base (CPU) | small (CPU) | qwen3.5:4b |
| Pas de GPU NVIDIA, moins de RAM ou 4 cœurs ou moins | tiny (CPU) | base (CPU) | qwen3.5:4b |
| Mac (Apple Silicon) | base (CPU) | small (CPU) | Selon la taille de la mémoire unifiée (qwen3.5:9b avec 16 Go) |

- D’après nos mesures, les précisions int8_float16 et float16 donnent la même qualité de reconnaissance (différence non significative), mais int8_float16 utilise environ moitié moins de VRAM ; quand la carte graphique est partagée avec le modèle de traduction, kikitori préfère laisser le modèle de traduction prendre une taille au-dessus.
- Taille de téléchargement des modèles de reconnaissance : large-v3-turbo 1,62 Go, large-v3 3,09 Go, small 487 Mo, base 148 Mo, tiny 78 Mo. Modèles de traduction : qwen3.5:9b environ 6,6 Go, qwen3.5:4b environ 3,3 Go.
- Les GPU NVIDIA ont besoin de cuBLAS 12 (environ 570 Mo). kikitori ne l’inclut pas dans l’installateur ; il se télécharge en un clic sur la page Modèles après acceptation de la NVIDIA EULA. Sans lui, le CPU est utilisé.
- Quand le serveur de traduction est sur un autre ordinateur (distant), aucun modèle de traduction n’est choisi automatiquement ; choisissez-le vous-même sur la page Modèles.
- Pour la traduction, Qwen3.5 (Apache-2.0) est toujours recommandé en premier : 9B si la VRAM suffit, sinon 4B. Avec une carte graphique de 4 Go ou moins, ou sans GPU NVIDIA, c’est aussi qwen3.5:4b ; Ollama exécute sur le CPU la partie qui ne tient pas, ce qui est plus lent (la latence de traduction dans ce cas n’a pas encore été mesurée).
- Tencent HY-MT a des restrictions géographiques et ne peut être recommandé qu’après avoir coché, sur la page Paramètres, la confirmation que vous ne l’utilisez pas dans l’UE, au Royaume-Uni ni en Corée du Sud ; Sakura (licence non commerciale) n’est jamais recommandé et ne peut être choisi que manuellement.
- La vitesse de reconnaissance sous macOS et Linux et la vitesse de traduction sur CPU n’ont pas encore été mesurées (voir « Limitations connues »).

## Prise en main

1. Téléchargez l’installateur depuis [GitHub Releases](https://github.com/alianlbj23/kikitori/releases) (les consignes d’installation de chaque plateforme figurent sur la page de la Release : avertissements SmartScreen/Gatekeeper dus à l’absence de signature, fuse3 et polices sous Linux).
2. Pour traduire, installez [Ollama](https://ollama.com/download) et laissez-le tourner en arrière-plan. Si kikitori ne le détecte pas, il propose « Installer Ollama » ou « N’afficher que l’original ».
3. Ouvrez kikitori. Au premier lancement, la combinaison recommandée pour votre matériel est appliquée automatiquement.
4. Dans « Langues » sur la page Paramètres, choisissez la langue écoutée et la langue des sous-titres. Ce choix affiche seulement la combinaison recommandée et la taille de téléchargement ; **rien n’est téléchargé tout de suite**.
5. Choisissez la source audio et cliquez sur « Démarrer ». Les modèles de reconnaissance et de traduction (avec Ollama) manquants sont d’abord téléchargés, puis les sous-titres démarrent automatiquement.

### N’afficher que l’original

C’est l’interrupteur « Afficher la traduction » de la barre d’outils de la page Transcription et de la section « Sous-titres » de la page Paramètres (ou le raccourci Ctrl+Alt+J). Quand il est désactivé :

- le moteur ne se connecte pas du tout au serveur de traduction, Ollama n’est pas nécessaire et le GPU n’est pas occupé par un modèle de traduction ;
- la fenêtre des sous-titres affiche l’original avec la taille et la couleur du sous-titre principal ;
- un changement en cours d’exécution s’applique immédiatement ; les phrases déjà affichées ne sont pas traduites après coup.

### Furigana

« Afficher les furigana sur l'original japonais » sur la page Paramètres : quand la langue écoutée est le japonais, la lecture en hiragana s’affiche au-dessus des kanji de l’original (les okurigana ne sont pas annotés une seconde fois). La première activation télécharge SudachiDict core (Apache-2.0, environ 69 Mio) ; la page Modèles permet de voir l’espace utilisé et de le supprimer. Seul l’affichage est concerné ; les sous-titres exportés ne changent pas. Conception : [`docs/specs/furigana.md`](docs/specs/furigana.md).

### Glossaire

« Glossaire » sur la page Paramètres :

- **Vocabulaire de live intégré** (activé par défaut) : utilisé quand l’original est en japonais et les sous-titres en chinois traditionnel, chinois simplifié, anglais ou coréen ; par exemple, avec des sous-titres en anglais, スパチャ → Super Chat, メン限 → members-only, 切り抜き → clip. Seuls les termes qui apparaissent réellement dans l’original de la phrase sont ajoutés au prompt de traduction (8 au maximum par phrase).
- **Mots sino-japonais faciles à confondre** (suivent l’interrupteur de la liste intégrée) : quand l’original est en chinois et les sous-titres en japonais, le prompt signale les mots qui s’écrivent de la même façon ou presque en chinois et en japonais mais n’ont pas le même sens, p. ex. 便當 → 弁当, 節目 → 番組, 老婆 → 妻 ; pour le chinois vers le japonais, les instructions données au modèle sont en outre rédigées en chinois, pour qu’il lise moins le chinois avec le sens japonais des mots.
- **Entrées personnalisées** : original, traduction et langue des sous-titres concernée (ou « Toutes les langues »), 500 entrées au maximum, valables pour toute langue écoutée ; pour une même graphie, elles priment sur la liste intégrée. La comparaison ignore la casse et la différence entre pleine chasse et demi-chasse.
- Import/export en CSV possible (UTF-8, séparateur virgule, point-virgule ou tabulation ; les fichiers enregistrés par Excel sont lus aussi). Après un clic sur « Enregistrer le glossaire », les changements s’appliquent au prochain démarrage des sous-titres.

### Export des transcriptions

« Exporter » sur la page Transcription : SRT, VTT, TXT, CSV, avec pour contenu « Original et traduction », « Original seulement » ou « Traduction seulement ». Seuls les sous-titres définitifs sont exportés (les sous-titres en direct changent encore).
Les fichiers sont enregistrés dans `Documents/kikitori/transcripts/YYYY-MM-DD_HHMMSS_<mode>.<extension>`, et les timecodes partent du démarrage des sous-titres.
Sur la page Paramètres, vous pouvez activer « Enregistrer automatiquement à la fin de chaque session (SRT et TXT) » ; après un clic sur « Arrêter », l’enregistrement attend le sous-titre définitif de la dernière phrase (2 secondes au maximum).

### Fichier texte pour OBS

Page Paramètres, « Transcriptions et OBS » → « Écrire les sous-titres dans un fichier texte (pour OBS) » : original, traduction ou les deux.
kikitori écrase `Documents/kikitori/obs/subtitle.txt` avec les phrases affichées à ce moment dans la fenêtre des sous-titres (UTF-8, écriture atomique, 10 fois par seconde au maximum) ;
dans OBS, ajoutez une source « Texte (GDI+) », cochez « Lire à partir d’un fichier » et pointez-la vers ce fichier. Le fichier est vidé quand les sous-titres s’arrêtent, s’estompent après inactivité ou sont effacés.
Aucun port réseau n’est ouvert, il n’y a donc pas de méthode passant par une source OBS « Navigateur web ».

### Raccourcis globaux

| Windows / Linux | macOS | Action |
|---|---|---|
| Ctrl+Alt+K | ⌘+Option+K | Basculer « Laisser passer les clics » pour la fenêtre des sous-titres |
| Ctrl+Alt+J | ⌘+Option+J | Basculer « Afficher la traduction » |
| Ctrl+Alt+U | ⌘+Option+U | Effacer la fenêtre des sous-titres (et le fichier texte pour OBS) ; la transcription n’est pas affectée |
| Ctrl+Alt+P | ⌘+Option+P | Mettre en pause / reprendre la fenêtre des sous-titres ; la transcription continue normalement |
| Ctrl+Alt+H | ⌘+Option+H | Afficher un instant les 10 dernières phrases dans la fenêtre des sous-titres |
| Ctrl+Alt+R | ⌘+Option+R | En interprétation, afficher « Pouvez-vous répéter ? » dans la langue de l’interlocuteur dans la fenêtre des sous-titres |

Un raccourci déjà pris par un autre programme est signalé comme non disponible sur la page Transcription, sans effet sur les autres raccourcis. Sous Linux avec Wayland, il est possible qu’aucun raccourci global ne puisse être enregistré.

## Confidentialité et hors ligne

- Le son, les résultats de la reconnaissance et les traductions sont traités uniquement sur votre ordinateur. Par défaut, la traduction est envoyée à l’Ollama local (`http://localhost:11434/v1`) ; si vous changez le service de traduction pour une autre URL, le texte des sous-titres y est envoyé.
- Les seuls accès réseau sont : le téléchargement des modèles (modèles de reconnaissance depuis Hugging Face, modèles de traduction via Ollama, composants GPU depuis PyPI, toujours seulement quand vous cliquez sur Démarrer ou sur télécharger) et la vérification des mises à jour au démarrage (GitHub Releases). Une fois les modèles téléchargés, les sous-titres eux-mêmes fonctionnent entièrement hors ligne.
- La clé API est lue uniquement depuis une variable d’environnement (les paramètres ne stockent que le nom de la variable, `KIKITORI_TRANSLATE_API_KEY` par défaut) et n’est écrite dans aucun fichier.
- « Copier le diagnostic » sur la page « À propos » copie seulement dans le presse-papiers ; les clés, les identifiants contenus dans les URL et les noms d’utilisateur sont masqués, et le glossaire n’est indiqué que par son nombre d’entrées.
- Le moteur et l’application de bureau communiquent uniquement par l’entrée et la sortie standard ; aucun port réseau n’est ouvert.

## Nouveautés de la 0.3.0

Résumé (détails complets et remarques de mise à niveau dans [docs/release-notes/v0.3.0.md](docs/release-notes/v0.3.0.md) ; pour la 0.2.0, voir [docs/release-notes/v0.2.0.md](docs/release-notes/v0.2.0.md)) :

- Étiquettes de locuteur : lors des collaborations à plusieurs, chaque phrase est marquée « Locuteur 1, 2… » ; nombre détecté automatiquement ou fixé à 2–6 personnes ; cliquez sur une étiquette pour la renommer, les étiquettes portant le même nom sont fusionnées (modèle d’environ 27 Mio, téléchargé la première fois que vous le choisissez)
- La traduction en direct est séparée en une partie stable et une partie qui change, la phrase entière ne clignote plus ; les nouveaux mots apparaissent en fondu et les sous-titres ne sautent plus de haut en bas
- Le mode interprétation indique « Vous / L’autre personne » ; Ctrl+Alt+R affiche « Pouvez-vous répéter ? » dans la langue de l’interlocuteur
- Règles de correction de la reconnaissance, ajout au glossaire de mots sélectionnés dans la transcription, infos du live (nom du streamer, nom du jeu, description du live)
- Mise en page de lecture : taille et position de l’original, largeur de ligne, durée d’affichage allongée selon la longueur, furigana seulement à la première occurrence
- Nouveaux raccourcis Ctrl+Alt+P pour mettre les sous-titres en pause et Ctrl+Alt+H pour revoir les sous-titres récents ; réinitialisation de la position de la fenêtre des sous-titres ; avertissement quand le son de fond est trop fort

## Limitations connues

- **Testé complètement uniquement sous Windows (RTX 5070)**. La capture sur machines réelles sous macOS et Linux, ainsi que la vitesse de traduction sur Apple Silicon et sur CPU, n’ont pas été mesurées ; Linux n’a été validé que dans un conteneur Docker.
- **La nouvelle combinaison recommandée n’a été mesurée de bout en bout que sur une RTX 5070 (12 Go)** : avec turbo + large-v3 (int8_float16) et qwen3.5:9b en même temps, la carte entière atteint au maximum 10,8 Go, la latence des sous-titres en direct est d’environ 0,31 seconde et il faut environ 0,92 seconde entre la fin de la parole et le sous-titre définitif (médianes, voir [`tasks/acceptance-report-v0.2.0.md`](tasks/acceptance-report-v0.2.0.md)).
- **Le corpus d’évaluation de la précision de la reconnaissance n’est pas du contenu VTuber** : il s’agit de parole courante sur YouTube (YODAS), de lecture à voix haute et d’audio télévisé, avec de la BGM mélangée ; il n’y a pas de données de lives chantés, de cris ni de collaborations à plusieurs. Le chant passe à travers la détection de voix et peut donner des sous-titres étranges.
- **Les étiquettes de locuteur n’ont été mesurées que sur des extraits YouTube assemblés** (environ 92–100 % d’étiquettes correctes à 2 personnes, environ 80–85 % à 3–4) : il n’y a pas de données avec des personnes qui se coupent la parole, des acquiescements de moins de 1 seconde ni une même personne qui change de voix, donc les résultats en collaboration réelle peuvent être plus bas ; les étiquettes ne sont pas écrites dans le fichier texte pour OBS.
- Les langues autres que l’anglais n’ont que des bancs d’essai publics et des tests d’hallucination sur du non-vocal, sans mesure sur de la parole réelle ; la liste des phrases d’hallucination n’a pas été relue par des locuteurs natifs.
- Certaines formules de fin toutes faites (p. ex. 「チャンネル登録よろしくお願いします」) sont filtrées comme des hallucinations de Whisper et la phrase entière est supprimée, même si le streamer l’a vraiment dite.
- La traduction nécessite d’installer Ollama séparément ; avec un serveur compatible OpenAI autre qu’Ollama, vous devez installer les modèles vous-même.
- Les tests de charge mémoire de longue durée ont été faits sous Windows (moteur stable sur une session de 20 minutes, fenêtre principale +0,7 Mio/min sur 22 minutes) ; macOS et Linux n’ont pas été testés.
- Une seule session de sous-titres peut tourner à la fois ; après le débranchement d’un périphérique, la reconnexion n’est pas automatique.
- Non pris en charge : recherche de mots au clic, capture du son d’une seule application, mode lives chantés, séparation des locuteurs qui changent au milieu d’une phrase ou parlent en même temps, source OBS « Navigateur web » (les raisons figurent dans la section « Pas pour l’instant » (「先不做」) de [`docs/specs/competitive-features.md`](docs/specs/competitive-features.md), document en chinois, et dans [`docs/adr/`](docs/adr/)).
- Les installateurs n’ont pas de signature de code payante (Windows SmartScreen et macOS Gatekeeper afficheront un avertissement).

## Licence

kikitori est publié sous licence MIT ([LICENSE](LICENSE)). Le texte complet des licences des composants tiers se trouve dans le répertoire d’installation et peut être ouvert depuis la page « À propos ».

Les modèles ne sont pas distribués avec l’installateur ; leur propre licence s’applique quand vous les téléchargez :

| Modèle | Licence | Remarques |
|---|---|---|
| Whisper (version convertie pour CTranslate2) | MIT | — |
| Silero VAD (fourni avec le programme) | MIT | — |
| 3D-Speaker CAM++ (étiquettes de locuteur, téléchargé si choisi) | Apache-2.0 | — |
| Qwen3.5 (modèle de traduction par défaut) | Apache-2.0 | — |
| Tencent HY-MT1.5 | Tencent HY Community License | **Ne s’applique pas dans l’UE, au Royaume-Uni ni en Corée du Sud** ; kikitori ne le recommande pas par défaut, il faut confirmer votre région sur la page Paramètres |
| Sakura-GalTransl | CC BY-NC-SA 4.0 | **Usage commercial interdit** ; jamais recommandé, ne peut être choisi que manuellement |
| NVIDIA cuBLAS | NVIDIA CUDA EULA | À accepter avant le téléchargement |

## Pour les développeurs

- Architecture : [docs/architecture.md](docs/architecture.md) ; spécifications des modules : [docs/specs/](docs/specs/) ; guide d’apprentissage : [docs/learning/](docs/learning/README.md)
- Évaluations et expériences : `experiments/*/README.md` ; rapports d’étude : `reports/`

```text
uv sync                                       Installer les dépendances Python
uv run python -m unittest discover -s tests   Tests Python
uv run ruff check                             Lint
uv run kikitori                               Menu interactif (démarrer le moteur, lister les périphériques audio…)
cd app && npm ci && npm run menu              Menu de développement de l’application de bureau (nécessite Node et Rust)
cd app && npm test && npx tsc --noEmit        Tests du frontend et vérification des types
```
