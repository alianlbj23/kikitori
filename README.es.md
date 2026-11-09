# kikitori（聞き取り）

[繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md) | [English](README.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | **Español** | [Français](README.fr.md) | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md) | [Русский](README.ru.md) | [Tiếng Việt](README.vi.md) | [ไทย](README.th.md) | [Bahasa Indonesia](README.id.md)

*Esta es una traducción del [README.zh-TW.md](README.zh-TW.md) en chino tradicional. Si difieren, prevalece README.zh-TW.md. Se tradujo con ayuda de IA; si encuentras errores, puedes enviar correcciones mediante [issues](https://github.com/alianlbj23/kikitori/issues).*

kikitori escucha el audio que reproduce tu computadora (o el micrófono), lo transcribe en tiempo real, lo traduce al idioma que elijas y lo muestra como subtítulos bilingües flotando sobre la pantalla.
El reconocimiento de voz (Whisper) y la traducción (Ollama local) se ejecutan en tu propia computadora: el audio y el texto no se suben a ningún servicio en la nube.

El caso de uso principal es **ver directos de VTubers en japonés** (con BGM, efectos de sonido de juegos y jerga de directos); los modelos recomendados y los parámetros se ajustaron con las mediciones de este proyecto.
También funciona cualquier otra combinación entre los 20 idiomas.

- Plataformas: Windows 10/11 x64 (plataforma principal de pruebas), Linux x64, macOS Apple Silicon
- Licencia: MIT (los modelos tienen sus propias licencias, ver «Licencia»)
- Versión actual: 0.3.0 ([novedades](docs/release-notes/v0.3.0.md))

## Funciones

| Función | Descripción |
|---|---|
| Traducción de video | Captura el audio que reproduce el sistema (Windows WASAPI loopback / Linux PulseAudio monitor / macOS ScreenCaptureKit) y traduce el idioma que se escucha al idioma de los subtítulos |
| Interpretación en tiempo real | Captura el micrófono; cuando hablas en «Mi idioma» se traduce al «Idioma de la otra persona», y cuando habla la otra persona se traduce al tuyo |
| Subtítulos en dos pasadas: primero rápidos, luego precisos | Mientras la frase aún se está diciendo, un modelo más rápido genera «subtítulos en tiempo real»; al terminar, un modelo más preciso calcula los «subtítulos definitivos» que los reemplazan |
| Subtítulos en tiempo real estables | En el original y en la traducción del subtítulo en tiempo real, el comienzo que ya no va a cambiar se muestra normal y el final que todavía cambia se muestra atenuado; las palabras nuevas aparecen con un fundido y el bloque ya no salta arriba y abajo |
| Etiquetas de hablante | En colaboraciones de varias personas marca cada frase con «Hablante 1, 2…»; el número de hablantes se puede detectar automáticamente o indicar (2–6), y puedes cambiar el nombre de una etiqueta haciendo clic en ella; en el modo de interpretación marca «Tú / Otra persona» |
| Cualquier combinación de 20 idiomas | El idioma que se escucha (se puede elegir «Detectar automáticamente») y el idioma de los subtítulos se eligen entre 20 idiomas, ver «Idiomas compatibles» |
| 13 idiomas de interfaz | Por defecto sigue al sistema operativo; se puede cambiar en la página Configuración |
| Mostrar solo el original | Desactiva «Mostrar traducción» para ver solo el original: no hace falta Ollama ni se usa la GPU; también se puede cambiar durante la ejecución |
| Furigana | Muestra la lectura sobre los kanji del original en japonés (SudachiDict, unos 69 MiB, que se descargan solo la primera vez que lo activas) |
| Modelos recomendados según el hardware | Detecta CPU, memoria, tarjeta gráfica NVIDIA y Ollama, y elige automáticamente la combinación de modelos; los modelos que faltan se descargan solo la primera vez que pulsas «Iniciar» |
| Glosario | 53 términos integrados de directos en japonés (赤スパ, 推し, メン限…) y palabras chino-japonesas que se confunden con facilidad para la traducción de chino a japonés; también puedes añadir tus propias entradas e importar/exportar CSV |
| Correcciones del reconocimiento e información del directo | Corrige automáticamente las palabras que el reconocimiento suele escribir mal; rellena el nombre del streamer, el nombre del juego o la descripción del directo para que los nombres se traduzcan siempre igual |
| Exportar transcripciones | SRT, VTT, TXT, CSV; se puede configurar el guardado automático al terminar cada sesión |
| Archivo de texto para OBS | Escribe los subtítulos actuales en un archivo de texto para usarlo con la opción «Leer desde archivo» de una fuente «Texto (GDI+)» de OBS; no abre ningún puerto de red |
| Ventana de subtítulos | Transparente, siempre visible, deja pasar clics; se ajustan el tamaño de letra, el fondo, la posición, el número de frases, el tiempo mínimo que se muestra cada frase, el tiempo de inactividad antes de desvanecerse, el tamaño y la posición del original y el ancho de línea; se puede pausar y repasar los subtítulos recientes |
| Barra de estado | Medidor de volumen, latencia (mediana desde el final del habla hasta el subtítulo definitivo); avisa cuando el reconocimiento no da abasto, cuando no hay sonido durante 15 segundos o cuando el sonido de fondo está demasiado alto |
| Gestión de modelos | Descarga, muestra el espacio en disco y elimina modelos de reconocimiento; descarga por ti los modelos de traducción en Ollama |
| Actualización automática | Al iniciar comprueba GitHub Releases, descarga la actualización, verifica la firma y la instala |

## Idiomas compatibles

Idiomas que se escuchan e idiomas de los subtítulos (20, en cualquier combinación):

| | | | | |
|---|---|---|---|---|
| 日本語 | English | 繁體中文（台灣） | 简体中文 | 한국어 |
| Español | Français | Deutsch | Português | Русский |
| Italiano | العربية | हिन्दी | Bahasa Indonesia | Tiếng Việt |
| ไทย | Türkçe | Polski | Nederlands | Українська |

- Los subtítulos en chino tradicional se convierten con OpenCC `s2twp` a los caracteres tradicionales estándar de Taiwán y al uso léxico de Taiwán (p. ej. 视频 → 影片). Solo se convierte cuando la traducción contiene caracteres simplificados, y no cambia 「台灣」 por 「臺灣」.
- Si el idioma que se escucha y el de los subtítulos son el mismo, no se traduce (entre chino tradicional y simplificado solo se convierte la escritura).
- La calidad del reconocimiento de voz varía según el idioma. Cuando la tasa de error de un modelo para un idioma supera el 20 % (p. ej. tailandés, árabe e hindi con large-v3-turbo, vietnamita con large-v3), las notas de la recomendación avisan de que ese modelo reconoce mal el idioma; para tailandés, árabe e hindi, con tarjetas gráficas de 8 GB o más, los subtítulos en tiempo real también pasan a usar large-v3.

Idiomas de la interfaz (13): 繁體中文（台灣）, 简体中文, English, 日本語, 한국어, Español, Français, Deutsch, Português (Brasil), Русский, Tiếng Việt, ไทย, Bahasa Indonesia.
El árabe se puede usar como idioma que se escucha o de los subtítulos, pero no hay interfaz en árabe (la interfaz no admite diseños de derecha a izquierda).

## Combinaciones recomendadas (según el hardware)

kikitori las detecta y aplica automáticamente; la tabla solo sirve para saber qué vas a obtener. Se basan en las evaluaciones de este proyecto (`experiments/vtuber_eval/`, `experiments/translate_eval/`); las reglas y fórmulas están en [`docs/specs/hardware.md`](docs/specs/hardware.md).

| Tu computadora | Subtítulos en tiempo real | Subtítulos definitivos | Traducción (Ollama) |
|---|---|---|---|
| NVIDIA de 16 GB o más | large-v3-turbo (float16) | large-v3 (float16) | qwen3.5:9b |
| NVIDIA de 12 GB (p. ej. RTX 5070) | large-v3-turbo (int8_float16) | large-v3 (int8_float16) | qwen3.5:9b |
| NVIDIA de 8–11 GB | large-v3-turbo (small si la VRAM está justo en el mínimo de 8 GB) | large-v3 (precisión según la VRAM) | qwen3.5:4b |
| NVIDIA de 4–8 GB | base | large-v3-turbo | qwen3.5:4b |
| NVIDIA de menos de 4 GB | base | small | qwen3.5:4b |
| Sin GPU NVIDIA, RAM ≥ 16 GB y 5 núcleos o más | base (CPU) | small (CPU) | qwen3.5:4b |
| Sin GPU NVIDIA, menos RAM o 4 núcleos o menos | tiny (CPU) | base (CPU) | qwen3.5:4b |
| Mac (Apple Silicon) | base (CPU) | small (CPU) | Según el tamaño de la memoria unificada (qwen3.5:9b con 16 GB) |

- En nuestras mediciones, las precisiones int8_float16 y float16 dan la misma calidad de reconocimiento (sin diferencia significativa), pero int8_float16 usa aproximadamente la mitad de VRAM; cuando la tarjeta gráfica se comparte con el modelo de traducción, kikitori prefiere que el modelo de traducción sea un tamaño mayor.
- Tamaño de descarga de los modelos de reconocimiento: large-v3-turbo 1,62 GB, large-v3 3,09 GB, small 487 MB, base 148 MB, tiny 78 MB. Modelos de traducción: qwen3.5:9b unos 6,6 GB, qwen3.5:4b unos 3,3 GB.
- Las GPU NVIDIA necesitan cuBLAS 12 (unos 570 MB). kikitori no lo incluye en el instalador; se descarga con un clic en la página Modelos después de aceptar la NVIDIA EULA. Si no está instalado, se usa la CPU.
- Cuando el servidor de traducción está en otra computadora (remoto), no se elige automáticamente un modelo de traducción; elígelo tú en la página Modelos.
- Para la traducción siempre se recomienda primero Qwen3.5 (Apache-2.0): 9B si hay suficiente VRAM; si no, 4B. Con tarjetas gráficas de 4 GB o menos, o sin GPU NVIDIA, también es qwen3.5:4b; Ollama ejecuta en la CPU la parte que no cabe, lo que es más lento (la latencia de traducción en este caso aún no se ha medido).
- Tencent HY-MT tiene restricciones regionales y solo puede recomendarse después de marcar en la página Configuración la confirmación de que no lo usas en la UE, el Reino Unido ni Corea del Sur; Sakura (licencia no comercial) nunca se recomienda y solo se puede elegir manualmente.
- La velocidad de reconocimiento en macOS y Linux y la velocidad de traducción en CPU aún no se han medido (ver «Limitaciones conocidas»).

## Primeros pasos

1. Descarga el instalador desde [GitHub Releases](https://github.com/alianlbj23/kikitori/releases) (las indicaciones de instalación de cada plataforma están en la página del Release: avisos de SmartScreen/Gatekeeper por no estar firmado, fuse3 y fuentes en Linux).
2. Para traducir, instala [Ollama](https://ollama.com/download) y déjalo ejecutándose en segundo plano. Si kikitori no lo detecta, te ofrecerá «Instalar Ollama» o «Mostrar solo el original».
3. Abre kikitori. En el primer inicio aplica automáticamente la combinación recomendada para tu hardware.
4. En «Idiomas» de la página Configuración, elige el idioma que se escucha y el idioma de los subtítulos. Al elegirlos solo se muestran la combinación recomendada y el tamaño de descarga; **no se descarga nada en ese momento**.
5. Elige la fuente de audio y pulsa «Iniciar». Primero se descargan los modelos de reconocimiento y de traducción (con Ollama) que falten, y al terminar empieza automáticamente.

### Mostrar solo el original

Es el interruptor «Mostrar traducción» de la barra de herramientas de la página Transcripción y de la sección «Subtítulos» de la página Configuración (o el atajo Ctrl+Alt+J). Cuando está desactivado:

- el motor no se conecta en absoluto al servidor de traducción, no hace falta instalar Ollama y no se ocupa la GPU con un modelo de traducción;
- la ventana de subtítulos muestra el original con el tamaño de letra y el color del subtítulo principal;
- el cambio durante la ejecución se aplica al instante; las frases ya mostradas no se traducen después.

### Furigana

«Mostrar furigana sobre el original en japonés» en la página Configuración: cuando el idioma que se escucha es japonés, muestra la lectura en hiragana sobre los kanji del original (el okurigana no se vuelve a anotar). La primera vez que lo activas se descarga SudachiDict core (Apache-2.0, unos 69 MiB); en la página Modelos se puede ver el espacio que ocupa y eliminarlo. Solo afecta a la visualización; los subtítulos exportados no cambian. Diseño: [`docs/specs/furigana.md`](docs/specs/furigana.md).

### Glosario

«Glosario» en la página Configuración:

- **Términos de directos integrados** (activado por defecto): se usan cuando el original es japonés y los subtítulos están en chino tradicional, chino simplificado, inglés o coreano; por ejemplo, con subtítulos en inglés, スパチャ → Super Chat, メン限 → members-only, 切り抜き → clip. Solo se incluyen en el prompt de traducción los términos que aparecen realmente en el original de esa frase (como máximo 8 por frase).
- **Palabras chino-japonesas que se confunden** (siguen el interruptor de la lista integrada): cuando el original es chino y los subtítulos están en japonés, indican las palabras que se escriben igual o de forma parecida en chino y en japonés pero significan otra cosa, p. ej. 便當 → 弁当, 節目 → 番組, 老婆 → 妻; además, en la traducción de chino a japonés las instrucciones al modelo se escriben en chino, para que el modelo lea menos el chino con significados japoneses.
- **Entradas personalizadas**: original, traducción e idioma de los subtítulos al que se aplican (o «Todos los idiomas»), hasta 500 entradas, válidas para cualquier idioma que se escuche; ante una misma grafía tienen prioridad sobre la lista integrada. La comparación no distingue mayúsculas y minúsculas ni caracteres de ancho completo y medio ancho.
- Se puede importar/exportar CSV (UTF-8, separado por comas, punto y coma o tabulaciones; también se leen los archivos guardados con Excel). Tras pulsar «Guardar glosario», los cambios se aplican la próxima vez que inicies los subtítulos.

### Exportar transcripciones

«Exportar» en la página Transcripción: SRT, VTT, TXT, CSV, con el contenido «Original y traducción», «Solo el original» o «Solo la traducción». Solo se exportan los subtítulos definitivos (los subtítulos en tiempo real todavía están cambiando).
Los archivos se guardan en `Documentos/kikitori/transcripts/YYYY-MM-DD_HHMMSS_<modo>.<extensión>`, y los códigos de tiempo se cuentan desde que empiezan los subtítulos.
En la página Configuración se puede activar «Guardar automáticamente al terminar cada sesión (SRT y TXT)»; después de pulsar «Detener», espera a que llegue el subtítulo definitivo de la última frase (como máximo 2 segundos) antes de guardar.

### Archivo de texto para OBS

Página Configuración, «Transcripciones y OBS» → «Escribir los subtítulos en un archivo de texto (para OBS)»: original, traducción o ambos.
kikitori sobrescribe `Documentos/kikitori/obs/subtitle.txt` con las frases que hay en ese momento en la ventana de subtítulos (UTF-8, escritura atómica, como máximo 10 veces por segundo);
en OBS, añade una fuente «Texto (GDI+)», marca «Leer desde archivo» y selecciona este archivo. El archivo se vacía cuando terminan los subtítulos, cuando se desvanecen por inactividad o cuando se borran.
No se abre ningún puerto de red, así que no hay ninguna forma de usarlo con una fuente «Navegador» de OBS.

### Atajos globales

| Windows / Linux | macOS | Acción |
|---|---|---|
| Ctrl+Alt+K | ⌘+Option+K | Alternar «Dejar pasar clics» en la ventana de subtítulos |
| Ctrl+Alt+J | ⌘+Option+J | Alternar «Mostrar traducción» |
| Ctrl+Alt+U | ⌘+Option+U | Borrar la ventana de subtítulos (y el archivo de texto para OBS); la transcripción no se ve afectada |
| Ctrl+Alt+P | ⌘+Option+P | Pausar/reanudar la ventana de subtítulos; la transcripción sigue como siempre |
| Ctrl+Alt+H | ⌘+Option+H | Mostrar un momento las últimas 10 frases en la ventana de subtítulos |
| Ctrl+Alt+R | ⌘+Option+R | Durante la interpretación, mostrar «¿Puede repetirlo, por favor?» en el idioma de la otra persona en la ventana de subtítulos |

Si otro programa ocupa un atajo, la página Transcripción indica que no se puede utilizar; los demás atajos no se ven afectados. En Linux con Wayland puede que no se pueda registrar ningún atajo global.

## Privacidad y uso sin conexión

- El audio, los resultados del reconocimiento y las traducciones se procesan solo en tu computadora. Por defecto, la traducción se envía al Ollama local (`http://localhost:11434/v1`); si cambias el servicio de traducción a otra URL, el texto de los subtítulos se envía allí.
- Solo se conecta a la red para: descargar modelos (los de reconocimiento desde Hugging Face, los de traducción mediante Ollama, los componentes de GPU desde PyPI; siempre solo cuando pulsas Iniciar o descargar) y comprobar actualizaciones al iniciar (GitHub Releases). Una vez descargados los modelos, los subtítulos funcionan completamente sin conexión.
- La clave de API solo se lee de una variable de entorno (la configuración guarda solo el nombre de la variable, `KIKITORI_TRANSLATE_API_KEY` por defecto) y no se escribe en ningún archivo.
- «Copiar diagnóstico» en la página «Acerca de» solo copia al portapapeles; oculta las claves, las credenciales de las URL y los nombres de usuario, y del glosario solo indica el número de entradas.
- El motor y la aplicación de escritorio se comunican solo por la entrada y salida estándar; no se abre ningún puerto de red.

## Novedades de la 0.3.0

Resumen (los detalles completos y las notas de actualización están en [docs/release-notes/v0.3.0.md](docs/release-notes/v0.3.0.md); los de la 0.2.0, en [docs/release-notes/v0.2.0.md](docs/release-notes/v0.2.0.md)):

- Etiquetas de hablante: en colaboraciones de varias personas cada frase se marca con «Hablante 1, 2…»; el número se detecta automáticamente o se indican 2–6 personas; haz clic en una etiqueta para cambiarle el nombre, y las que tienen el mismo nombre se fusionan (modelo de unos 27 MiB, que se descarga la primera vez que lo eliges)
- La traducción en tiempo real se divide en una parte estable y otra que cambia, así que la frase entera ya no parpadea; las palabras nuevas aparecen con un fundido y los subtítulos no saltan arriba y abajo
- El modo de interpretación marca «Tú / Otra persona»; Ctrl+Alt+R muestra «¿Puede repetirlo, por favor?» en el idioma de la otra persona
- Reglas de corrección del reconocimiento, añadir al glosario palabras seleccionadas en la transcripción, información del directo (nombre del streamer, nombre del juego, descripción del directo)
- Diseño de lectura: tamaño y posición del original, ancho de línea, más tiempo en pantalla según la longitud, furigana solo la primera vez
- Nuevos atajos Ctrl+Alt+P para pausar los subtítulos y Ctrl+Alt+H para repasar los subtítulos recientes; restablecer la posición de la ventana de subtítulos; aviso cuando el sonido de fondo está demasiado alto

## Limitaciones conocidas

- **Solo se ha probado por completo en Windows (RTX 5070)**. No se han medido la captura en equipos reales con macOS y Linux ni la velocidad de traducción en Apple Silicon y en CPU; Linux solo se ha validado en un contenedor Docker.
- **La nueva combinación recomendada solo se ha medido de extremo a extremo en una RTX 5070 (12 GB)**: con turbo + large-v3 (int8_float16) y qwen3.5:9b ejecutándose a la vez, la tarjeta completa llega como máximo a 10,8 GB, la latencia de los subtítulos en tiempo real es de unos 0,31 segundos y del final del habla al subtítulo definitivo pasan unos 0,92 segundos (medianas, ver [`tasks/acceptance-report-v0.2.0.md`](tasks/acceptance-report-v0.2.0.md)).
- **El material de evaluación de la precisión del reconocimiento no es de VTubers**: es habla coloquial general de YouTube (YODAS), lectura en voz alta y audio de televisión, con BGM mezclada; no hay datos de directos de canto, gritos ni colaboraciones de varias personas. El canto atraviesa la detección de voz y puede producir subtítulos extraños.
- **Las etiquetas de hablante solo se han medido con fragmentos de YouTube empalmados** (alrededor del 92–100 % bien etiquetado con 2 personas y del 80–85 % con 3–4): no hay datos de personas que se pisan al hablar, interjecciones de menos de 1 segundo ni de una misma persona que cambia de voz, así que en colaboraciones reales el resultado puede ser peor; las etiquetas no se escriben en el archivo de texto para OBS.
- Los idiomas distintos del inglés solo cuentan con pruebas de referencia públicas y pruebas de alucinaciones sin voz, sin mediciones con habla real; la lista de frases de alucinación no ha sido revisada por hablantes nativos.
- Algunas fórmulas de despedida (p. ej. 「チャンネル登録よろしくお願いします」) se filtran como alucinaciones de Whisper y se elimina la frase entera, aunque el streamer la haya dicho de verdad.
- Para traducir hay que instalar Ollama aparte; con servidores compatibles con OpenAI distintos de Ollama, tienes que instalar los modelos tú mismo.
- Las pruebas de estrés de memoria de larga duración se hicieron en Windows (motor estable en una sesión de 20 minutos, ventana principal +0,7 MiB/min en 22 minutos); en macOS y Linux no se han probado.
- Solo puede haber una sesión de subtítulos a la vez; si se desconecta el dispositivo, no se vuelve a conectar automáticamente.
- No se admite: consultar palabras con un clic, capturar el audio de una sola aplicación, modo para directos de canto, separación de hablantes que cambian a mitad de frase o hablan a la vez, fuente «Navegador» de OBS (los motivos están en la sección «Por ahora no» (「先不做」) de [`docs/specs/competitive-features.md`](docs/specs/competitive-features.md), documento en chino, y en [`docs/adr/`](docs/adr/)).
- Los instaladores no tienen firma de código de pago (Windows SmartScreen y macOS Gatekeeper mostrarán advertencias).

## Licencia

kikitori se publica bajo la licencia MIT ([LICENSE](LICENSE)). Los textos completos de las licencias de los componentes de terceros están en el directorio de instalación y se pueden abrir desde la página «Acerca de».

Los modelos no se distribuyen con el instalador; al descargarlos se aplica la licencia de cada uno:

| Modelo | Licencia | Atención |
|---|---|---|
| Whisper (versión convertida a CTranslate2) | MIT | — |
| Silero VAD (incluido con el programa) | MIT | — |
| 3D-Speaker CAM++ (etiquetas de hablante, se descarga al elegirlo) | Apache-2.0 | — |
| Qwen3.5 (modelo de traducción predeterminado) | Apache-2.0 | — |
| Tencent HY-MT1.5 | Tencent HY Community License | **No es aplicable en la UE, el Reino Unido ni Corea del Sur**; kikitori no lo recomienda por defecto y hay que confirmar la región en la página Configuración |
| Sakura-GalTransl | CC BY-NC-SA 4.0 | **Prohibido el uso comercial**; nunca se recomienda, solo se puede elegir manualmente |
| NVIDIA cuBLAS | NVIDIA CUDA EULA | Hay que aceptarla antes de descargar |

## Para desarrolladores

- Arquitectura: [docs/architecture.md](docs/architecture.md); especificaciones de los módulos: [docs/specs/](docs/specs/); guía de aprendizaje: [docs/learning/](docs/learning/README.md)
- Evaluaciones y experimentos: `experiments/*/README.md`; informes de investigación: `reports/`

```text
uv sync                                       Instalar las dependencias de Python
uv run python -m unittest discover -s tests   Pruebas de Python
uv run ruff check                             Lint
uv run kikitori                               Menú interactivo (iniciar el motor, listar dispositivos de audio…)
cd app && npm ci && npm run menu              Menú de desarrollo de la aplicación de escritorio (requiere Node y Rust)
cd app && npm test && npx tsc --noEmit        Pruebas del frontend y comprobación de tipos
```
