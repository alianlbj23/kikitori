# kikitori（聞き取り）

[繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md) | [English](README.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | **Português (Brasil)** | [Русский](README.ru.md) | [Tiếng Việt](README.vi.md) | [ไทย](README.th.md) | [Bahasa Indonesia](README.id.md)

*Esta é uma tradução do README.zh-TW.md em chinês tradicional; em caso de divergência, vale o README.zh-TW.md. A tradução foi feita com auxílio de IA; correções são bem-vindas pelas [issues](https://github.com/alianlbj23/kikitori/issues).*

O kikitori escuta o som que o seu computador está reproduzindo (ou o microfone), reconhece a fala como texto em tempo real, traduz para o idioma escolhido e mostra legendas bilíngues flutuando sobre a tela.
O reconhecimento de fala (Whisper) e a tradução (Ollama local) rodam no seu próprio computador: áudio e texto não são enviados a nenhum serviço em nuvem.

O cenário principal é **assistir a lives de VTubers japoneses** (com BGM, efeitos sonoros de jogos e gírias de live); as recomendações de modelos e os parâmetros foram ajustados com base nas medições deste projeto.
Qualquer outra combinação entre os 20 idiomas também funciona.

- Plataformas: Windows 10/11 x64 (principal plataforma validada), Linux x64, macOS Apple Silicon
- Licença: MIT (os modelos têm licenças próprias; veja "Licenças")
- Versão atual: 0.2.0 ([novidades](docs/release-notes/v0.2.0.md))

## Recursos

| Recurso | Descrição |
|---|---|
| Tradução de vídeo | Captura o som reproduzido pelo sistema (Windows WASAPI loopback / Linux PulseAudio monitor / macOS ScreenCaptureKit) e traduz o idioma de escuta para o idioma das legendas |
| Interpretação ao vivo | Captura o microfone; o que você diz em "Meu idioma" é traduzido para o idioma da outra pessoa, e o que a outra pessoa diz é traduzido para o seu idioma |
| Legendas em dois caminhos: primeiro rápidas, depois precisas | Enquanto a frase ainda está sendo dita, um modelo mais rápido gera primeiro a "legenda ao vivo"; quando a frase termina, um modelo mais preciso recalcula a "legenda final" e a substitui |
| Legendas ao vivo estáveis | O início da legenda ao vivo que não vai mais mudar aparece normalmente, e o final que ainda muda aparece em tom mais claro; a tradução não fica mais piscando junto com o final |
| 20 idiomas em qualquer combinação | O idioma de escuta (pode ser "Detectar automaticamente") e o idioma das legendas são escolhidos livremente entre 20 idiomas; veja "Idiomas suportados" |
| 13 idiomas de interface | Por padrão segue o sistema operacional; pode ser alterado nas Configurações |
| Mostrar só o original | Desligue "Mostrar tradução" para ver só o original: não precisa do Ollama nem ocupa a GPU; dá para alternar durante a execução |
| Furigana | Mostra a leitura sobre os kanji do original em japonês (SudachiDict, cerca de 69 MiB, baixado só na primeira vez que é ativado) |
| Modelos recomendados conforme o hardware | Detecta CPU, memória, placa de vídeo NVIDIA e Ollama e escolhe automaticamente a combinação de modelos; os modelos que faltam só são baixados na primeira vez que você clica em "Iniciar" |
| Glossário | 53 termos de lives japonesas integrados (赤スパ, 推し, メン限…) e palavras chinês-japonês fáceis de confundir para chinês → japonês; também aceita termos personalizados e importação/exportação em CSV |
| Exportar transcrição | SRT, VTT, TXT, CSV; é possível salvar automaticamente ao fim de cada sessão |
| Arquivo de texto para o OBS | Grava a legenda atual em um arquivo de texto para usar no OBS em "Texto (GDI+) → Carregar do arquivo"; nenhuma porta de rede é aberta |
| Janela de legendas | Transparente, sempre no topo, com clique através; tamanho da fonte, fundo, posição, número de frases, tempo mínimo de exibição de cada frase e tempo ocioso até esmaecer são ajustáveis |
| Barra de status | Medidor de volume, latência (mediana do fim da fala até a legenda final); avisa quando o reconhecimento não está dando conta ou quando não chega som por 15 segundos |
| Gerenciamento de modelos | Baixa, mostra o uso de disco e exclui modelos de reconhecimento; baixa para você os modelos de tradução no Ollama |
| Atualização automática | Verifica o GitHub Releases ao iniciar, baixa, verifica a assinatura e instala |

## Idiomas suportados

Idiomas de escuta e de legendas (20, em qualquer combinação):

| | | | | |
|---|---|---|---|---|
| 日本語 | English | 繁體中文（台灣） | 简体中文 | 한국어 |
| Español | Français | Deutsch | Português | Русский |
| Italiano | العربية | हिन्दी | Bahasa Indonesia | Tiếng Việt |
| ไทย | Türkçe | Polski | Nederlands | Українська |

- As legendas em chinês tradicional são convertidas com o OpenCC `s2twp` para a escrita tradicional e o vocabulário usados em Taiwan (por exemplo, 视频 → 影片). A conversão só acontece quando a tradução contém caracteres simplificados, e "台灣" não é trocado por "臺灣".
- Quando o idioma de escuta e o das legendas são o mesmo, não há tradução (entre chinês tradicional e simplificado só se converte a escrita).
- A qualidade do reconhecimento de fala varia conforme o idioma. Quando a taxa de erro de um modelo em um idioma passa de 20% (por exemplo, tailandês, árabe e hindi no large-v3-turbo, e vietnamita no large-v3), a explicação da recomendação avisa que a qualidade do reconhecimento é mais baixa; para tailandês, árabe e hindi, em placas de vídeo com 8 GB ou mais, as legendas ao vivo também passam a usar o large-v3.

Idiomas de interface (13): 繁體中文（台灣）, 简体中文, English, 日本語, 한국어, Español, Français, Deutsch, Português (Brasil), Русский, Tiếng Việt, ไทย, Bahasa Indonesia.
O árabe pode ser usado como idioma de escuta ou de legendas, mas não há interface em árabe (a interface não suporta layout da direita para a esquerda).

## Combinações recomendadas (conforme o hardware)

O kikitori detecta e aplica automaticamente; a tabela abaixo só mostra o que você vai receber. A base são as avaliações deste projeto (`experiments/vtuber_eval/`, `experiments/translate_eval/`); as regras e fórmulas estão em [`docs/specs/hardware.md`](docs/specs/hardware.md).

| Seu computador | Legendas ao vivo | Legendas finais | Tradução (Ollama) |
|---|---|---|---|
| NVIDIA com 16 GB ou mais | large-v3-turbo (float16) | large-v3 (float16) | qwen3.5:9b |
| NVIDIA com 12 GB (por exemplo, RTX 5070) | large-v3-turbo (int8_float16) | large-v3 (int8_float16) | qwen3.5:9b |
| NVIDIA com 8–11 GB | large-v3-turbo (small quando a memória de vídeo está exatamente no limite de 8 GB) | large-v3 (precisão conforme a memória de vídeo) | qwen3.5:4b |
| NVIDIA com 4–8 GB | base | large-v3-turbo | qwen3.5:4b |
| NVIDIA com menos de 4 GB | base | small | qwen3.5:4b |
| Sem GPU NVIDIA, RAM ≥ 16 GB, 5 núcleos ou mais | base (CPU) | small (CPU) | qwen3.5:4b |
| Sem GPU NVIDIA, menos RAM ou até 4 núcleos | tiny (CPU) | base (CPU) | qwen3.5:4b |
| Mac (Apple Silicon) | base (CPU) | small (CPU) | Conforme o tamanho da memória unificada (16 GB: qwen3.5:9b) |

- Nas medições, as precisões int8_float16 e float16 tiveram a mesma qualidade de reconhecimento (diferença não significativa), mas int8_float16 usa cerca de metade da memória de vídeo. Quando o reconhecimento divide a placa de vídeo com o modelo de tradução, o kikitori dá prioridade para o modelo de tradução poder ser um nível maior.
- Tamanho do download dos modelos de reconhecimento: large-v3-turbo 1.62 GB, large-v3 3.09 GB, small 487 MB, base 148 MB, tiny 78 MB. Modelos de tradução: qwen3.5:9b cerca de 6.6 GB, qwen3.5:4b cerca de 3.3 GB.
- GPUs NVIDIA precisam do cuBLAS 12 (cerca de 570 MB). O kikitori não o inclui no instalador; na página Modelos, depois de aceitar a NVIDIA EULA, ele é baixado com um clique. Sem ele, a CPU é usada.
- Quando o servidor de tradução está em outro computador (remoto), o modelo de tradução não é escolhido automaticamente; escolha-o você mesmo na página Modelos.
- Para tradução, a recomendação é sempre o Qwen3.5 (Apache-2.0) em primeiro lugar: 9B se a memória de vídeo for suficiente, senão 4B. Com placa de vídeo de menos de 4 GB ou sem GPU NVIDIA também é o qwen3.5:4b; o Ollama coloca na CPU a parte que não cabe, o que é mais lento (a latência de tradução nesse caso ainda não foi medida).
- O Tencent HY-MT tem restrição regional e só pode ser recomendado depois que você confirmar, em "Regiões de licença dos modelos de tradução" nas Configurações, que não usa o kikitori na União Europeia, no Reino Unido nem na Coreia do Sul; o Sakura (licença não comercial) nunca é recomendado e só pode ser escolhido manualmente.
- A velocidade de reconhecimento no macOS e no Linux e a velocidade de tradução na CPU ainda não foram medidas (veja "Limitações conhecidas").

## Como começar

1. Baixe o instalador no [GitHub Releases](https://github.com/alianlbj23/kikitori/releases) (as dicas de instalação de cada plataforma estão na página da release: avisos do SmartScreen/Gatekeeper por falta de assinatura, fuse3 e fontes no Linux).
2. Para traduzir, instale o [Ollama](https://ollama.com/download) e deixe-o rodando em segundo plano. Se o kikitori não o encontrar, ele oferece "Instalar o Ollama" ou "Mostrar só o original".
3. Abra o kikitori. Na primeira execução, a combinação recomendada para o seu hardware é aplicada automaticamente.
4. Em "Idiomas", nas Configurações, escolha o idioma de escuta e o idioma das legendas. Depois da escolha, só aparecem a combinação recomendada e o tamanho do download; **nada é baixado na hora**.
5. Escolha a fonte de áudio e clique em "Iniciar". Os modelos de reconhecimento e de tradução (no caso do Ollama) que faltam são baixados primeiro, e as legendas começam automaticamente ao terminar.

### Mostrar só o original

Use a chave "Mostrar tradução" na barra de ferramentas da página Transcrição ou na seção "Legendas" das Configurações (ou o atalho Ctrl+Alt+J). Quando desligada:

- o mecanismo não se conecta ao servidor de tradução, não é preciso instalar o Ollama e a GPU não é ocupada por um modelo de tradução;
- a janela de legendas mostra o original no tamanho e na cor da legenda principal;
- a mudança durante a execução vale na hora; as frases já exibidas não são traduzidas depois.

### Furigana

Configuração "Mostrar furigana sobre o original em japonês": quando o idioma de escuta é japonês, mostra a leitura em hiragana sobre os kanji do original (o okurigana / 送り仮名 não é repetido). Na primeira vez que é ativado, baixa o SudachiDict core (Apache-2.0, cerca de 69 MiB); na página Modelos é possível ver o uso de disco e excluí-lo. Afeta só a tela; as legendas exportadas não mudam. Design em [`docs/specs/furigana.md`](docs/specs/furigana.md).

### Glossário

Seção "Glossário" das Configurações:

- **Termos de live integrados** ("Usar os termos de live integrados", ligado por padrão): usados quando o original é japonês e as legendas estão em chinês tradicional/simplificado, inglês ou coreano; por exemplo, com legendas em inglês, スパチャ → Super Chat, メン限 → members-only, 切り抜き → clip. Só os termos que de fato aparecem na frase entram no prompt de tradução (no máximo 8 por frase).
- **Falsos cognatos chinês-japonês** (seguem a chave dos termos integrados): quando o original é chinês e as legendas estão em japonês, indica palavras que se escrevem igual ou parecido em chinês e japonês mas têm outro sentido, por exemplo 便當 → 弁当, 節目 → 番組, 老婆 → 妻. Além disso, na tradução de chinês para japonês, as instruções ao modelo passam a ser escritas em chinês, para que ele leia menos o chinês com o sentido japonês.
- **Termos personalizados**: original, tradução e idioma de legenda a que se aplica (ou "Todos os idiomas"), até 500 termos, válidos para qualquer idioma de escuta; com a mesma grafia, têm prioridade sobre os termos integrados. A comparação ignora maiúsculas/minúsculas e largura total/meia largura.
- Importação/exportação em CSV (UTF-8, separado por vírgula, ponto e vírgula ou Tab; também lê arquivos salvos pelo Excel). Depois de clicar em "Salvar glossário", vale a partir da próxima vez que as legendas forem iniciadas.

### Exportar transcrição

"Exportar" na página Transcrição: SRT, VTT, TXT, CSV, com conteúdo "Original e tradução", "Só o original" ou "Só a tradução". Só as legendas finais são exportadas (as legendas ao vivo ainda estão mudando).
Os arquivos ficam em `Documentos/kikitori/transcripts/YYYY-MM-DD_HHMMSS_<modo>.<extensão>`, com códigos de tempo contados a partir do início das legendas.
Nas Configurações, é possível ativar "Salvar automaticamente ao fim de cada sessão (SRT e TXT)"; depois de clicar em "Parar", o kikitori espera chegar a legenda final da última frase (no máximo 2 segundos) e então salva.

### Arquivo de texto para o OBS

Configurações, "Transcrições e OBS" → "Gravar as legendas em um arquivo de texto (para o OBS)": original, tradução ou ambos.
O kikitori sobrescreve `Documentos/kikitori/obs/subtitle.txt` com as frases atuais da janela de legendas (UTF-8, gravação atômica, no máximo 10 vezes por segundo);
no OBS, adicione uma fonte "Texto (GDI+)", marque "Carregar do arquivo" e aponte para esse arquivo. O arquivo é esvaziado quando as legendas terminam, esmaecem por inatividade ou são limpas.
Nenhuma porta de rede é aberta, por isso não há a opção de usar uma fonte "Navegador" do OBS.

### Atalhos globais

| Windows / Linux | macOS | Ação |
|---|---|---|
| Ctrl+Alt+K | ⌘+Option+K | Alterna o clique através da janela de legendas |
| Ctrl+Alt+J | ⌘+Option+J | Alterna "Mostrar tradução" |
| Ctrl+Alt+U | ⌘+Option+U | Limpa a janela de legendas (e o arquivo de texto do OBS); a transcrição não é afetada |

Atalhos ocupados por outros programas aparecem na página Transcrição como indisponíveis, sem afetar os outros atalhos. No Linux com Wayland, pode não ser possível registrar nenhum atalho global.

## Privacidade e uso offline

- Áudio, resultados do reconhecimento e traduções são processados só no seu computador. Por padrão, a tradução vai para o Ollama local (`http://localhost:11434/v1`); se você trocar o serviço de tradução por outro endereço, o texto das legendas é enviado para lá.
- O acesso à rede acontece só para: baixar modelos (modelos de reconhecimento do Hugging Face, modelos de tradução pelo Ollama, componentes de GPU do PyPI, sempre só quando você clica em iniciar ou baixar) e verificar atualizações ao iniciar (GitHub Releases). Com todos os modelos baixados, as legendas em si funcionam totalmente offline.
- As chaves de API são lidas só de variáveis de ambiente (as configurações guardam só o nome da variável, por padrão `KIKITORI_TRANSLATE_API_KEY`) e não são gravadas em nenhum arquivo.
- "Copiar diagnóstico", na página Sobre, copia só para a área de transferência; oculta chaves, credenciais em URLs e nomes de usuário, e do glossário só informa o número de termos.
- O mecanismo e o app desktop se comunicam só pela entrada e saída padrão, sem abrir portas de rede.

## Novidades da 0.2.0

Resumo (descrição completa e observações sobre a atualização em [docs/release-notes/v0.2.0.md](docs/release-notes/v0.2.0.md)):

- 20 idiomas em qualquer combinação, 13 idiomas de interface, modo só original, furigana em japonês
- Nova escolha de modelos pensada para lives de VTubers japoneses (GPU: ao vivo large-v3-turbo, final large-v3; temperatura de tradução 0)
- Glossário integrado de termos de live e termos personalizados
- Legendas ao vivo estáveis, medidor de volume e latência, aviso de atraso no reconhecimento
- Exportação em SRT/VTT/TXT/CSV, arquivo de texto para o OBS, ajustes de ritmo de leitura
- Modelos que faltam só são baixados ao iniciar; uso de disco e exclusão de modelos
- Novos atalhos Ctrl+Alt+J/U, "Copiar diagnóstico"
- Tradução de chinês para japonês mais precisa (instruções escritas em chinês e lista de falsos cognatos chinês-japonês), modelo de tradução escolhido em lista suspensa, e ao fechar o kikitori o modelo de tradução do Ollama local também é descarregado para liberar memória de vídeo
- "Iniciar" mais rápido (combinação recomendada de 12 GB com tradução: logo após abrir, de cerca de 11 → 6 segundos; depois, de cerca de 7.8 → 2.4 segundos); o aviso de atualização mostra as notas da versão
- Correções: o modelo ficava na GPU quando a abertura do dispositivo falhava; cada início de legendas ocupava cerca de 17 MiB a mais de memória de vídeo; a memória da janela principal crescia continuamente em execuções longas

## Limitações conhecidas

- **Testado por completo só no Windows (RTX 5070)**. A captura real no macOS e no Linux e a velocidade de tradução no Apple Silicon e na CPU não foram medidas; o Linux só foi validado em um contêiner Docker.
- **As novas combinações recomendadas só foram medidas de ponta a ponta na RTX 5070 (12 GB)**: com turbo + large-v3 (int8_float16) e qwen3.5:9b rodando ao mesmo tempo, a placa inteira chegou a no máximo 10.8 GB, a latência da legenda ao vivo foi de cerca de 0.31 segundo e do fim da fala até a legenda final, de cerca de 0.92 segundo (mediana; veja [`tasks/acceptance-report-v0.2.0.md`](tasks/acceptance-report-v0.2.0.md)).
- **O material de avaliação da precisão do reconhecimento não é de VTubers**: foram usados fala coloquial geral do YouTube (YODAS), leitura em voz alta e áudio de TV, com BGM misturada; não há dados de lives de canto, gritos ou collabs com várias pessoas. O canto passa pela detecção de voz e pode gerar legendas estranhas.
- Para idiomas além do inglês só há benchmarks públicos e testes de alucinação com áudio sem fala, sem medições com fala real; a lista de frases de alucinação não foi revisada por falantes nativos.
- Algumas frases de encerramento (por exemplo, "チャンネル登録よろしくお願いします") são descartadas por inteiro como alucinação do Whisper, mesmo quando o streamer realmente as diz.
- Para traduzir é preciso instalar o Ollama separadamente; com servidores compatíveis com OpenAI que não sejam o Ollama, você mesmo precisa instalar os modelos.
- Testes de uso de memória em execuções longas foram feitos no Windows (mecanismo estável em uma sessão de 20 minutos; janela principal +0.7 MiB/min em 22 minutos); macOS e Linux não foram testados.
- Só pode haver uma sessão de legendas por vez; se o dispositivo for desconectado, não há reconexão automática.
- Não suportado: consultar palavras com um clique, capturar o som de um único app, modo de live de canto, separação de falantes, fonte "Navegador" do OBS (motivos na seção "Por enquanto não" ("先不做") do documento em chinês [`docs/specs/competitive-features.md`](docs/specs/competitive-features.md) e em [`docs/adr/`](docs/adr/)).
- O instalador não tem assinatura de código paga (o Windows SmartScreen e o macOS Gatekeeper mostram avisos).

## Licenças

O kikitori em si é distribuído sob a licença MIT ([LICENSE](LICENSE)). Os textos completos das licenças de componentes de terceiros ficam no diretório de instalação e podem ser abertos pela página Sobre.

Os modelos não são distribuídos com o instalador; ao baixá-los, valem as licenças de cada um:

| Modelo | Licença | Observação |
|---|---|---|
| Whisper (versão convertida para CTranslate2) | MIT | — |
| Silero VAD (incluído no programa) | MIT | — |
| Qwen3.5 (modelo de tradução padrão) | Apache-2.0 | — |
| Tencent HY-MT1.5 | Tencent HY Community License | **Não vale na União Europeia, no Reino Unido nem na Coreia do Sul**; o kikitori não o recomenda por padrão, e é preciso confirmar a região nas Configurações |
| Sakura-GalTransl | CC BY-NC-SA 4.0 | **Uso comercial proibido**; nunca é recomendado, só pode ser escolhido manualmente |
| NVIDIA cuBLAS | NVIDIA CUDA EULA | É preciso aceitar antes de baixar |

## Para desenvolvedores

- Arquitetura: [docs/architecture.md](docs/architecture.md); especificações dos módulos: [docs/specs/](docs/specs/); guia de estudo: [docs/learning/](docs/learning/README.md)
- Avaliações e experimentos: `experiments/*/README.md`; relatórios de investigação: `reports/`

```text
uv sync                                       Instala as dependências Python
uv run python -m unittest discover -s tests   Testes Python
uv run ruff check                             Lint
uv run kikitori                               Menu interativo (iniciar o mecanismo, listar dispositivos de áudio…)
cd app && npm ci && npm run menu              Menu de desenvolvimento do app desktop (requer Node e Rust)
cd app && npm test && npx tsc --noEmit        Testes do frontend e verificação de tipos
```
