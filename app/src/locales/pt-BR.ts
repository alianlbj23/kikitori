/**
 * Brazilian Portuguese UI dictionary.
 *
 * Role: the pt-BR interface strings. Typed as `Messages`, so it must have
 * exactly the base (zh-TW) keys; plural messages need every category that
 * `Intl.PluralRules("pt-BR")` selects (one/many/other).
 */

import type { Messages } from "./types";

export const ptBR: Messages = {
  // ------------------------------------------------------------ common
  "common.unknown": "Desconhecido",
  "common.unknownReason": "motivo desconhecido",
  "common.unknownCause": "causa desconhecida",
  "common.auto": "Automático",
  "common.loading": "Carregando",
  "common.saving": "Salvando",
  "common.redownload": "Baixar novamente",
  "common.recommendedFor": "Recomendado: {roles}",
  "common.parenthetical": "{text} ({note})",
  "common.withReason": "{context}: {reason}",
  "common.listSeparator": ", ",
  "common.clauseSeparator": ", ",
  "common.strongSeparator": "; ",
  "common.altSeparator": " / ",
  "common.sentenceEnd": ".",
  "common.approxGb": "cerca de {gb} GB",

  // ------------------------------------------------------------ rail
  "rail.mode": "Modo",
  "rail.device": "Fonte de áudio",
  "mode.media.name": "Tradução de vídeo",
  "mode.media.note": "{source} para {target}",
  "mode.media.noteAuto": "Qualquer idioma detectado para {target}",
  "mode.media.noteOriginal": "{source} (somente original)",
  "mode.interpret.noteOriginal": "{own} ⇄ {partner} (somente original)",
  "mode.interpret.name": "Interpretação ao vivo",
  "mode.interpret.note": "{own} ⇄ {partner}",
  "device.hint.media": "A tradução de vídeo captura o som que o computador está reproduzindo.",
  "device.hint.interpret": "A interpretação ao vivo usa o microfone.",
  "device.hint.missing": "O dispositivo escolhido antes não está disponível; será usado o padrão do sistema.",
  "device.defaultNamed": "Padrão: {name}",
  "device.systemDefault": "Padrão do sistema",
  "start.start": "Iniciar",
  "start.stop": "Parar",
  "nav.label": "Páginas",
  "nav.transcript": "Transcrição",
  "nav.models": "Modelos",
  "nav.settings": "Configurações",
  "engine.restart": "Reiniciar mecanismo",
  "engine.failedNotice": "Não foi possível iniciar o mecanismo: {reason}",
  "engine.log": "Aviso do mecanismo: {message}",
  // ------------------------------------------------------------ engine message codes (hardware.md; params localized in lib/messages.ts)
  "engine.asr_gpu":
    "{gpu_name} ({vram_mb} MiB de VRAM): {fast_model} para legendas ao vivo, {final_model} para legendas finais ({compute_type})",
  "engine.asr_gpu_int8_saves_vram":
    "int8_float16 reduz pela metade a VRAM usada pelo reconhecimento de fala, deixando mais para o modelo de tradução",
  "engine.asr_final_no_speech_unreliable":
    "{final_model} não consegue distinguir fala de silêncio (no_speech); por isso, o no_speech de {fast_model} e o detector de voz filtram o que não é fala",
  "engine.asr_large_v3_remote_translation":
    "A tradução não roda neste computador, então as legendas finais usam {final_model} (qualidade semelhante, no_speech confiável, cerca de 0,33 s por frase)",
  "engine.asr_apple_silicon":
    "Apple Silicon: no macOS, o reconhecimento de fala roda na CPU; {fast_model} para legendas ao vivo, {final_model} para legendas finais",
  "engine.asr_apple_silicon_untested":
    "A latência no Apple Silicon ainda não foi medida; usando os mesmos modelos de uma CPU com 16 GB de RAM ou mais",
  "engine.asr_cpu":
    "O reconhecimento de fala roda na CPU (16 GB de RAM ou mais): {fast_model} para legendas ao vivo, {final_model} para legendas finais",
  "engine.asr_cpu_large_models_slow":
    "Os modelos medium e maiores precisam de 3,6 s ou mais por frase na CPU, lento demais para legendas ao vivo",
  "engine.asr_cpu_low":
    "Modelos leves mantêm baixos a latência e o uso de memória: {fast_model} para legendas ao vivo, {final_model} para legendas finais",
  "engine.cpu_low_ram": "{ram_gb} GB de RAM é menos que 16 GB",
  "engine.cpu_few_cores": "{physical_cores} núcleos físicos de CPU (no máximo {max_cores})",
  "engine.cpu_info_incomplete": "As informações de hardware estão incompletas",
  "engine.multiple_gpus": "{gpu_count} GPUs NVIDIA encontradas; usando {gpu_name}, a que tem mais VRAM",
  "engine.cuda_platform_unsupported":
    "O CTranslate2 não tem versão com CUDA para esta plataforma (somente Windows e Linux x86-64); o reconhecimento de fala usa a CPU",
  "engine.cuda_driver_too_old":
    "O driver NVIDIA {driver_version} é antigo demais (o CUDA 12 exige {min_driver} ou mais recente); o reconhecimento de fala usa a CPU até o driver ser atualizado",
  "engine.cuda_compute_capability_unknown":
    "Não foi possível ler a compute capability da GPU (o driver pode estar desatualizado); o reconhecimento de fala usa a CPU",
  "engine.cuda_compute_capability_unsupported":
    "{gpu_name} (compute capability {compute_capability}) não suporta a aceleração float16/int8 do CTranslate2; o reconhecimento de fala usa a CPU",
  "engine.gpu_runtime_missing":
    "O cuBLAS não está instalado: a aceleração por GPU exige baixar o componente de aceleração por GPU (cerca de 570 MB); até lá, o reconhecimento de fala usa a CPU",
  "engine.asr_vram_insufficient":
    "VRAM insuficiente para os modelos de reconhecimento de fala (são necessários cerca de {need_mb} MiB); o reconhecimento de fala usa a CPU",
  "engine.memory_unknown":
    "Não foi possível ler a quantidade de memória; usando os modelos de reconhecimento de fala mais leves",
  "engine.asr_low_quality_model": "{model} comete visivelmente mais erros de reconhecimento",
  "engine.asr_language_quality_low":
    "{model} reconhece mal o idioma {language} (taxa de erro acima de 20% em benchmarks públicos)",
  "engine.translation_remote":
    "O servidor de tradução não está neste computador ({base_url}); escolha um dos modelos dele na configuração personalizada da página Modelos",
  "engine.translation_cpu":
    "Nenhuma GPU utilizável: o modelo de tradução roda na CPU, então é escolhido o menor modelo (velocidade na CPU não medida)",
  "engine.translation_budget_vram":
    "Restam cerca de {budget_gb} GB de VRAM para o modelo de tradução após o reconhecimento de fala e uma reserva de 1 GB",
  "engine.translation_budget_unified":
    "Restam cerca de {budget_gb} GB de memória unificada para o modelo de tradução após o reconhecimento de fala e a reserva do sistema",
  "engine.translation_same_model":
    "As traduções ao vivo e final usam o mesmo modelo: cada frase leva menos de 0,3 s na GPU, enquanto trocar de modelo no Ollama leva de 1,5 a 18 s",
  "engine.translation_different_models": "As traduções ao vivo e final usam modelos diferentes",
  "engine.translation_better_model_available":
    "{model} foi escolhido entre os modelos instalados; instalar {better_model} oferece qualidade melhor",
  "engine.translation_not_needed":
    "{source_language} e {target_language} são a mesma língua falada: não é preciso modelo de tradução, apenas conversão de escrita",
  "engine.source_language_auto":
    "O idioma de escuta é detectado automaticamente: o modelo de tradução é escolhido para traduzir qualquer idioma para {target_language}",
  "engine.translation_server_unreachable":
    "Não foi possível conectar ao servidor de tradução {base_url} (o Ollama está em execução?); não é possível verificar os modelos instalados",
  "engine.translation_model_list_unavailable":
    "Não foi possível obter a lista de modelos do servidor de tradução: {error}",
  "engine.low_ram_for_translation":
    "Menos de 8 GB de RAM: pode não haver memória para executar o modelo de tradução e o reconhecimento de fala ao mesmo tempo",
  "engine.translation_model_not_installed": "O modelo de tradução recomendado {model} ainda não está instalado",
  "engine.translation_model_exceeds_budget":
    "{model} precisa de cerca de {model_gb} GB, mais que os {budget_gb} GB disponíveis; o Ollama executará parte dele na CPU, o que é mais lento",
  "engine.translation_unsupported":
    "Nenhum modelo de tradução recomendado suporta {source_language} → {target_language}",
  "engine.combination_not_object": "A combinação de modelos deve ser um objeto",
  "engine.asr_path_not_object": "{path} deve ser um objeto",
  "engine.asr_model_unknown":
    "{path}: modelo de reconhecimento de fala desconhecido {model} (disponíveis: {available})",
  "engine.asr_device_invalid": "{path}: o dispositivo deve ser automático, CPU ou CUDA",
  "engine.asr_compute_type_not_string": "{path}: a precisão deve ser um texto ou ficar vazia",
  "engine.asr_cpu_threads_invalid": "{path}: as threads de CPU devem ser um número inteiro de 0 a 256",
  "engine.asr_cuda_no_gpu": "{path}: CUDA está selecionado, mas nenhuma GPU NVIDIA foi encontrada",
  "engine.asr_cuda_platform_unsupported":
    "{path}: CUDA está selecionado, mas o CTranslate2 não tem versão com CUDA para esta plataforma (somente Windows e Linux x86-64)",
  "engine.asr_cuda_driver_too_old":
    "{path}: CUDA está selecionado, mas o driver NVIDIA {driver_version} é antigo demais (o CUDA 12 exige {min_driver} ou mais recente)",
  "engine.asr_cuda_compute_capability_unknown":
    "{path}: CUDA está selecionado, mas a compute capability da GPU é desconhecida (o driver pode estar desatualizado)",
  "engine.asr_cuda_compute_capability_unsupported":
    "{path}: CUDA está selecionado, mas {gpu_name} (compute capability {compute_capability}) não suporta a aceleração float16/int8 do CTranslate2",
  "engine.asr_cuda_cublas_missing":
    "{path}: CUDA está selecionado, mas o cuBLAS não está instalado; baixe primeiro o componente de aceleração por GPU",
  "engine.asr_compute_type_unknown": "{path}: precisão desconhecida {compute_type} (disponíveis: {available})",
  "engine.translation_model_name_invalid":
    "{field} deve ser um nome de modelo de 1 a {max_chars} caracteres, ou ficar vazio",
  "engine.asr_auto_uses_cpu":
    "{path}: o modo automático usará a CPU (a GPU não pode executar o reconhecimento de fala agora)",
  "engine.asr_compute_type_cpu_fallback":
    "{path}: a CPU não suporta {compute_type}; o CTranslate2 usará outra precisão (geralmente float32, mais lenta)",
  "engine.asr_compute_type_gpu_fallback":
    "{path}: a compute capability {compute_capability} não suporta {compute_type}; o CTranslate2 usará float32 ou int8_float32 (mais lento, mais VRAM)",
  "engine.asr_model_slow_on_cpu":
    "{path}: {model} precisa de 3,6 s ou mais por frase na CPU; as legendas terão um atraso perceptível",
  "engine.asr_model_not_downloaded":
    "{path}: o modelo de reconhecimento de fala {model} ainda não foi baixado; ele será baixado antes de iniciar",
  "engine.asr_model_language_unsupported": "{path}: {model} não reconhece o idioma {language}",
  "engine.asr_fast_larger_than_final":
    "O modelo ao vivo {fast_model} é maior que o modelo final {final_model}: as legendas ao vivo podem ficar mais lentas que as finais",
  "engine.asr_no_speech_unavailable":
    "Nem {fast_model} nem {final_model} conseguem filtrar o que não é fala com no_speech; canto e ruído têm mais chance de ser transcritos",
  "engine.translation_model_not_on_server": "O servidor de tradução não tem {model}",
  "engine.translation_model_language_unsupported":
    "{model} não suporta {source_language} → {target_language}",
  "engine.translation_license_non_commercial": "A licença de {model} ({license}) proíbe o uso comercial",
  "engine.translation_license_region_restricted":
    "A licença de {model} ({license}) não é válida nestas regiões: {regions}",
  "engine.translation_models_differ":
    "As traduções ao vivo e final usam modelos diferentes: o Ollama local leva de 1,5 a 18 s cada vez que troca de modelo",
  "engine.vram_overflow":
    "O reconhecimento de fala e a tradução precisam de cerca de {need_mb} MiB de VRAM, mais que os {available_mb} MiB disponíveis em {gpu_name}; podem ficar lentos ou não carregar",
  "engine.asr_ram_over_half": "Os modelos de reconhecimento de fala na CPU usam mais da metade da RAM",
  "engine.translation_disabled":
    "A tradução está desativada: só o texto original é exibido e nenhum modelo de tradução é necessário",
  "engineParam.fast_asr": "Reconhecimento das legendas ao vivo",
  "engineParam.final_asr": "Reconhecimento das legendas finais",
  "engineParam.translation_fast": "O modelo de tradução ao vivo",
  "engineParam.translation_final": "O modelo de tradução final",

  // ------------------------------------------------------------ status
  "status.starting": "Iniciando o mecanismo",
  "status.restarting": "O mecanismo parou inesperadamente; reiniciando",
  "status.failed": "Não foi possível iniciar o mecanismo",
  "status.stopped": "Mecanismo parado",
  "status.loading": "Carregando modelos",
  "status.running": "Ouvindo",
  "status.error": "Parado após um erro",
  "status.idle": "Pronto",
  "status.code.model_not_downloaded":
    "O modelo ainda não foi baixado. Baixe-o na página Modelos e inicie novamente.",
  "status.code.unknown_model": "O modelo nas suas configurações não existe. Escolha outro na página Modelos.",
  "status.code.device_not_found": "O dispositivo de áudio selecionado não foi encontrado. Escolha um dispositivo novamente.",
  "status.code.permission_denied":
    "O kikitori não tem permissão para usar o microfone ou gravar áudio. Permita nas configurações do sistema.",
  "status.code.device_lost": "O dispositivo de áudio foi desconectado. Verifique a conexão e inicie novamente.",
  "status.code.capture_unavailable": "Este computador não consegue capturar este tipo de fonte de áudio.",
  "status.code.capture_error": "Ocorreu um erro ao capturar o áudio.",
  "status.code.pipeline_error": "Ocorreu um erro ao processar a fala.",
  "status.code.load_error": "Falha ao carregar o modelo.",
  "status.code.translation_model_missing": "O servidor de tradução ainda não tem o modelo de tradução.",
  "status.code.translation_server_auth_failed": "O servidor de tradução recusou a solicitação — verifique a API key.",
  "status.errorDetail": "Ocorreu um erro: {detail}",
  "status.errorUnknown": "Ocorreu um erro desconhecido.",

  // ------------------------------------------------------------ notices
  "notice.close": "Fechar",
  "notice.closeLabel": "Fechar esta mensagem",
  "notice.settingsFieldsUnreadable": {
    one: "Não foi possível ler {count} campo do arquivo de configurações; usando o padrão por enquanto: {fields}",
    many: "Não foi possível ler {count} campos do arquivo de configurações; usando os padrões por enquanto: {fields}",
    other: "Não foi possível ler {count} campos do arquivo de configurações; usando os padrões por enquanto: {fields}",
  },
  "notice.initFailed": "Falha ao inicializar a janela: {reason}",

  // ------------------------------------------------------------ failure contexts
  "error.saveMode": "Não foi possível lembrar o modo",
  "error.saveDevice": "Não foi possível lembrar a fonte de áudio",
  "error.start": "Não foi possível iniciar",
  "error.stop": "Não foi possível parar",
  "error.showOverlay": "Não foi possível mostrar a janela de legendas",
  "error.hideOverlay": "Não foi possível ocultar a janela de legendas",
  "error.toggleClickThrough": "Não foi possível alternar o clique através",
  "error.applyRecommendation": "Não foi possível aplicar a configuração recomendada",
  "error.readSettings": "Não foi possível ler as configurações",
  "error.saveDismissal": "Não foi possível lembrar “Não lembrar novamente”",
  "error.saveUiLocale": "Não foi possível lembrar o idioma da interface",
  "error.setOverlayLocale": "Não foi possível mudar o idioma da janela de legendas",
  "error.readModels": "Não foi possível ler a lista de modelos",
  "error.detectHardware": "Não foi possível detectar o hardware",
  "error.downloadModel": "Não foi possível baixar {model}",
  "error.downloadGpuRuntime": "Não foi possível baixar o componente de aceleração por GPU",
  "error.openEula": "Não foi possível abrir os termos de licença; copie a URL para o navegador",
  "error.applyStyle": "Não foi possível aplicar a aparência das legendas",
  "error.openLicenses": "Não foi possível abrir a pasta de licenças; abra-a pelo caminho acima",
  "error.listDevices": "Não foi possível listar os dispositivos de áudio",
  "error.restartEngine": "Não foi possível reiniciar o mecanismo",
  "error.readOverlayState": "Não foi possível ler o estado da janela de legendas",
  "error.readBridge": "Não foi possível ler o estado do mecanismo",
  "error.readLanguages": "Não foi possível ler a lista de idiomas",
  "error.setTranslation": "Não foi possível alternar “Mostrar tradução”",
  "error.previewLanguages": "Não foi possível obter a combinação recomendada",

  // ------------------------------------------------------------ engine error codes
  "error.code.invalid_settings": "Configuração inválida",
  "error.code.settings_io_error":
    "Não foi possível salvar o arquivo de configurações; verifique o espaço em disco e as permissões",
  "error.code.session_active": "Já está em execução; pare primeiro",
  "error.code.device_not_found": "O dispositivo de áudio selecionado não foi encontrado; escolha-o novamente",
  "error.code.capture_unavailable": "Este computador não consegue capturar este tipo de fonte de áudio",
  "error.code.capture_error": "Ocorreu um erro ao capturar o áudio",
  "error.code.unknown_model": "O mecanismo não conhece este modelo",
  "error.code.download_in_progress": "Este item já está sendo baixado; aguarde a conclusão",
  "error.code.license_not_accepted":
    "Aceite o NVIDIA CUDA EULA antes de baixar o componente de aceleração por GPU",
  "error.code.gpu_runtime_unsupported":
    "Esta plataforma não suporta o componente de aceleração por GPU (somente Windows e Linux em x86-64)",
  "error.code.pull_unsupported":
    "O servidor de tradução não é um Ollama, então o kikitori não pode baixar modelos de tradução por você",
  "error.code.translation_server_unreachable": "Não foi possível conectar ao servidor de tradução",
  "error.code.translation_server_auth_failed": "O servidor de tradução recusou a solicitação — verifique a API key",
  "error.code.unknown_method": "A versão do mecanismo não corresponde à do aplicativo; esta operação não é suportada",
  "error.code.invalid_params": "Parâmetros inválidos foram enviados ao mecanismo",
  "error.code.invalid_request": "Uma solicitação malformada foi enviada ao mecanismo",
  "error.code.internal_error": "Erro interno do mecanismo",
  "error.code.other": "O mecanismo informou um erro ({code})",
  "error.engineSentence": "{head}.",
  "error.engineDetail": "{head}: {message}",
  "error.timeout": {
    one: "O mecanismo não respondeu em {count} segundo. Tente novamente mais tarde ou reinicie o mecanismo.",
    many: "O mecanismo não respondeu em {count} segundos. Tente novamente mais tarde ou reinicie o mecanismo.",
    other: "O mecanismo não respondeu em {count} segundos. Tente novamente mais tarde ou reinicie o mecanismo.",
  },
  "error.disconnected":
    "O mecanismo não está em execução. Aguarde ele reiniciar ou clique em “Reiniciar mecanismo” na área de status.",
  "error.invalidInput": "Valor inválido: {message}",
  "error.generic": "Ocorreu um erro: {message}",

  // ------------------------------------------------------------ transcript
  "transcript.title": "Transcrição",
  "transcript.lede":
    "As legendas ao vivo aparecem esmaecidas primeiro e passam para a tradução final quando a frase termina.",
  "transcript.overlayVisible": "Mostrar janela de legendas",
  "transcript.clickThrough": "Clique através",
  "transcript.clear": "Limpar",
  "transcript.showTranslation": "Mostrar tradução",
  "transcript.noTranslation": "(sem tradução)",
  "transcript.translating": "Traduzindo",
  "transcript.emptyTitle": "Ainda não há legendas",
  "transcript.emptyBody":
    "Escolha um modo e uma fonte de áudio e clique em Iniciar. Cada frase ouvida aparece aqui, com o original em cima e a tradução embaixo.",
  "transcript.langTitle": "{source} para {target}",
  "transcript.shortcutCtrl": "Ctrl (⌘ no macOS)",
  "transcript.shortcutOn":
    "Com o clique através ativado, os cliques passam pela janela de legendas. Pressione {keys} para alternar a qualquer momento.",
  "transcript.shortcutTaken":
    "O atalho {keys} está sendo usado por outro aplicativo; use o botão aqui para alternar o clique através.",

  // ------------------------------------------------------------ models page
  "models.title": "Modelos",
  "models.lede":
    "O reconhecimento de fala roda neste computador; a tradução vai para o serviço definido na página Configurações.",
  "models.refresh": "Detectar novamente",

  "hw.title": "Este computador",
  "hw.status": "Status",
  "hw.detecting": "Detectando",
  "hw.os": "Sistema operacional",
  "hw.cpu": "Processador",
  "hw.memory": "Memória",
  "hw.gpu": "Placa de vídeo",
  "hw.cublas": "cuBLAS (reconhecimento na GPU)",
  "hw.server": "Serviço de tradução",
  "hw.cpuValue": "{model}, {cores}",
  "hw.cores": "{physical} núcleos, {logical} threads",
  "hw.threads": { one: "{count} thread", many: "{count} threads", other: "{count} threads" },
  "hw.coresUnknown": "número de núcleos desconhecido",
  "hw.appleSilicon": "Apple Silicon (memória compartilhada com o processador)",
  "hw.gpuVram": "{size} de memória de vídeo",
  "hw.gpuDriver": "driver {version}",
  "hw.gpuCompute": "compute capability {capability}",
  "hw.noNvidia": "Nenhuma placa de vídeo NVIDIA detectada",
  "hw.cublasNotUsed": "Não usado nesta plataforma",
  "hw.cublasAvailable": "Disponível ({source})",
  "hw.cublasSourceUnknown": "origem desconhecida",
  "hw.cublasMissing": "Não instalado",
  "hw.cublasSource.runtime": "componente de aceleração por GPU baixado pelo kikitori",
  "hw.cublasSource.configured": "pasta definida na configuração personalizada",
  "hw.cublasSource.system": "instalação do CUDA no sistema",
  "hw.serverNotDetected": "Não detectado",
  "hw.serverLocal": "local",
  "hw.serverRemote": "remoto",
  "hw.serverModelsUnknown": "lista de modelos desconhecida",
  "hw.serverModelCount": { one: "{count} modelo", many: "{count} modelos", other: "{count} modelos" },
  "hw.serverReachable": "{url} ({where}, {models})",
  "hw.serverUnreachable": "{url} (inacessível)",
  "hw.serverUnreachableError": "{url} (inacessível: {error})",
  "hw.memoryAvailable": "{total} ({available} disponível)",
  "hw.incomplete": "Detecção incompleta: {error}",

  "rec.title": "Configuração recomendada",
  "rec.pending": "As recomendações aparecem aqui quando a detecção terminar.",
  "rec.tier": "Nível de hardware",
  "rec.fastAsr": "Reconhecimento das legendas ao vivo",
  "rec.finalAsr": "Reconhecimento da tradução final",
  "rec.fastTranslation": "Tradução das legendas ao vivo",
  "rec.finalTranslation": "Tradução final",
  "rec.budget": "Memória disponível para tradução",
  "rec.notLocal":
    "Sem recomendação (o serviço de tradução não é local); escolha um na configuração personalizada",
  "rec.gpuRuntimeMissing":
    "Depois que você baixar o componente de aceleração por GPU abaixo, a recomendação usará a placa de vídeo.",
  "rec.apply": "Aplicar configuração recomendada",
  "rec.applyNote": "Define os modelos de reconhecimento, o dispositivo, a precisão, as threads e os modelos de tradução.",
  "rec.applied":
    "Configuração recomendada aplicada: legendas ao vivo {fast}, tradução final {final} ({device}).",
  "rec.appliedWhileRunning":
    "Configuração recomendada aplicada: legendas ao vivo {fast}, tradução final {final} ({device}). Ela entra em vigor na próxima vez que você iniciar.",
  "tier.nvidia_8gb": "Placa de vídeo NVIDIA (8 GB ou mais)",
  "tier.nvidia_4gb": "Placa de vídeo NVIDIA (4–8 GB)",
  "tier.nvidia_lt4gb": "Placa de vídeo NVIDIA (menos de 4 GB)",
  "tier.cpu": "CPU (16 GB de memória ou mais)",
  "tier.cpu_low": "CPU (configuração leve)",
  "tier.apple_silicon": "Apple Silicon (CPU)",
  "threads.auto": "threads automáticas",
  "threads.count": { one: "{count} thread", many: "{count} threads", other: "{count} threads" },
  "computeType.auto": "precisão automática",

  "machine.gpu": "{name}, {gb} GB",
  "machine.ram": "{gb} GB de memória",
  "machine.named": "{name}, {ram}",
  "machine.thisCpu": "o processador deste computador",
  "machine.thisCpuRam": "o processador deste computador, {ram}",
  "summary.asrSameDevice": "ao vivo {fast}, final {final} ({device})",
  "summary.asrSplit": "ao vivo {fast} ({fastDevice}), final {final} ({finalDevice})",
  "summary.translationKept": "modelos de tradução mantidos (o serviço de tradução não é local)",
  "summary.translationSame": "tradução {model}",
  "summary.translationSplit": "tradução ao vivo {fast}, tradução final {final}",
  "summary.full": "{asr}; {translation}",
  "firstRun.applied": "A configuração recomendada para este computador ({machine}) foi aplicada: {summary}.",
  "firstRun.appliedGpuMissing":
    "A configuração recomendada para este computador ({machine}) foi aplicada: {summary}. Falta o componente de aceleração por GPU para a placa de vídeo, então a CPU está sendo usada por enquanto; baixe-o na página Modelos para usar a placa de vídeo.",
  "firstRun.applyFailed":
    "Não foi possível aplicar a configuração recomendada: {reason} Verifique a configuração atual na página Modelos e aplique a recomendação lá, se necessário.",
  "firstRun.detectFailed":
    "Não foi possível detectar o hardware deste computador: {reason} Usando a configuração padrão por enquanto (ao vivo {fast}, final {final}); você pode detectar novamente na página Modelos e aplicar a recomendação.",
  "firstRun.review": "Revisar na página Modelos",
  "offer.text": "Há uma configuração melhor para este computador: {summary}.",
  "offer.apply": "Aplicar",
  "offer.dismiss": "Não lembrar novamente",
  "missing.itemFailed": "Falha ao baixar {model}: {error}",
  "missing.downloading": "Baixando {models}; você poderá iniciar quando terminar.",
  "missing.failed": "{items}. Você pode baixar novamente.",
  "missing.offer": "É preciso baixar {models} (cerca de {size} no total) antes de iniciar.",
  "missing.done": "Os modelos foram baixados. Você já pode clicar em Iniciar.",
  "missing.download": "Baixar modelos que faltam",
  "startdl.title": "Baixando modelos antes de iniciar",
  "startdl.itemAsr": "Modelo de reconhecimento de fala {model}",
  "startdl.itemTranslation": "Modelo de tradução {model}",
  "startdl.pending": "Aguardando",
  "startdl.preparing": "Preparando o download",
  "startdl.progress": "{percent}%, {done} de {total}",
  "startdl.done": "Concluído",
  "startdl.failed": "Falha no download: {error}",
  "startdl.stillMissing": "{model} já foi baixado, mas o mecanismo ainda informa que ele está faltando.",
  "startdl.downloading": "As legendas começam automaticamente quando os downloads terminarem.",
  "startdl.starting": "Downloads concluídos; iniciando.",
  "startdl.failedHelp": "Um download falhou. Clique em Tentar novamente ou escolha outros modelos na página Modelos.",
  "startdl.pullUnsupported":
    "O servidor de tradução {url} não é um Ollama, então o kikitori não pode baixar modelos de tradução por você. Instale {models} nesse servidor por conta própria ou escolha um modelo instalado na página Modelos. Você também pode desativar “Mostrar tradução” para ver só o original.",
  "startdl.serverUnreachable":
    "Não foi possível conectar ao servidor de tradução {url}. Se você usa o Ollama, inicie-o primeiro (abra o aplicativo Ollama ou execute ollama serve em um terminal) e clique em Tentar novamente. Você também pode desativar “Mostrar tradução” para ver só o original.",
  "startdl.authFailed":
    "O servidor de tradução {url} recusou a solicitação — verifique a API key (a variável de ambiente indicada em Configurações) e clique em Tentar novamente.",
  "startdl.retry": "Tentar novamente",
  "startdl.cancel": "Cancelar",
  "startdl.cancelLabel": "Parar de aguardar os downloads e não iniciar automaticamente",

  "gpu.title": "Componente de aceleração por GPU",
  "gpu.lede":
    "Para acelerar o reconhecimento de fala com uma placa de vídeo NVIDIA, é preciso a biblioteca cuBLAS da NVIDIA. O kikitori não a inclui; ela é baixada do PyPI depois que você aceita a licença dela.",
  "gpu.name": "Componente de aceleração por GPU",
  "gpu.unsupported":
    "A plataforma deste computador não usa este componente: a aceleração por GPU é só para Windows e Linux em x86-64 com placa de vídeo NVIDIA. Nas outras plataformas, a fala é sempre reconhecida na CPU.",
  "gpu.progressLabel": "Progresso do download do componente de aceleração por GPU",
  "gpu.finishing": "Baixado; verificando e extraindo (alguns segundos)",
  "gpu.progress": "{percent}%, {done} / {total}",
  "gpu.installed": "{what} instalado.",
  "gpu.installedAt": "{what} instalado em {path}.",
  "gpu.noCublas":
    "Instalado, mas nenhum cuBLAS utilizável foi detectado. Reinicie o mecanismo e clique em “Detectar novamente”.",
  "gpu.sizeApprox": "cerca de {size}",
  "gpu.sizeUnknown": "algumas centenas de MB",
  "gpu.situationMissing":
    "A placa de vídeo NVIDIA deste computador pode acelerar o reconhecimento, mas falta este componente, então a CPU está sendo usada por enquanto.",
  "gpu.situationNoGpu": "Nenhuma placa de vídeo NVIDIA detectada; sem ela, não é preciso baixar isto.",
  "gpu.readLicense": "Ler a licença",
  "gpu.download": "Baixar componente de aceleração por GPU",
  "gpu.consentNotice":
    "Isto baixa o NVIDIA cuBLAS ({package}, {size}) do PyPI. Ao usá-lo, você concorda com o NVIDIA CUDA EULA ({license}).",
  "gpu.lastFailed": "O último download falhou: {error}",
  "gpu.agree": "Concordo com o NVIDIA CUDA EULA",
  "gpu.installedRestarting":
    "Componente de aceleração por GPU instalado. Reiniciando o mecanismo para usar a GPU.",
  "gpu.installedBusy":
    "Componente de aceleração por GPU instalado. Há uma tarefa em andamento, então o mecanismo não foi reiniciado automaticamente; reinicie-o quando terminar para usar a GPU.",

  "asr.title": "Modelos de reconhecimento de fala",
  "asr.lede":
    "As legendas ao vivo precisam de velocidade e as traduções finais, de precisão; cada uma pode usar um modelo diferente.",
  "asr.colModel": "Modelo",
  "asr.colSize": "Tamanho",
  "asr.colSpeed": "Velocidade",
  "asr.colMemory": "Memória",
  "asr.colLicense": "Licença",
  "asr.colStatus": "Status",
  "asr.inUse": "Em uso: {roles}",
  "asr.noSpeech": "Sem detecção de não fala",
  "asr.noSpeechHelp":
    "Este modelo não consegue identificar com confiabilidade quando não há fala; nas traduções finais, o julgamento do modelo das legendas ao vivo é usado para filtrar ruído.",
  "asr.memoryCpu": "CPU {size}",
  "asr.memoryGpu": "GPU {size}",
  "asr.progressLabel": "Progresso do download de {model}",
  "asr.progress": "{percent}%, {done}",
  "asr.downloaded": "Baixado",
  "asr.download": "Baixar",
  "asr.downloadFailed": "Falha no download: {error}",
  "role.fast": "ao vivo",
  "role.final": "final",
  "speed.fastest": "Mais rápido",
  "speed.fast": "Rápido",
  "speed.medium": "Médio",
  "speed.slow": "Lento",
  "speed.slowest": "Mais lento",
  "download.done": "{model} baixado.",
  "download.failed": "Falha ao baixar {what}: {error}",

  "tr.title": "Modelos de tradução",
  "tr.unreachable": "Não foi possível acessar o serviço de tradução em {url}. Se você usa um Ollama local, inicie-o primeiro.",
  "tr.unreachableError":
    "Não foi possível acessar o serviço de tradução em {url} ({error}). Se você usa um Ollama local, inicie-o primeiro.",
  "tr.installedCount": {
    one: "{count} modelo instalado no serviço de tradução em {url}.",
    many: "{count} modelos instalados no serviço de tradução em {url}.",
    other: "{count} modelos instalados no serviço de tradução em {url}.",
  },
  "tr.installedCountLocal": {
    one: "{count} modelo instalado no serviço de tradução em {url} (local).",
    many: "{count} modelos instalados no serviço de tradução em {url} (local).",
    other: "{count} modelos instalados no serviço de tradução em {url} (local).",
  },
  "tr.nonCommercial": "Não comercial",
  "tr.regionExcluded": "Licença não válida em: {regions}",
  "tr.license": "Licença {license}",
  "tr.unlisted": "Fora da lista do kikitori; verifique a licença por conta própria",
  "tr.stateUnknown": "Estado da instalação desconhecido",
  "tr.stateInstalled": "Instalado",
  "tr.stateMissing": "Não instalado",

  "combo.title": "Configuração personalizada",
  "combo.lede":
    "As alterações são verificadas na hora em relação a este computador; uma configuração com erros não pode ser salva. Uma nova configuração entra em vigor na próxima vez que você clicar em Iniciar.",
  "combo.fastModel": "Modelo de reconhecimento para legendas ao vivo",
  "combo.finalModel": "Modelo de reconhecimento para traduções finais",
  "combo.device": "Dispositivo de computação",
  "combo.deviceAuto": "Automático (usa uma GPU NVIDIA se houver; caso contrário, a CPU)",
  "combo.deviceCpu": "CPU",
  "combo.deviceCuda": "GPU NVIDIA (CUDA)",
  "combo.deviceHelp":
    "Os dois caminhos compartilham um dispositivo: se a GPU falhar durante a execução, ela não é tentada de novo.",
  "combo.translationFast": "Modelo de tradução para legendas ao vivo",
  "combo.translationFinal": "Modelo de tradução para traduções finais",
  "combo.advanced": "Avançado",
  "combo.advancedHelp":
    "A precisão e o número de threads costumam ficar com a configuração recomendada. Quando vazio ou Automático, o mecanismo decide conforme o dispositivo realmente usado (int8 na CPU, float16 na GPU).",
  "combo.fastComputeType": "Precisão para legendas ao vivo",
  "combo.finalComputeType": "Precisão para traduções finais",
  "combo.fastThreads": "Threads de CPU para legendas ao vivo",
  "combo.finalThreads": "Threads de CPU para traduções finais",
  "combo.cudaDirs": "Pastas do cuBLAS 12 instalado por você",
  "combo.cudaDirsPlaceholder":
    "Um caminho completo por linha, por exemplo C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v12.8\\bin",
  "combo.cudaDirsHelp":
    "Só é necessário se você instalou o CUDA Toolkit por conta própria. O componente de aceleração por GPU baixado acima é usado automaticamente e não precisa ser listado.",
  "combo.save": "Salvar configuração",
  "combo.reset": "Voltar às configurações atuais",
  "combo.notInList": "{value} (fora da lista)",
  "combo.notDownloaded": "{id} (não baixado)",
  "combo.saved": "Configuração salva.",
  "combo.savedNextStart": "Salvo; entra em vigor na próxima vez que você iniciar.",
  "check.fixFirst": "Corrija primeiro os campos marcados.",
  "check.checking": "Verificando esta configuração",
  "check.okWithWarnings": "Este computador pode usar esta configuração, mas atenção:",
  "check.ok": "Este computador pode usar esta configuração.",
  "check.notOk": "Esta configuração não pode ser usada agora:",
  "check.rejected": "O mecanismo não aceita esta configuração: {message}",
  "check.unavailable":
    "Não é possível verificar a configuração agora ({reason}). Você ainda pode salvar; o mecanismo verifica o formato das configurações ao salvar.",
  "check.engineNotReady": "O mecanismo ainda não está pronto",
  "form.fixFields": "Corrija os campos marcados.",
  "form.engineRejected": "O mecanismo recusou este valor: {message}",

  // ------------------------------------------------------------ settings page
  "settings.title": "Configurações",
  "settings.path": "Arquivo de configurações: {path}",
  "settings.uiLocaleLegend": "Idioma da interface",
  "settings.uiLocaleLabel": "Idioma da interface",
  "settings.uiLocaleSystem": "Seguir o sistema",
  "settings.uiLocaleHelp":
    "Aplica-se na hora às janelas principal e de legendas. “Seguir o sistema” usa o idioma do seu sistema operacional ou, se ele não estiver disponível, o inglês.",
  "settings.translationLegend": "Serviço de tradução",
  "settings.translationLede":
    "Qualquer API compatível com OpenAI funciona. A URL padrão de um Ollama local é http://localhost:11434/v1.",
  "settings.baseUrl": "URL do serviço",
  "settings.apiKeyEnv": "Variável de ambiente da API key",
  "settings.apiKeyEnvHelp":
    "Informe só o nome da variável. Defina a chave em si como variável de ambiente do sistema; o kikitori nunca armazena chaves. Um Ollama local não precisa de chave.",
  "settings.translationModelsHelp":
    "Os modelos de tradução são escolhidos na configuração personalizada da página Modelos, onde a licença e a carga neste computador também são verificadas.",
  "settings.regionLegend": "Regiões de licença dos modelos de tradução",
  "settings.regionLede":
    "Alguns modelos de tradução (por exemplo, Tencent HY-MT) não são licenciados para a União Europeia, o Reino Unido ou a Coreia do Sul. O kikitori não tem como saber onde você está, por isso não os recomenda por padrão.",
  "settings.regionConsent":
    "Confirmo que não uso o kikitori na União Europeia, no Reino Unido nem na Coreia do Sul e concordo com os termos de licença desses modelos de tradução.",
  "settings.regionHelp":
    "Quando marcado, a configuração recomendada também considera modelos com restrição de região. Quando desmarcado, você ainda pode escolhê-los na configuração personalizada, com um aviso.",
  "settings.languageLegend": "Idiomas",
  "settings.languageLede":
    "Escolha o idioma que você ouve e o idioma das legendas; os modelos recomendados para eles aparecem abaixo. Nada é baixado agora: os modelos que faltam são baixados na primeira vez que você clicar em Iniciar.",
  "settings.languageModeMedia": "Estes são os idiomas da tradução de vídeo.",
  "settings.languageModeInterpret": "Estes são os idiomas da interpretação ao vivo.",
  "settings.source": "Idioma de escuta",
  "settings.sourceHelp":
    "O idioma falado no vídeo. Com “Detectar automaticamente”, cada frase é identificada primeiro, e frases muito curtas podem ser identificadas errado.",
  "settings.own": "Meu idioma",
  "settings.ownHelp": "O idioma que você fala; o que a outra pessoa diz é traduzido para ele.",
  "settings.languageFilter": "Digite para filtrar",
  "settings.languageFilterLabel": "Filtrar {field}",
  "settings.applyRecommended": "Também mudar para os modelos recomendados",
  "settings.applyRecommendedHelp":
    "Muda os modelos de reconhecimento, o dispositivo e os modelos de tradução; se desmarcado, só os idiomas mudam.",
  "settings.saveLanguages": "Salvar idiomas",
  "settings.languagesSaved": "Idiomas salvos.",
  "settings.languagesSavedWithModels": "Idiomas salvos, e os modelos recomendados já estão em uso.",
  "settings.languagesSavedNoPreview":
    "Idiomas salvos; a combinação recomendada não estava pronta, então os modelos não mudaram.",
  "settings.languagesSavedNextStart": "Salvo; entra em vigor na próxima vez que você iniciar.",
  "settings.languagesUnavailable": "Os idiomas podem ser escolhidos quando o mecanismo estiver pronto.",
  "language.auto": "Detectar automaticamente",
  "preview.title": "Combinação recomendada",
  "preview.checking": "Calculando a combinação recomendada para estes idiomas",
  "preview.unavailable": "A combinação recomendada não está disponível agora: {reason}",
  "preview.translation": "Modelo de tradução",
  "preview.installed": "Instalado",
  "preview.downloadOnFirstUse": "Baixado no primeiro uso, cerca de {size}",
  "preview.downloadUnknownSize": "Baixado no primeiro uso (tamanho desconhecido)",
  "preview.installedUnknown": "Não é possível saber se está instalado (o servidor de tradução não respondeu)",
  "preview.notNeeded": "Nenhuma tradução necessária",
  "preview.translationOff": "“Mostrar tradução” está desativado, então nenhum modelo de tradução é necessário",
  "preview.unsupported": "Nenhum modelo de tradução suporta esta combinação de idiomas",
  "preview.remote":
    "O servidor de tradução não está neste computador; escolha o modelo dele na configuração personalizada da página Modelos",
  "preview.allInstalled": "Todos os modelos necessários estão instalados.",
  "preview.total": "A primeira inicialização baixa cerca de {size}.",
  "preview.totalUnknown":
    "A primeira inicialização baixa modelos, alguns de tamanho desconhecido (cerca de {size} conhecidos).",
  "settings.target": "Idioma das legendas",
  "settings.targetHelp":
    "O idioma das legendas. Quando é a mesma língua falada que o idioma de escuta, nada é traduzido; o chinês é apenas convertido entre sistemas de escrita.",
  "settings.partner": "Idioma da outra pessoa",
  "settings.partnerHelp":
    "O que você diz é traduzido para este idioma, e o que a outra pessoa diz, para o seu. Os dois precisam ser diferenciados, por isso não podem ser iguais (chinês tradicional e simplificado contam como o mesmo).",
  "settings.styleLegend": "Legendas",
  "settings.showTranslation": "Mostrar tradução",
  "settings.showTranslationHelp":
    "Quando desativado, só o original é exibido, no tamanho e na cor da legenda principal: o mecanismo não traduz nada, não precisa de servidor de tradução e deixa a GPU livre. A mudança durante a execução vale na hora; as linhas já exibidas não são traduzidas depois.",
  "settings.previewOriginal": "次の駅で降りましょう。",
  "settings.previewTranslation": "Vamos descer na próxima estação.",
  "settings.fontSize": "Tamanho da fonte da tradução",
  "settings.opacity": "Opacidade do fundo",
  "settings.position": "Posição das legendas",
  "settings.positionBottom": "Parte inferior da janela",
  "settings.positionTop": "Parte superior da janela",
  "settings.lines": "Frases exibidas ao mesmo tempo",
  "settings.linesOption": { one: "{count} frase", many: "{count} frases", other: "{count} frases" },
  "settings.save": "Salvar configurações",
  "settings.saved": "Configurações salvas.",
  "settings.savedRegion":
    "Configurações salvas. A configuração recomendada será recalculada para a nova região de licença (veja a página Modelos).",

  // ------------------------------------------------------------ validation
  "validation.urlScheme": "A URL deve começar com http:// ou https://.",
  "validation.urlEmpty": "Informe a URL do serviço de tradução.",
  "validation.urlInvalid": "Esta não é uma URL válida.",
  "validation.envPattern": "Use apenas letras, dígitos e sublinhados, sem começar com um dígito.",
  "validation.envTooLong": "O nome é longo demais (no máximo {max} caracteres).",
  "validation.partner":
    "O idioma da outra pessoa deve ser diferente do seu (chinês tradicional e simplificado contam como o mesmo idioma).",
  "validation.language": "Escolha um idioma da lista.",
  "validation.fontSize": "O tamanho da fonte deve estar entre {min} e {max}.",
  "validation.opacity": "A opacidade do fundo deve estar entre 0% e 100%.",
  "validation.position": "Escolha superior ou inferior.",
  "validation.lines": "O número de frases deve estar entre {min} e {max}.",
  "validation.asrModel": "Escolha um modelo de reconhecimento de fala.",
  "validation.device": "Escolha um dispositivo de computação.",
  "validation.translationEmpty": "Informe o nome de um modelo de tradução.",
  "validation.translationTooLong": "Os nomes de modelo têm no máximo {max} caracteres.",
  "validation.computeType": "Escolha uma precisão.",
  "validation.threads": "O número de threads deve estar entre {min} e {max}; deixe vazio para automático.",
  "validation.cudaDirsTooMany": "No máximo {max} pastas.",
  "validation.cudaDirNotAbsolute": "“{path}” não é um caminho completo (por exemplo, C:\\CUDA\\bin).",

  // ------------------------------------------------------------ about
  "about.title": "Sobre",
  "about.engine": "Mecanismo {version}, versão do protocolo {protocol}.",
  "about.engineFake":
    "Mecanismo {version}, versão do protocolo {protocol}; usando o mecanismo simulado no momento (modo de desenvolvimento).",
  "about.engineOffline": "O mecanismo ainda não está conectado.",
  "about.license": "O kikitori é distribuído sob a licença {license}.",
  "about.notice":
    "Os componentes de terceiros deste programa, como o Intel® OpenMP, são fornecidos sob seus próprios termos de licença; não é permitido fazer engenharia reversa deles.",
  "about.thirdPartyHead": "Textos completos das licenças dos componentes de terceiros:",
  "about.thirdPartyApp":
    "O aplicativo em si (crates Tauri/Rust, pacotes do frontend e o LICENSE do kikitori): ",
  "about.thirdPartyEngine": "Mecanismo de fala (pacotes Python): ",
  "about.thirdPartyDev":
    "As versões de desenvolvimento não incluem pastas de licenças. Nas versões de lançamento, os textos das licenças ficam nas pastas licenses (o aplicativo) e kikitori-engine/THIRD_PARTY_LICENSES (o mecanismo de fala) do diretório de instalação.",
  "about.openAppLicenses": "Abrir a pasta de licenças de terceiros do aplicativo",
  "about.openEngineLicenses": "Abrir a pasta de licenças de terceiros do mecanismo",
  "about.checkUpdate": "Verificar atualizações",

  // ------------------------------------------------------------ updates
  "update.available": "O kikitori {version} está disponível para baixar e instalar.",
  "update.install": "Baixar e instalar",
  "update.downloading": "Baixando a atualização.",
  "update.progress": "Baixando a atualização: {percent}%",
  "update.installed": "O kikitori {version} foi instalado e entra em vigor após reiniciar.",
  "update.restartNow": "Reiniciar agora",
  "update.checking": "Verificando",
  "update.found": "Nova versão {version} disponível.",
  "update.latest": "Você está com a versão mais recente.",
  "update.checkFailed": "Não foi possível verificar atualizações: {reason}",
  "update.failed": "Falha na atualização: {reason}",
  "update.relaunchFailed":
    "Não foi possível reiniciar automaticamente; feche e abra o kikitori novamente. ({reason})",

  // ------------------------------------------------------------ overlay
  "overlay.title": "Legendas do kikitori",
  "overlay.drag": "Arraste para mover",
  "overlay.hide": "Ocultar",
  "overlay.hint":
    "As legendas aparecem aqui. Arraste a janela para movê-la; arraste o canto inferior direito para redimensioná-la.",

  // ------------------------------------------------------------ transcript export, reading rhythm, OBS text file, first use and diagnostics
  "export.button": "Exportar",
  "export.content": "Conteúdo",
  "export.contentBoth": "Original e tradução",
  "export.contentOriginal": "Só o original",
  "export.contentTranslation": "Só a tradução",
  "export.help": "Exporta todas as frases completas desta sessão (não só as que ainda estão na tela) para kikitori/transcripts, na sua pasta Documentos. O CSV abre direto no Excel.",
  "export.empty": "Ainda não há frases completas para exportar.",
  "export.saved": "Transcrição salva: {path}",
  "export.savedAuto": "A transcrição desta sessão foi salva automaticamente (SRT e TXT): {path}",
  "export.openFolder": "Abrir pasta",
  "error.export": "Não foi possível salvar a transcrição",
  "error.autoSave": "Não foi possível salvar a transcrição automaticamente",
  "error.openFolder": "Não foi possível abrir a pasta; abra-a pelo caminho mostrado em Configurações",
  "error.applyObs": "Não foi possível aplicar a configuração do arquivo de texto para o OBS",
  "error.openOllama": "Não foi possível abrir a página de download do Ollama; abra https://ollama.com/download no navegador",
  "settings.minDisplay": "Mostrar cada linha por pelo menos (segundos)",
  "settings.minDisplayHelp": "Quando a fala é rápida, uma linha nova espera a anterior ficar na tela por esse tempo antes de substituí-la, para dar tempo de ler. 0 substitui na hora.",
  "settings.idleClear": "Esmaecer sem legendas novas (segundos)",
  "settings.idleClearHelp": "Depois desse tempo desde a última linha, a janela de legendas esmaece e o arquivo de texto para o OBS é esvaziado. 0 mantém as legendas na tela.",
  "output.legend": "Transcrições e OBS",
  "output.autoSave": "Salvar automaticamente ao fim de cada sessão (SRT e TXT)",
  "output.autoSaveHelp": "Ao clicar em Parar ou quando o mecanismo para, a transcrição da sessão (original e tradução) é salva na pasta abaixo.",
  "output.transcriptsPath": "Salvo em: {path}",
  "output.noDocuments": "A pasta Documentos não foi encontrada; não é possível salvar.",
  "output.openTranscripts": "Abrir pasta de transcrições",
  "output.obs": "Gravar as legendas em um arquivo de texto (para o OBS)",
  "output.obsOff": "Desligado",
  "output.obsOriginal": "Original",
  "output.obsTranslation": "Tradução",
  "output.obsBoth": "Original e tradução",
  "output.obsHelp": "Quando ligado, as legendas da janela de legendas são gravadas em um arquivo de texto conforme aparecem. No OBS, adicione uma fonte Texto (GDI+), marque Ler do arquivo e escolha este arquivo. Nenhuma porta de rede é aberta.",
  "output.obsPath": "Arquivo de texto: {path}",
  "output.obsError": "Não foi possível gravar o arquivo de texto para o OBS: {error}",
  "output.openObs": "Abrir pasta do arquivo para o OBS",
  "validation.minDisplay": "Deve ficar entre {min} e {max} segundos.",
  "validation.idleClear": "Deve ficar entre {min} e {max} segundos; 0 mantém as legendas na tela.",
  "validation.obsOutput": "Escolha o que gravar.",
  "guide.local": "Não foi possível conectar ao serviço de tradução em {url}. Para ver traduções, instale e abra o Ollama (gratuito, roda neste computador) e clique em Detectar novamente. Você também pode mostrar só o original por enquanto.",
  "guide.remote": "Não foi possível conectar ao serviço de tradução em {url}. Verifique se ele está em execução e se o endereço está correto e clique em Detectar novamente; ou mostre só o original por enquanto.",
  "guide.install": "Instalar o Ollama",
  "guide.originalOnly": "Mostrar só o original",
  "guide.recheck": "Detectar novamente",
  "transcript.shortcutTranslation": "Pressione {keys} para ligar ou desligar Mostrar tradução.",
  "transcript.shortcutClear": "Pressione {keys} para limpar as legendas da janela de legendas e do arquivo de texto para o OBS.",
  "transcript.shortcutActionTaken": "O atalho {keys} ({action}) está sendo usado por outro aplicativo e não pode ser usado.",
  "shortcut.toggleTranslation": "alternar a tradução",
  "shortcut.clearSubtitles": "limpar as legendas",
  "about.copyDiagnostics": "Copiar diagnóstico",
  "about.diagnosticsHelp": "Anexe ao relatar um problema: versões, sistema operacional, detecção de hardware, configurações (da API key, só o nome da variável de ambiente, nunca a chave) e as últimas mensagens do mecanismo. Nomes de usuário nos caminhos ficam ocultos.",
  "about.diagnosticsCopied": "Copiado para a área de transferência.",
  "about.diagnosticsManual": "Não foi possível gravar na área de transferência; copie o texto abaixo manualmente.",
  // ------------------------------------------------------------ 術語表（F3）、狀態列音量與延遲（F4）、刪除模型（F7）、新引擎訊息
  "glossary.legend": "Glossário",
  "glossary.lede": "Defina como nomes e termos de lives são traduzidos: quando o original contém um deles, a tradução usa o texto informado aqui. Suas entradas têm prioridade sobre a lista integrada.",
  "glossary.builtin": "Usar os termos de live integrados",
  "glossary.builtinHelp": "Termos comuns de lives em japonês (ex.: スパチャ → Super Chat); usados só quando o original é japonês e as legendas estão em chinês tradicional ou simplificado, inglês ou coreano.",
  "glossary.colSource": "Original",
  "glossary.colTarget": "Tradução",
  "glossary.colLanguage": "Idioma das legendas",
  "glossary.colActions": "Ações",
  "glossary.allLanguages": "Todos os idiomas",
  "glossary.sourceLabel": "Original, linha {row}",
  "glossary.targetLabel": "Tradução, linha {row}",
  "glossary.languageLabel": "Idioma das legendas, linha {row}",
  "glossary.remove": "Remover",
  "glossary.removeLabel": "Remover a linha {row}",
  "glossary.empty": "Nenhuma entrada sua ainda.",
  "glossary.add": "Adicionar entrada",
  "glossary.import": "Importar CSV",
  "glossary.export": "Exportar CSV",
  "glossary.csvHelp": "Colunas do CSV: source (original), target (tradução), target_language (código do idioma das legendas, ex.: pt; vazio = todos os idiomas). Dá para editar no Excel. As entradas importadas vão para o fim da tabela e passam a valer quando você clica em Salvar glossário; os arquivos exportados ficam na pasta de transcrições.",
  "glossary.save": "Salvar glossário",
  "glossary.reset": "Desfazer alterações",
  "glossary.saved": "Salvo.",
  "glossary.savedNextStart": "Salvo; entra em vigor na próxima vez que você iniciar.",
  "glossary.exported": "Glossário exportado: {path}",
  "glossary.imported": "Entradas importadas: {added}. Duplicadas ignoradas: {skipped}. Clique em Salvar glossário para aplicá-las.",
  "glossary.importBadLines": "Linhas sem original ou sem tradução não foram importadas: {lines}.",
  "glossary.importTooLarge": "O arquivo é grande demais (limite {size}); confira se é um CSV de glossário.",
  "glossary.error.empty": "Obrigatório.",
  "glossary.error.spaces": "Remova os espaços no início ou no fim.",
  "glossary.error.tooLong": "No máximo {max} caracteres.",
  "glossary.error.control": "Quebras de linha, tabulações e caracteres de controle invisíveis não são permitidos.",
  "glossary.error.language": "Escolha um idioma da lista.",
  "glossary.error.duplicate": "Repete a linha {row} (mesmo idioma das legendas).",
  "glossary.error.tooMany": "No máximo {max} entradas (agora há {count}).",
  "error.exportGlossary": "Não foi possível exportar o glossário",
  "error.importGlossary": "Não foi possível ler o arquivo CSV",
  "metrics.levelLabel": "Nível de entrada",
  "metrics.levelValue": "{db} dBFS",
  "metrics.levelUnknown": "Ainda sem leitura de nível",
  "metrics.latency": "Atraso {seconds} s",
  "metrics.latencyUnknown": "Atraso —",
  "metrics.latencyHelp": "Tempo entre o fim de uma frase e sua legenda final (mediana das últimas frases)",
  "metrics.backlog": "O reconhecimento não está dando conta: várias frases aguardam a legenda final. Considere um modelo menor na página Modelos.",
  "metrics.openModels": "Ir para Modelos",
  "metrics.noAudio": "Nenhum som há 15 segundos. Verifique se a fonte de áudio certa está selecionada, se o vídeo está tocando ou se o microfone não está mudo.",
  "asr.diskUse": "{size} em disco",
  "asr.delete": "Excluir",
  "asr.deleting": "Excluindo",
  "asr.deleteYes": "Sim, excluir",
  "asr.deleteNo": "Cancelar",
  "asr.deleteConfirm": "Excluir {model}? Isso libera {size}.",
  "asr.deleteSelected": "Suas configurações o usam para as legendas ({roles}), então ele será baixado de novo na próxima vez que você iniciar.",
  "asr.deleteInUse": "A sessão em andamento usa este modelo; pare-a primeiro.",
  "asr.deleteDownloading": "Baixando; dá para excluir quando terminar.",
  "asr.deleted": "{model} excluído; {size} liberados.",
  "error.deleteModel": "Não foi possível excluir {model}",
  "error.code.model_not_downloaded": "Este modelo não está baixado",
  "error.code.model_in_use": "O modelo está em uso; pare primeiro",
  "engineParam.compute_type_pair": "ao vivo {fast}, final {final}",
  "engine.asr_bgm_robust_final": "{final_model} para as legendas finais é o que melhor resiste a música de fundo e som de jogos em lives (avaliação de lives em japonês do kikitori)",
  "engine.asr_live_model_for_language": "{fast_model} é bem menos preciso com o idioma {language}, então as legendas ao vivo também usam {model} (carregado uma vez e compartilhado)",
};
