/**
 * French UI dictionary (vouvoiement).
 *
 * Role: one interface locale, typed as `Messages`, so it must have exactly the
 * base (zh-TW) keys and the plural categories French uses (one/many/other).
 * Typography: a no-break space (U+00A0) precedes : ; ? ! % and pads « ».
 */

import type { Messages } from "./types";

export const fr: Messages = {
  // ------------------------------------------------------------ common
  "common.unknown": "Inconnu",
  "common.unknownReason": "raison inconnue",
  "common.unknownCause": "cause inconnue",
  "common.auto": "Automatique",
  "common.loading": "Chargement",
  "common.saving": "Enregistrement",
  "common.redownload": "Télécharger à nouveau",
  "common.recommendedFor": "Recommandé : {roles}",
  "common.parenthetical": "{text} ({note})",
  "common.withReason": "{context} : {reason}",
  "common.listSeparator": ", ",
  "common.clauseSeparator": ", ",
  "common.strongSeparator": " ; ",
  "common.altSeparator": " / ",
  "common.sentenceEnd": ".",
  "common.approxGb": "environ {gb} Go",

  // ------------------------------------------------------------ rail
  "rail.mode": "Mode",
  "rail.device": "Source audio",
  "mode.media.name": "Traduction de vidéos",
  "mode.media.note": "{source} vers {target}",
  "mode.media.noteAuto": "Toute langue détectée vers {target}",
  "mode.media.noteOriginal": "{source} (original uniquement)",
  "mode.interpret.noteOriginal": "{own} ⇄ {partner} (original uniquement)",
  "mode.interpret.name": "Interprétation en direct",
  "mode.interpret.note": "{own} ⇄ {partner}",
  "device.hint.media": "La traduction de vidéos capture le son que votre ordinateur est en train de lire.",
  "device.hint.interpret": "L’interprétation en direct utilise le microphone.",
  "device.hint.missing":
    "Le périphérique choisi précédemment n’est pas disponible ; le périphérique par défaut du système sera utilisé.",
  "device.defaultNamed": "Par défaut : {name}",
  "device.systemDefault": "Par défaut du système",
  "start.start": "Démarrer",
  "start.stop": "Arrêter",
  "nav.label": "Pages",
  "nav.transcript": "Transcription",
  "nav.models": "Modèles",
  "nav.settings": "Paramètres",
  "engine.restart": "Redémarrer le moteur",
  "engine.failedNotice": "Le moteur n’a pas pu démarrer : {reason}",
  "engine.log": "Message du moteur : {message}",
  // ------------------------------------------------------------ engine message codes (hardware.md; params localized in lib/messages.ts)
  "engine.asr_gpu":
    "{gpu_name} ({vram_mb} Mio de VRAM) : {fast_model} pour les sous-titres en direct, {final_model} pour les sous-titres définitifs ({compute_type})",
  "engine.asr_gpu_int8_saves_vram":
    "int8_float16 divise par deux la VRAM utilisée par la reconnaissance vocale et en laisse davantage au modèle de traduction",
  "engine.asr_final_no_speech_unreliable":
    "{final_model} ne distingue pas la parole du silence (no_speech) ; le no_speech de {fast_model} et le détecteur de voix filtrent à la place ce qui n’est pas de la parole",
  "engine.asr_large_v3_remote_translation":
    "La traduction ne s’exécute pas sur cet ordinateur, donc les sous-titres définitifs utilisent {final_model} (qualité similaire, no_speech fiable, environ 0,33 s par phrase)",
  "engine.asr_apple_silicon":
    "Apple Silicon : sous macOS, la reconnaissance vocale s’exécute sur le CPU ; {fast_model} pour les sous-titres en direct, {final_model} pour les sous-titres définitifs",
  "engine.asr_apple_silicon_untested":
    "La latence sur Apple Silicon n’a pas encore été mesurée ; mêmes modèles que pour un CPU avec 16 Go de RAM ou plus",
  "engine.asr_cpu":
    "La reconnaissance vocale s’exécute sur le CPU (16 Go de RAM ou plus) : {fast_model} pour les sous-titres en direct, {final_model} pour les sous-titres définitifs",
  "engine.asr_cpu_large_models_slow":
    "Les modèles medium et plus grands demandent 3,6 s ou plus par phrase sur un CPU, trop lent pour des sous-titres en direct",
  "engine.asr_cpu_low":
    "Des modèles légers limitent la latence et la mémoire : {fast_model} pour les sous-titres en direct, {final_model} pour les sous-titres définitifs",
  "engine.cpu_low_ram": "{ram_gb} Go de RAM, soit moins de 16 Go",
  "engine.cpu_few_cores": "{physical_cores} cœurs physiques de CPU (au plus {max_cores})",
  "engine.cpu_info_incomplete": "Les informations matérielles sont incomplètes",
  "engine.multiple_gpus":
    "{gpu_count} GPU NVIDIA détectés ; utilisation de {gpu_name}, celui qui a le plus de VRAM",
  "engine.cuda_platform_unsupported":
    "CTranslate2 n’a pas de version CUDA pour cette plateforme (Windows et Linux x86-64 uniquement) ; la reconnaissance vocale utilise le CPU",
  "engine.cuda_driver_too_old":
    "Le pilote NVIDIA {driver_version} est trop ancien (CUDA 12 requiert {min_driver} ou ultérieur) ; la reconnaissance vocale utilise le CPU jusqu’à la mise à jour du pilote",
  "engine.cuda_compute_capability_unknown":
    "Impossible de lire la capacité de calcul du GPU (le pilote est peut-être trop ancien) ; la reconnaissance vocale utilise le CPU",
  "engine.cuda_compute_capability_unsupported":
    "{gpu_name} (capacité de calcul {compute_capability}) ne prend pas en charge l’accélération float16/int8 de CTranslate2 ; la reconnaissance vocale utilise le CPU",
  "engine.gpu_runtime_missing":
    "cuBLAS n’est pas installé : l’accélération GPU nécessite le téléchargement du runtime GPU (environ 570 Mo) ; en attendant, la reconnaissance vocale utilise le CPU",
  "engine.asr_vram_insufficient":
    "VRAM insuffisante pour les modèles de reconnaissance vocale (environ {need_mb} Mio nécessaires) ; la reconnaissance vocale utilise le CPU",
  "engine.memory_unknown":
    "Impossible de lire la quantité de mémoire ; utilisation des modèles de reconnaissance vocale les plus légers",
  "engine.asr_low_quality_model": "{model} fait nettement plus d’erreurs de reconnaissance",
  "engine.asr_language_quality_low":
    "{model} reconnaît mal la langue suivante : {language} (taux d’erreur supérieur à 20 % sur des bancs d’essai publics)",
  "engine.translation_remote":
    "Le serveur de traduction n’est pas sur cet ordinateur ({base_url}) ; choisissez l’un de ses modèles dans la combinaison personnalisée de la page Modèles",
  "engine.translation_cpu":
    "Aucun GPU utilisable : le modèle de traduction s’exécute sur le CPU, le plus petit modèle est donc choisi (vitesse sur CPU non mesurée)",
  "engine.translation_budget_vram":
    "Il reste environ {budget_gb} Go de VRAM pour le modèle de traduction après la reconnaissance vocale et une réserve de 1 Go",
  "engine.translation_budget_unified":
    "Il reste environ {budget_gb} Go de mémoire unifiée pour le modèle de traduction après la reconnaissance vocale et la réserve du système",
  "engine.translation_same_model":
    "Les traductions en direct et définitive utilisent le même modèle : chaque phrase prend moins de 0,3 s sur un GPU, alors que changer de modèle dans Ollama prend de 1,5 à 18 s",
  "engine.translation_different_models": "Les traductions en direct et définitive utilisent des modèles différents",
  "engine.translation_better_model_available":
    "{model} a été choisi parmi les modèles installés ; installer {better_model} donnerait une meilleure qualité",
  "engine.translation_not_needed":
    "{source_language} et {target_language} sont la même langue parlée : aucun modèle de traduction n’est nécessaire, seulement une conversion d’écriture",
  "engine.source_language_auto":
    "La langue écoutée est détectée automatiquement : le modèle de traduction est choisi pour traduire n’importe quelle langue vers {target_language}",
  "engine.translation_server_unreachable":
    "Impossible de se connecter au serveur de traduction {base_url} (Ollama est-il lancé ?) ; les modèles installés ne peuvent pas être vérifiés",
  "engine.translation_model_list_unavailable":
    "Impossible d’obtenir la liste des modèles du serveur de traduction : {error}",
  "engine.low_ram_for_translation":
    "Moins de 8 Go de RAM : le modèle de traduction et la reconnaissance vocale risquent de ne pas tenir ensemble en mémoire",
  "engine.translation_model_not_installed": "Le modèle de traduction recommandé {model} n’est pas encore installé",
  "engine.translation_model_exceeds_budget":
    "{model} demande environ {model_gb} Go, plus que les {budget_gb} Go disponibles ; Ollama en exécutera une partie sur le CPU, ce qui est plus lent",
  "engine.translation_unsupported":
    "Aucun modèle de traduction recommandé ne prend en charge {source_language} → {target_language}",
  "engine.combination_not_object": "La combinaison de modèles doit être un objet",
  "engine.asr_path_not_object": "{path} doit être un objet",
  "engine.asr_model_unknown":
    "{path} : modèle de reconnaissance vocale inconnu {model} (disponibles : {available})",
  "engine.asr_device_invalid": "{path} : le périphérique doit être automatique, CPU ou CUDA",
  "engine.asr_compute_type_not_string": "{path} : la précision doit être du texte ou vide",
  "engine.asr_cpu_threads_invalid": "{path} : les threads CPU doivent être un nombre entier de 0 à 256",
  "engine.asr_cuda_no_gpu": "{path} : CUDA est sélectionné, mais aucun GPU NVIDIA n’a été trouvé",
  "engine.asr_cuda_platform_unsupported":
    "{path} : CUDA est sélectionné, mais CTranslate2 n’a pas de version CUDA pour cette plateforme (Windows et Linux x86-64 uniquement)",
  "engine.asr_cuda_driver_too_old":
    "{path} : CUDA est sélectionné, mais le pilote NVIDIA {driver_version} est trop ancien (CUDA 12 requiert {min_driver} ou ultérieur)",
  "engine.asr_cuda_compute_capability_unknown":
    "{path} : CUDA est sélectionné, mais la capacité de calcul du GPU est inconnue (le pilote est peut-être trop ancien)",
  "engine.asr_cuda_compute_capability_unsupported":
    "{path} : CUDA est sélectionné, mais {gpu_name} (capacité de calcul {compute_capability}) ne prend pas en charge l’accélération float16/int8 de CTranslate2",
  "engine.asr_cuda_cublas_missing":
    "{path} : CUDA est sélectionné, mais cuBLAS n’est pas installé ; téléchargez d’abord le runtime GPU",
  "engine.asr_compute_type_unknown": "{path} : précision inconnue {compute_type} (disponibles : {available})",
  "engine.translation_model_name_invalid":
    "{field} doit être un nom de modèle de 1 à {max_chars} caractères, ou vide",
  "engine.asr_auto_uses_cpu":
    "{path} : le mode automatique utilisera le CPU (le GPU ne peut pas exécuter la reconnaissance vocale actuellement)",
  "engine.asr_compute_type_cpu_fallback":
    "{path} : le CPU ne prend pas en charge {compute_type} ; CTranslate2 utilisera une autre précision (généralement float32, plus lent)",
  "engine.asr_compute_type_gpu_fallback":
    "{path} : la capacité de calcul {compute_capability} ne prend pas en charge {compute_type} ; CTranslate2 utilisera float32 ou int8_float32 (plus lent, plus de VRAM)",
  "engine.asr_model_slow_on_cpu":
    "{path} : {model} demande 3,6 s ou plus par phrase sur un CPU ; les sous-titres auront un retard notable",
  "engine.asr_model_not_downloaded":
    "{path} : le modèle de reconnaissance vocale {model} n’est pas encore téléchargé ; il sera téléchargé avant le démarrage",
  "engine.asr_model_language_unsupported": "{path} : {model} ne sait pas reconnaître la langue suivante : {language}",
  "engine.asr_fast_larger_than_final":
    "Le modèle en direct {fast_model} est plus grand que le modèle définitif {final_model} : les sous-titres en direct risquent d’être plus lents que les définitifs",
  "engine.asr_no_speech_unavailable":
    "Ni {fast_model} ni {final_model} ne peuvent filtrer ce qui n’est pas de la parole avec no_speech ; le chant et le bruit risquent davantage d’être transcrits",
  "engine.translation_model_not_on_server": "Le serveur de traduction n’a pas {model}",
  "engine.translation_model_language_unsupported":
    "{model} ne prend pas en charge {source_language} → {target_language}",
  "engine.translation_license_non_commercial": "La licence de {model} ({license}) interdit l’usage commercial",
  "engine.translation_license_region_restricted":
    "La licence de {model} ({license}) ne s’applique pas dans les régions suivantes : {regions}",
  "engine.translation_models_differ":
    "Les traductions en direct et définitive utilisent des modèles différents : l’Ollama local met 1,5 à 18 s chaque fois qu’il change de modèle",
  "engine.vram_overflow":
    "La reconnaissance vocale et la traduction demandent environ {need_mb} Mio de VRAM, plus que les {available_mb} Mio disponibles sur {gpu_name} ; elles risquent d’être lentes ou de ne pas se charger",
  "engine.asr_ram_over_half": "Les modèles de reconnaissance vocale sur le CPU utilisent plus de la moitié de la RAM",
  "engine.translation_disabled":
    "La traduction est désactivée : seul le texte original est affiché et aucun modèle de traduction n’est nécessaire",
  "engineParam.fast_asr": "Reconnaissance des sous-titres en direct",
  "engineParam.final_asr": "Reconnaissance des sous-titres définitifs",
  "engineParam.translation_fast": "Le modèle de traduction en direct",
  "engineParam.translation_final": "Le modèle de traduction définitive",

  // ------------------------------------------------------------ status
  "status.starting": "Démarrage du moteur",
  "status.restarting": "Le moteur s’est arrêté de façon inattendue ; redémarrage",
  "status.failed": "Le moteur n’a pas pu démarrer",
  "status.stopped": "Moteur arrêté",
  "status.loading": "Chargement des modèles",
  "status.running": "À l’écoute",
  "status.error": "Arrêté après une erreur",
  "status.idle": "Prêt",
  "status.code.model_not_downloaded":
    "Le modèle n’est pas encore téléchargé. Téléchargez-le dans la page Modèles, puis démarrez à nouveau.",
  "status.code.unknown_model":
    "Le modèle indiqué dans vos paramètres n’existe pas. Choisissez-en un autre dans la page Modèles.",
  "status.code.device_not_found":
    "Le périphérique audio sélectionné est introuvable. Choisissez à nouveau un périphérique.",
  "status.code.permission_denied":
    "kikitori n’est pas autorisé à utiliser le microphone ou à enregistrer l’audio. Autorisez-le dans les paramètres du système.",
  "status.code.device_lost": "Le périphérique audio a été déconnecté. Vérifiez la connexion, puis démarrez à nouveau.",
  "status.code.capture_unavailable": "Cet ordinateur ne peut pas capturer ce type de source audio.",
  "status.code.capture_error": "Une erreur s’est produite pendant la capture audio.",
  "status.code.pipeline_error": "Une erreur s’est produite pendant le traitement de la parole.",
  "status.code.load_error": "Le chargement du modèle a échoué.",
  "status.code.translation_model_missing": "Le serveur de traduction n’a pas encore le modèle de traduction.",
  "status.code.translation_server_auth_failed":
    "Le serveur de traduction a refusé la requête : vérifiez la clé API.",
  "status.errorDetail": "Une erreur s’est produite : {detail}",
  "status.errorUnknown": "Une erreur inconnue s’est produite.",

  // ------------------------------------------------------------ notices
  "notice.close": "Fermer",
  "notice.closeLabel": "Fermer ce message",
  "notice.settingsFieldsUnreadable": {
    one: "{count} champ du fichier de paramètres n’a pas pu être lu ; la valeur par défaut est utilisée pour l’instant : {fields}",
    many: "{count} champs du fichier de paramètres n’ont pas pu être lus ; les valeurs par défaut sont utilisées pour l’instant : {fields}",
    other:
      "{count} champs du fichier de paramètres n’ont pas pu être lus ; les valeurs par défaut sont utilisées pour l’instant : {fields}",
  },
  "notice.initFailed": "L’initialisation de la fenêtre a échoué : {reason}",

  // ------------------------------------------------------------ failure contexts
  "error.saveMode": "Impossible de mémoriser le mode",
  "error.saveDevice": "Impossible de mémoriser la source audio",
  "error.start": "Impossible de démarrer",
  "error.stop": "Impossible d’arrêter",
  "error.showOverlay": "Impossible d’afficher la fenêtre des sous-titres",
  "error.hideOverlay": "Impossible de masquer la fenêtre des sous-titres",
  "error.toggleClickThrough": "Impossible de basculer « Laisser passer les clics »",
  "error.applyRecommendation": "Impossible d’appliquer la configuration recommandée",
  "error.readSettings": "Impossible de lire les paramètres",
  "error.saveDismissal": "Impossible de mémoriser « Ne plus me le rappeler »",
  "error.saveUiLocale": "Impossible de mémoriser la langue de l’interface",
  "error.setOverlayLocale": "Impossible de changer la langue de la fenêtre des sous-titres",
  "error.readModels": "Impossible de lire la liste des modèles",
  "error.detectHardware": "Impossible de détecter le matériel",
  "error.downloadModel": "Impossible de télécharger {model}",
  "error.downloadGpuRuntime": "Impossible de télécharger le composant d’accélération GPU",
  "error.openEula": "Impossible d’ouvrir les conditions de licence ; copiez l’URL dans votre navigateur",
  "error.applyStyle": "Impossible d’appliquer l’apparence des sous-titres",
  "error.openLicenses": "Impossible d’ouvrir le dossier des licences ; ouvrez-le à partir du chemin ci-dessus",
  "error.listDevices": "Impossible de lister les périphériques audio",
  "error.restartEngine": "Impossible de redémarrer le moteur",
  "error.readOverlayState": "Impossible de lire l’état de la fenêtre des sous-titres",
  "error.readBridge": "Impossible de lire l’état du moteur",
  "error.readLanguages": "Impossible de lire la liste des langues",
  "error.setTranslation": "Impossible de basculer « Afficher la traduction »",
  "error.previewLanguages": "Impossible d’obtenir la combinaison recommandée",

  // ------------------------------------------------------------ engine error codes
  "error.code.invalid_settings": "Paramètre non valide",
  "error.code.settings_io_error":
    "Impossible d’enregistrer le fichier de paramètres ; vérifiez l’espace disque et les autorisations",
  "error.code.session_active": "Déjà en cours d’exécution ; arrêtez d’abord la session",
  "error.code.device_not_found": "Le périphérique audio sélectionné est introuvable ; choisissez-le à nouveau",
  "error.code.capture_unavailable": "Cet ordinateur ne peut pas capturer ce type de source audio",
  "error.code.capture_error": "Une erreur s’est produite pendant la capture audio",
  "error.code.unknown_model": "Le moteur ne connaît pas ce modèle",
  "error.code.download_in_progress": "Cet élément est déjà en cours de téléchargement ; attendez qu’il se termine",
  "error.code.license_not_accepted":
    "Acceptez le NVIDIA CUDA EULA avant de télécharger le composant d’accélération GPU",
  "error.code.gpu_runtime_unsupported":
    "Cette plateforme ne prend pas en charge le composant d’accélération GPU (uniquement Windows et Linux sur x86-64)",
  "error.code.pull_unsupported":
    "Le serveur de traduction n’est pas Ollama, kikitori ne peut donc pas télécharger de modèles de traduction pour vous",
  "error.code.translation_server_unreachable": "Impossible de se connecter au serveur de traduction",
  "error.code.translation_server_auth_failed": "Le serveur de traduction a refusé la requête : vérifiez la clé API",
  "error.code.unknown_method":
    "La version du moteur ne correspond pas à celle de l’application ; cette opération n’est pas prise en charge",
  "error.code.invalid_params": "Des paramètres non valides ont été envoyés au moteur",
  "error.code.invalid_request": "Une requête mal formée a été envoyée au moteur",
  "error.code.internal_error": "Erreur interne du moteur",
  "error.code.other": "Le moteur a signalé une erreur ({code})",
  "error.engineSentence": "{head}.",
  "error.engineDetail": "{head} : {message}",
  "error.timeout": {
    one: "Le moteur n’a pas répondu en {count} seconde. Réessayez plus tard ou redémarrez le moteur.",
    many: "Le moteur n’a pas répondu en {count} secondes. Réessayez plus tard ou redémarrez le moteur.",
    other: "Le moteur n’a pas répondu en {count} secondes. Réessayez plus tard ou redémarrez le moteur.",
  },
  "error.disconnected":
    "Le moteur n’est pas en cours d’exécution. Attendez qu’il redémarre ou cliquez sur « Redémarrer le moteur » dans la zone d’état.",
  "error.invalidInput": "Valeur non valide : {message}",
  "error.generic": "Une erreur s’est produite : {message}",

  // ------------------------------------------------------------ transcript
  "transcript.title": "Transcription",
  "transcript.lede":
    "Les sous-titres en direct apparaissent d’abord estompés, puis laissent place à la traduction définitive une fois la phrase terminée.",
  "transcript.overlayVisible": "Afficher la fenêtre des sous-titres",
  "transcript.clickThrough": "Laisser passer les clics",
  "transcript.clear": "Effacer",
  "transcript.showTranslation": "Afficher la traduction",
  "transcript.noTranslation": "(pas de traduction)",
  "transcript.translating": "Traduction en cours",
  "transcript.emptyTitle": "Aucun sous-titre pour l’instant",
  "transcript.emptyBody":
    "Choisissez un mode et une source audio, puis cliquez sur Démarrer. Chaque phrase entendue est listée ici, l’original au-dessus et la traduction en dessous.",
  "transcript.langTitle": "{source} vers {target}",
  "transcript.shortcutCtrl": "Ctrl (⌘ sous macOS)",
  "transcript.shortcutOn":
    "Lorsque « Laisser passer les clics » est activé, les clics traversent la fenêtre des sous-titres. Appuyez sur {keys} pour l’activer ou le désactiver à tout moment.",
  "transcript.shortcutTaken":
    "Le raccourci {keys} est utilisé par une autre application ; utilisez l’interrupteur ci-contre pour activer ou désactiver « Laisser passer les clics ».",

  // ------------------------------------------------------------ models page
  "models.title": "Modèles",
  "models.lede":
    "La reconnaissance vocale s’exécute sur cet ordinateur ; la traduction est envoyée au service défini dans la page Paramètres.",
  "models.refresh": "Détecter à nouveau",

  "hw.title": "Cet ordinateur",
  "hw.status": "État",
  "hw.detecting": "Détection en cours",
  "hw.os": "Système d’exploitation",
  "hw.cpu": "Processeur",
  "hw.memory": "Mémoire",
  "hw.gpu": "Carte graphique",
  "hw.cublas": "cuBLAS (reconnaissance sur GPU)",
  "hw.server": "Service de traduction",
  "hw.cpuValue": "{model}, {cores}",
  "hw.cores": "{physical} cœurs, {logical} threads",
  "hw.threads": { one: "{count} thread", many: "{count} threads", other: "{count} threads" },
  "hw.coresUnknown": "nombre de cœurs inconnu",
  "hw.appleSilicon": "Apple Silicon (mémoire partagée avec le processeur)",
  "hw.gpuVram": "{size} de mémoire vidéo",
  "hw.gpuDriver": "pilote {version}",
  "hw.gpuCompute": "capacité de calcul {capability}",
  "hw.noNvidia": "Aucune carte graphique NVIDIA détectée",
  "hw.cublasNotUsed": "Non utilisé sur cette plateforme",
  "hw.cublasAvailable": "Disponible ({source})",
  "hw.cublasSourceUnknown": "source inconnue",
  "hw.cublasMissing": "Non installé",
  "hw.cublasSource.runtime": "composant d’accélération GPU téléchargé par kikitori",
  "hw.cublasSource.configured": "dossier défini dans la configuration personnalisée",
  "hw.cublasSource.system": "installation CUDA du système",
  "hw.serverNotDetected": "Non détecté",
  "hw.serverLocal": "local",
  "hw.serverRemote": "distant",
  "hw.serverModelsUnknown": "liste des modèles inconnue",
  "hw.serverModelCount": { one: "{count} modèle", many: "{count} modèles", other: "{count} modèles" },
  "hw.serverReachable": "{url} ({where}, {models})",
  "hw.serverUnreachable": "{url} (injoignable)",
  "hw.serverUnreachableError": "{url} (injoignable : {error})",
  "hw.memoryAvailable": "{total} ({available} disponibles)",
  "hw.incomplete": "Détection incomplète : {error}",

  "rec.title": "Configuration recommandée",
  "rec.pending": "Les recommandations s’afficheront ici une fois la détection terminée.",
  "rec.tier": "Catégorie de matériel",
  "rec.fastAsr": "Reconnaissance des sous-titres en direct",
  "rec.finalAsr": "Reconnaissance pour la traduction définitive",
  "rec.fastTranslation": "Traduction des sous-titres en direct",
  "rec.finalTranslation": "Traduction définitive",
  "rec.budget": "Mémoire disponible pour la traduction",
  "rec.notLocal":
    "Aucune recommandation (le service de traduction n’est pas local) ; choisissez-en un dans la configuration personnalisée",
  "rec.gpuRuntimeMissing":
    "Une fois le composant d’accélération GPU ci-dessous téléchargé, la recommandation utilisera la carte graphique.",
  "rec.apply": "Appliquer la configuration recommandée",
  "rec.applyNote":
    "Définit les modèles de reconnaissance, le périphérique, la précision, les threads et les modèles de traduction.",
  "rec.applied":
    "Configuration recommandée appliquée : sous-titres en direct {fast}, traduction définitive {final} ({device}).",
  "rec.appliedWhileRunning":
    "Configuration recommandée appliquée : sous-titres en direct {fast}, traduction définitive {final} ({device}). Elle prendra effet au prochain démarrage.",
  "tier.nvidia_8gb": "Carte graphique NVIDIA (8 Go ou plus)",
  "tier.nvidia_4gb": "Carte graphique NVIDIA (4 à 8 Go)",
  "tier.nvidia_lt4gb": "Carte graphique NVIDIA (moins de 4 Go)",
  "tier.cpu": "CPU (16 Go de mémoire ou plus)",
  "tier.cpu_low": "CPU (configuration légère)",
  "tier.apple_silicon": "Apple Silicon (CPU)",
  "threads.auto": "threads automatiques",
  "threads.count": { one: "{count} thread", many: "{count} threads", other: "{count} threads" },
  "computeType.auto": "précision automatique",

  "machine.gpu": "{name}, {gb} Go",
  "machine.ram": "{gb} Go de mémoire",
  "machine.named": "{name}, {ram}",
  "machine.thisCpu": "le processeur de cet ordinateur",
  "machine.thisCpuRam": "le processeur de cet ordinateur, {ram}",
  "summary.asrSameDevice": "direct {fast}, définitif {final} ({device})",
  "summary.asrSplit": "direct {fast} ({fastDevice}), définitif {final} ({finalDevice})",
  "summary.translationKept": "modèles de traduction inchangés (le service de traduction n’est pas local)",
  "summary.translationSame": "traduction {model}",
  "summary.translationSplit": "traduction en direct {fast}, traduction définitive {final}",
  "summary.full": "{asr} ; {translation}",
  "firstRun.applied": "Configuration recommandée appliquée pour cet ordinateur ({machine}) : {summary}.",
  "firstRun.appliedGpuMissing":
    "Configuration recommandée appliquée pour cet ordinateur ({machine}) : {summary}. Il manque le composant d’accélération GPU pour la carte graphique, le CPU est donc utilisé pour l’instant ; téléchargez-le dans la page Modèles pour utiliser la carte graphique.",
  "firstRun.applyFailed":
    "Impossible d’appliquer la configuration recommandée : {reason} Vérifiez la configuration actuelle dans la page Modèles et appliquez-y la recommandation si nécessaire.",
  "firstRun.detectFailed":
    "Impossible de détecter le matériel de cet ordinateur : {reason} La configuration par défaut est utilisée pour l’instant (direct {fast}, définitif {final}) ; vous pouvez relancer la détection dans la page Modèles et appliquer la recommandation.",
  "firstRun.review": "Vérifier dans la page Modèles",
  "offer.text": "Il existe une meilleure configuration pour cet ordinateur : {summary}.",
  "offer.apply": "Appliquer",
  "offer.dismiss": "Ne plus me le rappeler",
  "missing.itemFailed": "Le téléchargement de {model} a échoué : {error}",
  "missing.downloading": "Téléchargement de {models} ; vous pourrez démarrer une fois terminé.",
  "missing.failed": "{items}. Vous pouvez relancer le téléchargement.",
  "missing.offer": "Il faut télécharger {models} (environ {size} au total) avant de pouvoir démarrer.",
  "missing.done": "Les modèles sont téléchargés. Vous pouvez maintenant cliquer sur Démarrer.",
  "missing.download": "Télécharger les modèles manquants",
  "startdl.title": "Téléchargement des modèles avant le démarrage",
  "startdl.itemAsr": "Modèle de reconnaissance vocale {model}",
  "startdl.itemTranslation": "Modèle de traduction {model}",
  "startdl.pending": "En attente",
  "startdl.preparing": "Préparation du téléchargement",
  "startdl.progress": "{percent} %, {done} sur {total}",
  "startdl.done": "Terminé",
  "startdl.failed": "Échec du téléchargement : {error}",
  "startdl.stillMissing": "{model} a déjà été téléchargé, mais le moteur le signale toujours comme manquant.",
  "startdl.downloading": "Les sous-titres démarreront automatiquement une fois les téléchargements terminés.",
  "startdl.starting": "Téléchargements terminés ; démarrage.",
  "startdl.failedHelp":
    "Un téléchargement a échoué. Cliquez sur Réessayer ou choisissez d’autres modèles dans la page Modèles.",
  "startdl.pullUnsupported":
    "Le serveur de traduction {url} n’est pas Ollama, kikitori ne peut donc pas télécharger de modèles de traduction pour vous. Installez vous-même {models} sur ce serveur ou choisissez un modèle installé dans la page Modèles. Vous pouvez aussi désactiver « Afficher la traduction » pour ne voir que l’original.",
  "startdl.serverUnreachable":
    "Impossible de se connecter au serveur de traduction {url}. Si vous utilisez Ollama, lancez-le d’abord (ouvrez l’application Ollama ou exécutez ollama serve dans un terminal), puis cliquez sur Réessayer. Vous pouvez aussi désactiver « Afficher la traduction » pour ne voir que l’original.",
  "startdl.authFailed":
    "Le serveur de traduction {url} a refusé la requête : vérifiez la clé API (la variable d’environnement indiquée dans Paramètres), puis cliquez sur Réessayer.",
  "startdl.retry": "Réessayer",
  "startdl.cancel": "Annuler",
  "startdl.cancelLabel": "Cesser d’attendre les téléchargements et ne pas démarrer automatiquement",

  "gpu.title": "Composant d’accélération GPU",
  "gpu.lede":
    "Accélérer la reconnaissance vocale avec une carte graphique NVIDIA nécessite la bibliothèque cuBLAS de NVIDIA. kikitori ne la fournit pas ; elle est téléchargée depuis PyPI une fois sa licence acceptée.",
  "gpu.name": "Composant d’accélération GPU",
  "gpu.unsupported":
    "La plateforme de cet ordinateur n’utilise pas ce composant : l’accélération GPU est réservée à Windows et Linux sur x86-64 avec une carte graphique NVIDIA. Sur les autres plateformes, la reconnaissance vocale s’effectue toujours sur le CPU.",
  "gpu.progressLabel": "Progression du téléchargement du composant d’accélération GPU",
  "gpu.finishing": "Téléchargé ; vérification et extraction (quelques secondes)",
  "gpu.progress": "{percent} %, {done} / {total}",
  "gpu.installed": "{what} installé.",
  "gpu.installedAt": "{what} installé dans {path}.",
  "gpu.noCublas":
    "Installé, mais aucun cuBLAS utilisable n’est détecté. Redémarrez le moteur, puis cliquez sur « Détecter à nouveau ».",
  "gpu.sizeApprox": "environ {size}",
  "gpu.sizeUnknown": "quelques centaines de Mo",
  "gpu.situationMissing":
    "La carte graphique NVIDIA de cet ordinateur peut accélérer la reconnaissance, mais ce composant est manquant, le CPU est donc utilisé pour l’instant.",
  "gpu.situationNoGpu": "Aucune carte graphique NVIDIA détectée ; sans elle, inutile de télécharger ce composant.",
  "gpu.readLicense": "Lire la licence",
  "gpu.download": "Télécharger le composant d’accélération GPU",
  "gpu.consentNotice":
    "NVIDIA cuBLAS ({package}, {size}) sera téléchargé depuis PyPI. En l’utilisant, vous acceptez le NVIDIA CUDA EULA ({license}).",
  "gpu.lastFailed": "Le dernier téléchargement a échoué : {error}",
  "gpu.agree": "J’accepte le NVIDIA CUDA EULA",
  "gpu.installedRestarting": "Composant d’accélération GPU installé. Redémarrage du moteur pour utiliser le GPU.",
  "gpu.installedBusy":
    "Composant d’accélération GPU installé. Une tâche est en cours, le moteur n’a donc pas été redémarré automatiquement ; redémarrez-le une fois terminé pour utiliser le GPU.",

  "asr.title": "Modèles de reconnaissance vocale",
  "asr.lede":
    "Les sous-titres en direct demandent de la vitesse, les traductions définitives de la précision ; chacun peut utiliser un modèle différent.",
  "asr.colModel": "Modèle",
  "asr.colSize": "Taille",
  "asr.colSpeed": "Vitesse",
  "asr.colMemory": "Mémoire",
  "asr.colLicense": "Licence",
  "asr.colStatus": "État",
  "asr.inUse": "Utilisé : {roles}",
  "asr.noSpeech": "Pas de détection de l’absence de parole",
  "asr.noSpeechHelp":
    "Ce modèle ne sait pas déterminer de façon fiable l’absence de parole ; pour les traductions définitives, le jugement du modèle des sous-titres en direct sert à filtrer le bruit.",
  "asr.memoryCpu": "CPU {size}",
  "asr.memoryGpu": "GPU {size}",
  "asr.progressLabel": "Progression du téléchargement de {model}",
  "asr.progress": "{percent} %, {done}",
  "asr.downloaded": "Téléchargé",
  "asr.download": "Télécharger",
  "asr.downloadFailed": "Échec du téléchargement : {error}",
  "role.fast": "direct",
  "role.final": "définitif",
  "speed.fastest": "Très rapide",
  "speed.fast": "Rapide",
  "speed.medium": "Moyenne",
  "speed.slow": "Lente",
  "speed.slowest": "Très lente",
  "download.done": "{model} téléchargé.",
  "download.failed": "Le téléchargement de {what} a échoué : {error}",

  "tr.title": "Modèles de traduction",
  "tr.unreachable":
    "Impossible de joindre le service de traduction à l’adresse {url}. Si vous utilisez un Ollama local, lancez-le d’abord.",
  "tr.unreachableError":
    "Impossible de joindre le service de traduction à l’adresse {url} ({error}). Si vous utilisez un Ollama local, lancez-le d’abord.",
  "tr.installedCount": {
    one: "{count} modèle installé sur le service de traduction à l’adresse {url}.",
    many: "{count} modèles installés sur le service de traduction à l’adresse {url}.",
    other: "{count} modèles installés sur le service de traduction à l’adresse {url}.",
  },
  "tr.installedCountLocal": {
    one: "{count} modèle installé sur le service de traduction à l’adresse {url} (local).",
    many: "{count} modèles installés sur le service de traduction à l’adresse {url} (local).",
    other: "{count} modèles installés sur le service de traduction à l’adresse {url} (local).",
  },
  "tr.nonCommercial": "Non commercial",
  "tr.regionExcluded": "Licence excluant : {regions}",
  "tr.license": "Licence {license}",
  "tr.unlisted": "Absent de la liste de kikitori ; vérifiez vous-même la licence",
  "tr.stateUnknown": "État d’installation inconnu",
  "tr.stateInstalled": "Installé",
  "tr.stateMissing": "Non installé",

  "combo.title": "Configuration personnalisée",
  "combo.lede":
    "Les modifications sont vérifiées immédiatement pour cet ordinateur ; une configuration comportant des erreurs ne peut pas être enregistrée. Une nouvelle configuration prend effet au prochain clic sur Démarrer.",
  "combo.fastModel": "Modèle de reconnaissance pour les sous-titres en direct",
  "combo.finalModel": "Modèle de reconnaissance pour les traductions définitives",
  "combo.device": "Périphérique de calcul",
  "combo.deviceAuto": "Automatique (GPU NVIDIA s’il est disponible, sinon le CPU)",
  "combo.deviceCpu": "CPU",
  "combo.deviceCuda": "GPU NVIDIA (CUDA)",
  "combo.deviceHelp":
    "Les deux chaînes partagent un même périphérique : si le GPU échoue pendant une session, il n’est pas réessayé.",
  "combo.translationFast": "Modèle de traduction pour les sous-titres en direct",
  "combo.translationFinal": "Modèle de traduction pour les traductions définitives",
  "combo.advanced": "Options avancées",
  "combo.advancedHelp":
    "La précision et le nombre de threads sont généralement laissés à la configuration recommandée. S’ils sont vides ou sur Automatique, le moteur décide selon le périphérique réellement utilisé (int8 sur CPU, float16 sur GPU).",
  "combo.fastComputeType": "Précision pour les sous-titres en direct",
  "combo.finalComputeType": "Précision pour les traductions définitives",
  "combo.fastThreads": "Threads CPU pour les sous-titres en direct",
  "combo.finalThreads": "Threads CPU pour les traductions définitives",
  "combo.cudaDirs": "Dossiers cuBLAS 12 installés par vous-même",
  "combo.cudaDirsPlaceholder":
    "Un chemin complet par ligne, par exemple C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v12.8\\bin",
  "combo.cudaDirsHelp":
    "Nécessaire uniquement si vous avez installé vous-même le CUDA Toolkit. Le composant d’accélération GPU téléchargé ci-dessus est utilisé automatiquement et n’a pas besoin d’être listé.",
  "combo.save": "Enregistrer la configuration",
  "combo.reset": "Revenir aux paramètres actuels",
  "combo.notInList": "{value} (absent de la liste)",
  "combo.notDownloaded": "{id} (non téléchargé)",
  "combo.saved": "Configuration enregistrée.",
  "combo.savedNextStart": "Enregistré ; prendra effet au prochain démarrage.",
  "check.fixFirst": "Corrigez d’abord les champs signalés.",
  "check.checking": "Vérification de cette configuration",
  "check.okWithWarnings": "Cet ordinateur peut utiliser cette configuration, mais notez ceci :",
  "check.ok": "Cet ordinateur peut utiliser cette configuration.",
  "check.notOk": "Cette configuration ne peut pas être utilisée pour le moment :",
  "check.rejected": "Le moteur n’accepte pas ce paramètre : {message}",
  "check.unavailable":
    "Impossible de vérifier la configuration pour le moment ({reason}). Vous pouvez tout de même enregistrer ; le moteur vérifie le format des paramètres à l’enregistrement.",
  "check.engineNotReady": "Le moteur n’est pas encore prêt",
  "form.fixFields": "Corrigez les champs signalés.",
  "form.engineRejected": "Le moteur a refusé cette valeur : {message}",

  // ------------------------------------------------------------ settings page
  "settings.title": "Paramètres",
  "settings.path": "Fichier de paramètres : {path}",
  "settings.uiLocaleLegend": "Langue de l’interface",
  "settings.uiLocaleLabel": "Langue de l’interface",
  "settings.uiLocaleSystem": "Suivre le système",
  "settings.uiLocaleHelp":
    "S’applique immédiatement à la fenêtre principale et à celle des sous-titres. « Suivre le système » utilise la langue de votre système d’exploitation, ou l’anglais si elle n’est pas disponible.",
  "settings.translationLegend": "Service de traduction",
  "settings.translationLede":
    "Toute API compatible OpenAI convient. L’URL par défaut d’un Ollama local est http://localhost:11434/v1.",
  "settings.baseUrl": "URL du service",
  "settings.apiKeyEnv": "Variable d’environnement de la clé API",
  "settings.apiKeyEnvHelp":
    "Saisissez uniquement le nom de la variable. Définissez la clé elle-même comme variable d’environnement du système ; kikitori n’enregistre jamais de clés. Un Ollama local n’a pas besoin de clé.",
  "settings.translationModelsHelp":
    "Les modèles de traduction se choisissent dans la configuration personnalisée de la page Modèles, où la licence et la charge sur cet ordinateur sont également vérifiées.",
  "settings.regionLegend": "Régions de licence des modèles de traduction",
  "settings.regionLede":
    "Certains modèles de traduction (par exemple Tencent HY-MT) ne sont pas sous licence pour l’UE, le Royaume-Uni ou la Corée du Sud. kikitori ne peut pas savoir où vous vous trouvez, il ne les recommande donc pas par défaut.",
  "settings.regionConsent":
    "Je confirme que je n’utilise pas kikitori dans l’UE, au Royaume-Uni ni en Corée du Sud, et j’accepte les conditions de licence de ces modèles de traduction.",
  "settings.regionHelp":
    "La configuration recommandée n’utilise que Qwen3.5, qui n’a pas de restriction régionale. Si la case est cochée, choisir vous-même un modèle soumis à des restrictions régionales dans la configuration personnalisée n’affiche plus d’avertissement.",
  "settings.languageLegend": "Langues",
  "settings.languageLede":
    "Choisissez la langue que vous écoutez et la langue des sous-titres ; les modèles recommandés pour celles-ci sont listés ci-dessous. Rien n’est téléchargé maintenant : les modèles manquants sont téléchargés la première fois que vous cliquez sur Démarrer.",
  "settings.languageModeMedia": "Voici les langues de la traduction de vidéos.",
  "settings.languageModeInterpret": "Voici les langues de l’interprétation en direct.",
  "settings.source": "Langue écoutée",
  "settings.sourceHelp":
    "La langue parlée dans la vidéo. Avec « Détecter automatiquement », la langue de chaque phrase est d’abord identifiée, et les phrases très courtes peuvent être mal identifiées.",
  "settings.own": "Ma langue",
  "settings.ownHelp": "La langue que vous parlez ; les propos de votre interlocuteur sont traduits dans cette langue.",
  "settings.languageFilter": "Saisissez pour filtrer",
  "settings.languageFilterLabel": "Filtrer : {field}",
  "settings.applyRecommended": "Passer aussi aux modèles recommandés",
  "settings.applyRecommendedHelp":
    "Change les modèles de reconnaissance, le périphérique et les modèles de traduction ; si la case n’est pas cochée, seules les langues changent.",
  "settings.saveLanguages": "Enregistrer les langues",
  "settings.languagesSaved": "Langues enregistrées.",
  "settings.languagesSavedWithModels": "Langues enregistrées ; les modèles recommandés sont maintenant utilisés.",
  "settings.languagesSavedNoPreview":
    "Langues enregistrées ; la combinaison recommandée n’était pas prête, les modèles n’ont donc pas changé.",
  "settings.languagesSavedNextStart": "Enregistré ; prendra effet au prochain démarrage.",
  "settings.languagesUnavailable": "Les langues pourront être choisies une fois le moteur prêt.",
  "language.auto": "Détecter automatiquement",
  "preview.title": "Combinaison recommandée",
  "preview.checking": "Calcul de la combinaison recommandée pour ces langues",
  "preview.unavailable": "La combinaison recommandée n’est pas disponible pour le moment : {reason}",
  "preview.translation": "Modèle de traduction",
  "preview.installed": "Installé",
  "preview.downloadOnFirstUse": "Téléchargé à la première utilisation, environ {size}",
  "preview.downloadUnknownSize": "Téléchargé à la première utilisation (taille inconnue)",
  "preview.installedUnknown": "Impossible de savoir s’il est installé (le serveur de traduction n’a pas répondu)",
  "preview.notNeeded": "Aucune traduction nécessaire",
  "preview.translationOff": "« Afficher la traduction » est désactivé, aucun modèle de traduction n’est donc nécessaire",
  "preview.unsupported": "Aucun modèle de traduction ne prend en charge cette combinaison de langues",
  "preview.remote":
    "Le serveur de traduction n’est pas sur cet ordinateur ; choisissez son modèle dans la combinaison personnalisée de la page Modèles",
  "preview.allInstalled": "Tous les modèles nécessaires sont installés.",
  "preview.total": "Le premier démarrage télécharge environ {size}.",
  "preview.totalUnknown":
    "Le premier démarrage télécharge des modèles, dont certains de taille inconnue (environ {size} connus).",
  "settings.target": "Langue des sous-titres",
  "settings.targetHelp":
    "La langue des sous-titres. Si c’est la même langue parlée que la langue écoutée, rien n’est traduit ; le chinois est seulement converti d’une écriture à l’autre.",
  "settings.partner": "Langue de l’interlocuteur",
  "settings.partnerHelp":
    "Vos propos sont traduits dans cette langue, et ceux de votre interlocuteur dans la vôtre. Les deux doivent pouvoir être distinguées, elles ne peuvent donc pas être identiques (le chinois traditionnel et le chinois simplifié comptent comme la même langue).",
  "settings.styleLegend": "Sous-titres",
  "settings.showTranslation": "Afficher la traduction",
  "settings.showTranslationHelp":
    "Si l’option est désactivée, seul l’original est affiché, avec la taille et la couleur des sous-titres principaux : le moteur ne traduit rien, n’a pas besoin de serveur de traduction et laisse le GPU libre. Un changement en cours d’exécution s’applique immédiatement ; les lignes déjà affichées ne sont pas traduites par la suite.",
  "settings.previewOriginal": "次の駅で降りましょう。",
  "settings.previewTranslation": "Descendons à la prochaine gare.",
  "settings.fontSize": "Taille de police de la traduction",
  "settings.opacity": "Opacité de l’arrière-plan",
  "settings.position": "Position des sous-titres",
  "settings.positionBottom": "En bas de la fenêtre",
  "settings.positionTop": "En haut de la fenêtre",
  "settings.lines": "Phrases affichées simultanément",
  "settings.linesOption": { one: "{count} phrase", many: "{count} phrases", other: "{count} phrases" },
  "settings.save": "Enregistrer les paramètres",
  "settings.saved": "Paramètres enregistrés.",
  "settings.savedRegion":
    "Paramètres enregistrés. La confirmation de la région de licence ne concerne que l’avertissement affiché quand vous choisissez vous-même un modèle dans la configuration personnalisée.",

  // ------------------------------------------------------------ validation
  "validation.urlScheme": "L’URL doit commencer par http:// ou https://.",
  "validation.urlEmpty": "Saisissez l’URL du service de traduction.",
  "validation.urlInvalid": "Cette URL n’est pas valide.",
  "validation.envPattern":
    "Utilisez uniquement des lettres, des chiffres et des traits de soulignement, sans commencer par un chiffre.",
  "validation.envTooLong": "Le nom est trop long ({max} caractères au maximum).",
  "validation.partner":
    "La langue de l’interlocuteur doit être différente de la vôtre (le chinois traditionnel et le chinois simplifié comptent comme la même langue).",
  "validation.language": "Choisissez une langue dans la liste.",
  "validation.fontSize": "La taille de police doit être comprise entre {min} et {max}.",
  "validation.opacity": "L’opacité de l’arrière-plan doit être comprise entre 0 % et 100 %.",
  "validation.position": "Choisissez en haut ou en bas.",
  "validation.lines": "Le nombre de phrases doit être compris entre {min} et {max}.",
  "validation.asrModel": "Choisissez un modèle de reconnaissance vocale.",
  "validation.device": "Choisissez un périphérique de calcul.",
  "validation.translationEmpty": "Saisissez un nom de modèle de traduction.",
  "validation.translationTooLong": "Les noms de modèle font au plus {max} caractères.",
  "validation.computeType": "Choisissez une précision.",
  "validation.threads":
    "Le nombre de threads doit être compris entre {min} et {max} ; laissez vide pour le mode automatique.",
  "validation.cudaDirsTooMany": "{max} dossiers au maximum.",
  "validation.cudaDirNotAbsolute": "« {path} » n’est pas un chemin complet (par exemple C:\\CUDA\\bin).",

  // ------------------------------------------------------------ about
  "about.title": "À propos",
  "about.engine": "Moteur {version}, version du protocole {protocol}.",
  "about.engineFake":
    "Moteur {version}, version du protocole {protocol} ; le faux moteur est actuellement utilisé (mode développement).",
  "about.engineOffline": "Le moteur n’est pas encore connecté.",
  "about.license": "kikitori est publié sous la licence {license}.",
  "about.notice":
    "Les composants tiers de ce programme, tels qu’Intel® OpenMP, sont fournis selon leurs propres conditions de licence ; leur rétro-ingénierie n’est pas autorisée.",
  "about.thirdPartyHead": "Textes complets des licences des composants tiers :",
  "about.thirdPartyApp":
    "L’application elle-même (crates Tauri/Rust, paquets du frontend et LICENSE de kikitori) : ",
  "about.thirdPartyEngine": "Moteur vocal (paquets Python) : ",
  "about.thirdPartyDev":
    "Les versions de développement n’incluent aucun dossier de licences. Dans les versions publiées, les textes des licences se trouvent dans les dossiers licenses (l’application) et kikitori-engine/THIRD_PARTY_LICENSES (le moteur vocal) du répertoire d’installation.",
  "about.openAppLicenses": "Ouvrir le dossier des licences tierces de l’application",
  "about.openEngineLicenses": "Ouvrir le dossier des licences tierces du moteur",
  "about.checkUpdate": "Rechercher des mises à jour",

  // ------------------------------------------------------------ updates
  "update.available": "kikitori {version} est disponible au téléchargement et à l’installation.",
  "update.install": "Télécharger et installer",
  "update.downloading": "Téléchargement de la mise à jour.",
  "update.progress": "Téléchargement de la mise à jour : {percent} %",
  "update.installed": "kikitori {version} est installé et prendra effet après un redémarrage.",
  "update.restartNow": "Redémarrer maintenant",
  "update.checking": "Recherche en cours",
  "update.found": "Nouvelle version {version} disponible.",
  "update.latest": "Vous disposez de la dernière version.",
  "update.checkFailed": "Impossible de rechercher des mises à jour : {reason}",
  "update.failed": "Échec de la mise à jour : {reason}",
  "update.relaunchFailed":
    "Impossible de redémarrer automatiquement ; fermez puis rouvrez kikitori vous-même. ({reason})",

  // ------------------------------------------------------------ overlay
  "overlay.title": "Sous-titres kikitori",
  "overlay.drag": "Faire glisser pour déplacer",
  "overlay.hide": "Masquer",
  "overlay.hint":
    "Les sous-titres s’affichent ici. Faites glisser la fenêtre pour la déplacer ; faites glisser le coin inférieur droit pour la redimensionner.",

  // ------------------------------------------------------------ transcript export, reading rhythm, OBS text file, first use and diagnostics
  "export.button": "Exporter",
  "export.content": "Contenu",
  "export.contentBoth": "Original et traduction",
  "export.contentOriginal": "Original seulement",
  "export.contentTranslation": "Traduction seulement",
  "export.help": "Exporte toutes les phrases complètes de cette session (pas seulement celles encore à l’écran) dans kikitori/transcripts, dans votre dossier Documents. Le CSV s’ouvre directement dans Excel.",
  "export.empty": "Aucune phrase complète à exporter pour l’instant.",
  "export.saved": "Transcription enregistrée : {path}",
  "export.savedAuto": "La transcription de cette session a été enregistrée automatiquement (SRT et TXT) : {path}",
  "export.openFolder": "Ouvrir le dossier",
  "error.export": "Impossible d’enregistrer la transcription",
  "error.autoSave": "Impossible d’enregistrer automatiquement la transcription",
  "error.openFolder": "Impossible d’ouvrir le dossier ; ouvrez-le à partir du chemin indiqué dans les Paramètres",
  "error.applyObs": "Impossible d’appliquer le réglage du fichier texte pour OBS",
  "error.openOllama": "Impossible d’ouvrir la page de téléchargement d’Ollama ; ouvrez https://ollama.com/download dans votre navigateur",
  "settings.minDisplay": "Afficher chaque ligne au moins (secondes)",
  "settings.minDisplayHelp": "Quand on parle vite, une nouvelle ligne attend que la précédente soit affichée depuis ce délai avant de la remplacer, pour vous laisser le temps de la lire. 0 la remplace immédiatement.",
  "settings.idleClear": "Estomper sans nouveaux sous-titres (secondes)",
  "settings.idleClearHelp": "Passé ce délai après la dernière ligne, la fenêtre des sous-titres s’estompe et le fichier texte pour OBS est vidé. 0 les laisse affichés.",
  "output.legend": "Transcriptions et OBS",
  "output.autoSave": "Enregistrer automatiquement à la fin de chaque session (SRT et TXT)",
  "output.autoSaveHelp": "Quand vous cliquez sur Arrêter ou que le moteur s’arrête, la transcription de la session (original et traduction) est enregistrée dans le dossier ci-dessous.",
  "output.transcriptsPath": "Emplacement : {path}",
  "output.noDocuments": "Le dossier Documents est introuvable ; impossible d’enregistrer.",
  "output.openTranscripts": "Ouvrir le dossier des transcriptions",
  "output.obs": "Écrire les sous-titres dans un fichier texte (pour OBS)",
  "output.obsOff": "Désactivé",
  "output.obsOriginal": "Original",
  "output.obsTranslation": "Traduction",
  "output.obsBoth": "Original et traduction",
  "output.obsHelp": "Une fois activé, les sous-titres de la fenêtre des sous-titres sont écrits dans un fichier texte au fur et à mesure. Dans OBS, ajoutez une source Texte (GDI+), cochez Lire à partir d’un fichier et choisissez ce fichier. Aucun port réseau n’est ouvert.",
  "output.obsPath": "Fichier texte : {path}",
  "output.obsError": "Impossible d’écrire le fichier texte pour OBS : {error}",
  "output.openObs": "Ouvrir le dossier du fichier pour OBS",
  "validation.minDisplay": "Doit être compris entre {min} et {max} secondes.",
  "validation.idleClear": "Doit être compris entre {min} et {max} secondes ; 0 les laisse affichés.",
  "validation.obsOutput": "Choisissez ce qu’il faut écrire.",
  "guide.local": "Le service de traduction {url} est injoignable. Pour afficher les traductions, installez et lancez Ollama (gratuit, fonctionne sur cet ordinateur), puis cliquez sur Détecter à nouveau. Vous pouvez aussi n’afficher que l’original pour le moment.",
  "guide.remote": "Le service de traduction {url} est injoignable. Vérifiez qu’il fonctionne et que l’adresse est correcte, puis cliquez sur Détecter à nouveau ; ou n’affichez que l’original pour le moment.",
  "guide.install": "Installer Ollama",
  "guide.originalOnly": "N’afficher que l’original",
  "guide.recheck": "Détecter à nouveau",
  "transcript.shortcutTranslation": "Appuyez sur {keys} pour activer ou désactiver Afficher la traduction.",
  "transcript.shortcutClear": "Appuyez sur {keys} pour effacer les sous-titres de la fenêtre des sous-titres et du fichier texte pour OBS.",
  "transcript.shortcutActionTaken": "Le raccourci {keys} ({action}) est utilisé par une autre application et n’est pas disponible.",
  "shortcut.toggleTranslation": "basculer la traduction",
  "shortcut.clearSubtitles": "effacer les sous-titres",
  "about.copyDiagnostics": "Copier le diagnostic",
  "about.diagnosticsHelp": "À joindre quand vous signalez un problème : versions, système, détection du matériel, paramètres (pour la clé API, seulement le nom de la variable d’environnement, jamais la clé) et les derniers messages du moteur. Les noms d’utilisateur dans les chemins sont masqués.",
  "about.diagnosticsCopied": "Copié dans le presse-papiers.",
  "about.diagnosticsManual": "Impossible d’écrire dans le presse-papiers ; copiez le texte ci-dessous à la main.",
  // ------------------------------------------------------------ 術語表（F3）、狀態列音量與延遲（F4）、刪除模型（F7）、新引擎訊息
  "glossary.legend": "Glossaire",
  "glossary.lede": "Indiquez comment traduire les noms et le vocabulaire des lives : quand l’original contient l’un de ces termes, la traduction utilise le texte saisi ici. Vos entrées priment sur la liste intégrée.",
  "glossary.builtin": "Utiliser le vocabulaire de live intégré",
  "glossary.builtinHelp": "Termes courants des lives japonais (p. ex. スパチャ → Super Chat) ; utilisés seulement si l’original est en japonais et les sous-titres en chinois traditionnel ou simplifié, en anglais ou en coréen.",
  "glossary.colSource": "Original",
  "glossary.colTarget": "Traduction",
  "glossary.colLanguage": "Langue des sous-titres",
  "glossary.colActions": "Actions",
  "glossary.allLanguages": "Toutes les langues",
  "glossary.sourceLabel": "Original, ligne {row}",
  "glossary.targetLabel": "Traduction, ligne {row}",
  "glossary.languageLabel": "Langue des sous-titres, ligne {row}",
  "glossary.remove": "Retirer",
  "glossary.removeLabel": "Retirer la ligne {row}",
  "glossary.empty": "Aucune entrée personnelle pour l’instant.",
  "glossary.add": "Ajouter une entrée",
  "glossary.import": "Importer un CSV",
  "glossary.export": "Exporter en CSV",
  "glossary.csvHelp": "Colonnes du CSV : source (original), target (traduction), target_language (code de la langue des sous-titres, p. ex. fr ; vide = toutes les langues). Modifiable dans Excel. Les entrées importées s’ajoutent à la fin du tableau et s’appliquent quand vous cliquez sur Enregistrer le glossaire ; les fichiers exportés vont dans le dossier des transcriptions.",
  "glossary.save": "Enregistrer le glossaire",
  "glossary.reset": "Annuler les modifications",
  "glossary.saved": "Enregistré.",
  "glossary.savedNextStart": "Enregistré ; prendra effet au prochain démarrage.",
  "glossary.exported": "Glossaire exporté : {path}",
  "glossary.imported": "Entrées importées : {added}. Doublons ignorés : {skipped}. Cliquez sur Enregistrer le glossaire pour les appliquer.",
  "glossary.importBadLines": "Lignes sans original ou sans traduction non importées : {lines}.",
  "glossary.importTooLarge": "Le fichier est trop volumineux (limite {size}) ; vérifiez qu’il s’agit bien d’un CSV de glossaire.",
  "glossary.error.empty": "Obligatoire.",
  "glossary.error.spaces": "Supprimez les espaces au début ou à la fin.",
  "glossary.error.tooLong": "{max} caractères au maximum.",
  "glossary.error.control": "Les sauts de ligne, tabulations et caractères de contrôle invisibles ne sont pas acceptés.",
  "glossary.error.language": "Choisissez une langue dans la liste.",
  "glossary.error.duplicate": "Identique à la ligne {row} (même langue de sous-titres).",
  "glossary.error.tooMany": "{max} entrées au maximum (actuellement {count}).",
  "error.exportGlossary": "Impossible d’exporter le glossaire",
  "error.importGlossary": "Impossible de lire le fichier CSV",
  "metrics.levelLabel": "Niveau d’entrée",
  "metrics.levelValue": "{db} dBFS",
  "metrics.levelUnknown": "Pas encore de mesure du niveau",
  "metrics.latency": "Délai {seconds} s",
  "metrics.latencyUnknown": "Délai —",
  "metrics.latencyHelp": "Temps entre la fin d’une phrase et son sous-titre définitif (médiane des dernières phrases)",
  "metrics.backlog": "La reconnaissance ne suit pas : plusieurs phrases attendent leur sous-titre définitif. Envisagez un modèle plus petit dans la page Modèles.",
  "metrics.openModels": "Ouvrir Modèles",
  "metrics.noAudio": "Aucun son depuis 15 secondes. Vérifiez que la bonne source audio est choisie, que la vidéo est en lecture ou que le micro n’est pas coupé.",
  "asr.diskUse": "{size} sur le disque",
  "asr.delete": "Supprimer",
  "asr.deleting": "Suppression…",
  "asr.deleteYes": "Oui, supprimer",
  "asr.deleteNo": "Annuler",
  "asr.deleteConfirm": "Supprimer {model} ? Cela libère {size}.",
  "asr.deleteSelected": "Vos réglages l’utilisent pour les sous-titres ({roles}) : il sera de nouveau téléchargé au prochain démarrage.",
  "asr.deleteInUse": "La session en cours utilise ce modèle ; arrêtez-la d’abord.",
  "asr.deleteDownloading": "Téléchargement en cours ; suppression possible une fois terminé.",
  "asr.deleted": "{model} supprimé ; {size} libérés.",
  "error.deleteModel": "Impossible de supprimer {model}",
  "error.code.model_not_downloaded": "Ce modèle n’est pas téléchargé",
  "error.code.model_in_use": "Le modèle est en cours d’utilisation ; arrêtez d’abord",
  "engineParam.compute_type_pair": "direct {fast}, définitif {final}",
  "engine.asr_bgm_robust_final": "{final_model} pour les sous-titres définitifs résiste le mieux à la musique de fond et au son des jeux en live (évaluation de lives japonais de kikitori)",
  "engine.asr_live_model_for_language": "{fast_model} est bien moins précis pour la langue suivante : {language} ; les sous-titres en direct utilisent donc aussi {model} (chargé une seule fois et partagé)",
};
