# kikitori（聞き取り）

[繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md) | [English](README.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md) | [Русский](README.ru.md) | [Tiếng Việt](README.vi.md) | [ไทย](README.th.md) | **Bahasa Indonesia**

*Ini adalah terjemahan dari README.zh-TW.md berbahasa Mandarin Tradisional; jika ada perbedaan, yang berlaku adalah README.zh-TW.md. Terjemahan ini dibuat dengan bantuan AI; silakan kirim koreksi melalui [issues](https://github.com/alianlbj23/kikitori/issues).*

kikitori mendengarkan suara yang sedang diputar komputer Anda (atau mikrofon), mengenalinya menjadi teks secara real-time, menerjemahkannya ke bahasa pilihan Anda, lalu menampilkannya sebagai subtitle dwibahasa yang melayang di layar.
Pengenalan suara (Whisper) dan terjemahan (Ollama lokal) berjalan di komputer Anda sendiri: suara dan teks tidak diunggah ke layanan cloud mana pun.

Skenario utamanya adalah **menonton siaran langsung VTuber berbahasa Jepang** (dengan BGM, efek suara game, dan istilah khas siaran langsung); rekomendasi model dan parameternya disesuaikan berdasarkan pengukuran nyata proyek ini;
kombinasi lain di antara 20 bahasa juga bisa digunakan.

- Platform: Windows 10/11 x64 (platform verifikasi utama), Linux x64. macOS (Apple Silicon) belum diuji di Mac sungguhan, jadi untuk sementara belum ada penginstal macOS.
- Lisensi: MIT (setiap model memiliki lisensinya sendiri, lihat "Lisensi")
- Versi saat ini: 0.3.1 ([catatan pembaruan](docs/release-notes/v0.3.1.md))

## Fitur

| Fitur | Keterangan |
|---|---|
| Terjemahan video | Menangkap suara yang diputar sistem (Windows WASAPI loopback / Linux PulseAudio monitor / macOS ScreenCaptureKit) dan menerjemahkan bahasa yang didengar ke bahasa subtitle |
| Penerjemahan lisan langsung | Menangkap mikrofon; saat Anda berbicara dalam "Bahasa saya", diterjemahkan ke bahasa lawan bicara, dan saat lawan bicara berbicara, diterjemahkan ke bahasa Anda |
| Subtitle dua jalur: cepat dulu, akurat kemudian | Selagi kalimat masih diucapkan, model yang lebih cepat menampilkan "subtitle langsung"; setelah selesai, model yang lebih akurat menghitung ulang "subtitle lengkap" untuk menggantikannya |
| Subtitle langsung yang stabil | Pada teks asli dan terjemahan subtitle langsung, bagian awal yang sudah tidak berubah ditampilkan normal, bagian akhir yang masih berubah ditampilkan dengan warna pudar; kata baru muncul perlahan dan blok subtitle tidak lagi melompat naik-turun |
| Label pembicara | Saat kolaborasi banyak orang, menandai setiap kalimat dengan "Pembicara 1, 2…"; jumlah pembicara bisa ditentukan otomatis atau ditetapkan 2–6 orang, dan label bisa diganti namanya dengan mengekliknya; di mode penerjemahan lisan ditandai "Anda / Lawan bicara" |
| Kombinasi bebas 20 bahasa | Bahasa yang didengar (bisa memilih "Deteksi otomatis") dan bahasa subtitle dipilih bebas dari 20 bahasa, lihat "Bahasa yang didukung" |
| 13 bahasa antarmuka | Secara default mengikuti sistem operasi, bisa diganti di halaman Pengaturan |
| Tampilkan teks asli saja | Matikan "Tampilkan terjemahan" untuk melihat teks asli saja: tidak perlu Ollama, tidak memakai GPU; bisa diganti saat sedang berjalan |
| Furigana | Menampilkan cara baca di atas kanji pada teks asli bahasa Jepang (SudachiDict, sekitar 69 MiB, baru diunduh saat pertama kali diaktifkan) |
| Rekomendasi model sesuai perangkat keras | Mendeteksi CPU, memori, kartu grafis NVIDIA, dan Ollama, lalu memilih kombinasi model secara otomatis; model yang kurang baru diunduh saat pertama kali menekan "Mulai" |
| Glosarium | Bawaan 53 istilah siaran langsung bahasa Jepang (赤スパ, 推し, メン限…) dan kata Mandarin–Jepang yang mudah tertukar untuk terjemahan Mandarin ke Jepang; Anda juga bisa menambah entri sendiri serta mengimpor/mengekspor CSV |
| Koreksi pengenalan dan info siaran | Otomatis membetulkan kata yang sering salah dikenali; isi nama streamer, nama game, atau deskripsi siaran agar nama diterjemahkan secara konsisten |
| Ekspor transkrip | SRT, VTT, TXT, CSV; bisa diatur agar disimpan otomatis setiap kali sesi selesai |
| File teks untuk OBS | Menulis subtitle saat ini ke file teks untuk dipakai opsi "Baca dari berkas" pada sumber "Teks (GDI+)" di OBS; tidak membuka port jaringan |
| Jendela subtitle | Transparan, selalu di atas, bisa ditembus klik mouse; ukuran huruf, latar, posisi, jumlah kalimat, durasi tampil minimum tiap kalimat, lama diam sebelum memudar, ukuran dan posisi teks asli, serta lebar baris semuanya bisa diatur; bisa dijeda dan subtitle terbaru bisa dilihat kembali |
| Bilah status | Meter volume, latensi (median dari selesai berbicara sampai subtitle lengkap); peringatan saat pengenalan tertinggal, tidak ada suara selama 15 detik, atau suara latar terlalu keras |
| Pengelolaan model | Mengunduh, melihat penggunaan disk, dan menghapus model pengenalan; mengunduhkan model terjemahan di Ollama untuk Anda |
| Pembaruan otomatis | Memeriksa GitHub Releases saat dijalankan, mengunduh dan memverifikasi tanda tangan, lalu memasang |

## Bahasa yang didukung

Bahasa yang didengar dan bahasa subtitle (20 bahasa, kombinasi bebas):

| | | | | |
|---|---|---|---|---|
| 日本語 | English | 繁體中文（台灣） | 简体中文 | 한국어 |
| Español | Français | Deutsch | Português | Русский |
| Italiano | العربية | हिन्दी | Bahasa Indonesia | Tiếng Việt |
| ไทย | Türkçe | Polski | Nederlands | Українська |

- Subtitle Mandarin Tradisional dikonversi dengan OpenCC `s2twp` ke aksara Tradisional standar Taiwan dan kosakata Taiwan (misalnya 视频 → 影片); konversi hanya dilakukan bila terjemahan mengandung aksara Sederhana, dan "台灣" tidak diubah menjadi "臺灣".
- Bila bahasa yang didengar dan bahasa subtitle sama, tidak ada terjemahan (antara Mandarin Tradisional dan Sederhana hanya dilakukan konversi aksara).
- Kualitas pengenalan suara berbeda-beda menurut bahasa. Bila tingkat kesalahan model untuk suatu bahasa melebihi 20% (misalnya bahasa Thai, Arab, dan Hindi pada large-v3-turbo, atau bahasa Vietnam pada large-v3), keterangan rekomendasi akan memperingatkan bahwa kualitas pengenalan rendah; untuk bahasa Thai, Arab, dan Hindi pada kartu grafis 8 GB ke atas, subtitle langsung juga beralih ke large-v3.

Bahasa antarmuka (13): 繁體中文（台灣）, 简体中文, English, 日本語, 한국어, Español, Français, Deutsch, Português (Brasil), Русский, Tiếng Việt, ไทย, Bahasa Indonesia.
Bahasa Arab bisa dipakai sebagai bahasa yang didengar atau bahasa subtitle, tetapi tidak ada antarmuka bahasa Arab (antarmuka tidak mendukung tata letak kanan ke kiri).

## Kombinasi yang direkomendasikan (sesuai perangkat keras)

kikitori mendeteksi dan menerapkannya secara otomatis; tabel di bawah hanya agar Anda tahu apa yang akan didapat. Dasarnya adalah evaluasi proyek ini (`experiments/vtuber_eval/`, `experiments/translate_eval/`); aturan dan rumusnya lihat [`docs/specs/hardware.md`](docs/specs/hardware.md).

| Komputer Anda | Subtitle langsung | Subtitle lengkap | Terjemahan (Ollama) |
|---|---|---|---|
| NVIDIA 16 GB ke atas | large-v3-turbo (float16) | large-v3 (float16) | qwen3.5:9b |
| NVIDIA 12 GB (misalnya RTX 5070) | large-v3-turbo (int8_float16) | large-v3 (int8_float16) | qwen3.5:9b |
| NVIDIA 8–11 GB | large-v3-turbo (beralih ke small bila memori grafis tepat di batas minimum 8 GB) | large-v3 (presisi dipilih sesuai memori grafis) | qwen3.5:4b |
| NVIDIA 4–8 GB | base | large-v3-turbo | qwen3.5:4b |
| NVIDIA kurang dari 4 GB | base | small | qwen3.5:4b |
| Tanpa GPU NVIDIA, RAM ≥ 16 GB, 5 inti ke atas | base (CPU) | small (CPU) | qwen3.5:4b |
| Tanpa GPU NVIDIA, RAM lebih kecil atau 4 inti ke bawah | tiny (CPU) | base (CPU) | qwen3.5:4b |
| Mac (Apple Silicon) | base (CPU) | small (CPU) | Tergantung ukuran memori terpadu (16 GB memakai qwen3.5:9b) |

- Presisi int8_float16 dan float16 memberikan kualitas pengenalan yang sama dalam pengukuran (perbedaannya tidak signifikan), hanya saja memori grafis yang dipakai sekitar separuhnya; saat berbagi kartu grafis dengan model terjemahan, kikitori memprioritaskan model terjemahan agar bisa memakai ukuran satu tingkat lebih besar.
- Ukuran unduhan model pengenalan: large-v3-turbo 1.62 GB, large-v3 3.09 GB, small 487 MB, base 148 MB, tiny 78 MB. Model terjemahan: qwen3.5:9b sekitar 6.6 GB, qwen3.5:4b sekitar 3.3 GB.
- GPU NVIDIA membutuhkan cuBLAS 12 (sekitar 570 MB). kikitori tidak menyertakannya dalam penginstal; unduh dengan satu klik setelah menyetujui NVIDIA EULA di halaman Model. Bila tidak terpasang, CPU yang dipakai.
- Bila server terjemahan berada di komputer lain (jarak jauh), model terjemahan tidak dipilih otomatis; pilih sendiri di halaman Model.
- Untuk terjemahan, Qwen3.5 (Apache-2.0) selalu direkomendasikan lebih dulu: 9B bila memori grafis cukup, selain itu 4B. Kartu grafis 4 GB ke bawah atau tanpa GPU NVIDIA juga memakai qwen3.5:4b; Ollama akan menjalankan bagian yang tidak muat di CPU sehingga lebih lambat (latensi terjemahan untuk kondisi ini belum diukur).
- Tencent HY-MT memiliki batasan wilayah dan baru mungkin direkomendasikan setelah Anda mencentang "Saya menyatakan bahwa saya tidak menggunakan kikitori di Uni Eropa, Inggris, atau Korea Selatan…" di halaman Pengaturan; Sakura (lisensi nonkomersial) tidak pernah direkomendasikan dan hanya bisa dipilih manual.
- Kecepatan pengenalan di macOS dan Linux serta kecepatan terjemahan di CPU belum diukur (lihat "Keterbatasan yang diketahui").

## Mulai menggunakan

1. Unduh penginstal dari [GitHub Releases](https://github.com/alianlbj23/kikitori/releases) (petunjuk pemasangan tiap platform ada di halaman Release: peringatan SmartScreen / Gatekeeper karena tidak bertanda tangan, fuse3 dan font di Linux).
2. Untuk menerjemahkan, pasang [Ollama](https://ollama.com/download) dan biarkan berjalan di latar belakang. Bila kikitori tidak mendeteksinya, akan muncul saran "Pasang Ollama" atau beralih ke "Tampilkan teks asli saja".
3. Buka kikitori. Saat pertama kali dijalankan, kombinasi yang direkomendasikan sesuai perangkat keras diterapkan otomatis.
4. Di bagian "Bahasa" pada halaman Pengaturan, pilih bahasa yang didengar dan bahasa subtitle. Setelah dipilih, yang ditampilkan hanya kombinasi yang direkomendasikan dan ukuran unduhannya, **tidak langsung diunduh**.
5. Pilih sumber suara, lalu tekan "Mulai". Model pengenalan dan model terjemahan (bila memakai Ollama) yang kurang akan diunduh lebih dulu, lalu dimulai otomatis setelah selesai.

### Tampilkan teks asli saja

Sakelar "Tampilkan terjemahan" ada di bilah alat halaman Transkrip dan di bagian "Subtitle" halaman Pengaturan (atau pintasan Ctrl+Alt+J). Saat dimatikan:

- Engine sama sekali tidak terhubung ke server terjemahan, tidak perlu memasang Ollama, dan tidak memakai GPU untuk model terjemahan;
- Jendela subtitle menampilkan teks asli dengan ukuran huruf dan warna subtitle utama;
- Mengganti saat sedang berjalan langsung berlaku, kalimat yang sudah tampil tidak akan diterjemahkan susulan.

### Furigana

"Tampilkan furigana di atas teks asli bahasa Jepang" di halaman Pengaturan: bila bahasa yang didengar adalah bahasa Jepang, cara baca hiragana ditampilkan di atas kanji teks asli (okurigana tidak ditandai ulang). Saat pertama kali diaktifkan, SudachiDict core (Apache-2.0, sekitar 69 MiB) diunduh; penggunaannya bisa dilihat dan dihapus di halaman Model. Hanya memengaruhi tampilan, subtitle yang diekspor tidak berubah. Rancangannya lihat [`docs/specs/furigana.md`](docs/specs/furigana.md).

### Glosarium

"Glosarium" di halaman Pengaturan:

- **Daftar istilah siaran langsung bawaan** (aktif secara default): dipakai bila teks asli berbahasa Jepang dan subtitle berbahasa Mandarin Tradisional / Mandarin Sederhana / Inggris / Korea, misalnya untuk subtitle bahasa Inggris: スパチャ → Super Chat, メン限 → members-only, 切り抜き → clip. Hanya istilah yang benar-benar muncul di teks asli kalimat tersebut yang dimasukkan ke prompt terjemahan (maksimal 8 entri per kalimat).
- **Kata Mandarin–Jepang yang mudah tertukar** (mengikuti sakelar daftar bawaan): bila teks asli berbahasa Mandarin dan subtitle berbahasa Jepang, model diingatkan tentang kata yang tulisannya sama atau mirip dalam bahasa Mandarin dan Jepang tetapi artinya berbeda, misalnya 便當 → 弁当, 節目 → 番組, 老婆 → 妻; untuk terjemahan Mandarin ke Jepang, instruksi kepada model juga ditulis dalam bahasa Mandarin agar model lebih jarang membaca bahasa Mandarin dengan makna bahasa Jepang.
- **Entri kustom**: teks asli, terjemahan, dan bahasa subtitle yang berlaku (atau "Semua bahasa"), maksimal 500 entri, berlaku untuk bahasa yang didengar apa pun; untuk tulisan yang sama, entri kustom lebih diutamakan daripada daftar bawaan. Pencocokan tidak membedakan huruf besar/kecil maupun lebar penuh/setengah lebar.
- CSV bisa diimpor/diekspor (UTF-8, dipisahkan koma, titik koma, atau Tab; file yang disimpan Excel juga bisa dibaca). Setelah menekan "Simpan glosarium", perubahan berlaku saat subtitle dimulai berikutnya.

### Ekspor transkrip

"Ekspor" di halaman Transkrip: SRT, VTT, TXT, CSV; isinya bisa dipilih "Teks asli dan terjemahan", "Hanya teks asli", atau "Hanya terjemahan". Hanya subtitle lengkap yang diekspor (subtitle langsung masih berubah).
File disimpan di `Dokumen/kikitori/transcripts/YYYY-MM-DD_HHMMSS_<mode>.<ekstensi>`, kode waktu dihitung sejak subtitle dimulai.
Di halaman Pengaturan, Anda bisa mengaktifkan "Simpan otomatis setiap kali sesi selesai (SRT dan TXT)"; setelah menekan "Hentikan", aplikasi menunggu subtitle lengkap kalimat terakhir tiba (maksimal 2 detik) lalu menyimpan.

### File teks untuk OBS

"Transkrip dan OBS" → "Tulis subtitle ke file teks (untuk OBS)" di halaman Pengaturan: teks asli, terjemahan, atau keduanya.
kikitori menimpa `Dokumen/kikitori/obs/subtitle.txt` dengan kalimat yang sedang tampil di jendela subtitle (UTF-8, penulisan atomik, maksimal 10 kali per detik);
di OBS, tambahkan sumber "Teks (GDI+)", centang "Baca dari berkas", dan arahkan ke file ini. File dikosongkan saat subtitle berakhir, memudar karena diam, atau saat subtitle dibersihkan.
Tidak ada port jaringan yang dibuka, jadi tidak ada cara yang memakai sumber "Peramban" di OBS.

### Pintasan global

| Windows / Linux | macOS | Aksi |
|---|---|---|
| Ctrl+Alt+K | ⌘+Option+K | Mengalihkan mode tembus klik mouse pada jendela subtitle |
| Ctrl+Alt+J | ⌘+Option+J | Mengalihkan "Tampilkan terjemahan" |
| Ctrl+Alt+U | ⌘+Option+U | Membersihkan jendela subtitle (dan file teks OBS); transkrip tidak terpengaruh |
| Ctrl+Alt+P | ⌘+Option+P | Menjeda / melanjutkan jendela subtitle; transkrip tetap berjalan seperti biasa |
| Ctrl+Alt+H | ⌘+Option+H | Menampilkan sebentar 10 kalimat terakhir di jendela subtitle |
| Ctrl+Alt+R | ⌘+Option+R | Saat penerjemahan lisan, menampilkan "Tolong ulangi sekali lagi" dalam bahasa lawan bicara di jendela subtitle |

Pintasan yang sudah dipakai program lain akan ditandai tidak bisa digunakan di halaman Transkrip, tanpa memengaruhi pintasan lainnya. Di Linux Wayland, pintasan global mungkin sama sekali tidak bisa didaftarkan.

## Privasi dan luring

- Suara, hasil pengenalan, dan terjemahan hanya diproses di komputer Anda. Secara default, terjemahan dikirim ke Ollama lokal (`http://localhost:11434/v1`); bila Anda mengganti layanan terjemahan ke alamat lain, teks subtitle akan dikirim ke sana.
- Yang terhubung ke jaringan hanya: mengunduh model (model pengenalan dari Hugging Face, model terjemahan lewat Ollama, komponen GPU dari PyPI; semuanya hanya terjadi saat Anda menekan mulai atau unduh) dan pemeriksaan pembaruan saat dijalankan (GitHub Releases). Setelah semua model terunduh, subtitle itu sendiri bisa dipakai sepenuhnya secara luring.
- API key hanya dibaca dari variabel lingkungan (pengaturan hanya menyimpan nama variabelnya, default `KIKITORI_TRANSLATE_API_KEY`) dan tidak ditulis ke file mana pun.
- "Salin info diagnostik" di halaman "Tentang" hanya menyalin ke papan klip, menyamarkan kunci, kredensial di URL, dan nama pengguna; glosarium hanya dicantumkan jumlah entrinya.
- Engine dan aplikasi desktop berkomunikasi hanya lewat input/output standar, tanpa membuka port jaringan.

## Fitur baru 0.3.0

Ringkasan (penjelasan lengkap dan catatan peningkatan versi lihat [docs/release-notes/v0.3.0.md](docs/release-notes/v0.3.0.md); untuk 0.2.0 lihat [docs/release-notes/v0.2.0.md](docs/release-notes/v0.2.0.md)):

- Label pembicara: saat kolaborasi banyak orang, setiap kalimat ditandai "Pembicara 1, 2…"; jumlah bisa ditentukan otomatis atau ditetapkan 2–6 orang; klik label untuk mengganti nama, label dengan nama sama digabung (model sekitar 27 MiB, diunduh saat pertama kali dipilih)
- Terjemahan langsung dibagi menjadi bagian stabil dan bagian yang masih berubah sehingga seluruh kalimat tidak lagi berkedip; kata baru muncul perlahan, subtitle tidak melompat naik-turun
- Mode penerjemahan lisan menandai "Anda / Lawan bicara", Ctrl+Alt+R menampilkan "Tolong ulangi sekali lagi" dalam bahasa lawan bicara
- Aturan koreksi pengenalan, memilih kata dari transkrip untuk ditambahkan ke glosarium, info siaran (nama streamer, nama game, deskripsi siaran)
- Tata letak baca: ukuran dan posisi teks asli, lebar baris, durasi tampil diperpanjang sesuai jumlah karakter, furigana hanya pada kemunculan pertama
- Pintasan baru Ctrl+Alt+P untuk menjeda subtitle, Ctrl+Alt+H untuk melihat kembali subtitle terbaru; atur ulang posisi jendela subtitle; peringatan saat suara latar terlalu keras

## Keterbatasan yang diketahui

- **Hanya diuji lengkap di Windows (RTX 5070)**. Penangkapan suara di perangkat nyata macOS dan Linux, serta kecepatan terjemahan di Apple Silicon dan CPU belum diukur; Linux hanya diuji penerimaan di dalam kontainer Docker.
- **Kombinasi rekomendasi baru hanya diukur ujung ke ujung di RTX 5070 (12 GB)**: saat turbo + large-v3 (int8_float16) dan qwen3.5:9b berjalan bersamaan, seluruh kartu memakai maksimal 10.8 GB, latensi subtitle langsung sekitar 0.31 detik, dari selesai berbicara sampai subtitle lengkap sekitar 0.92 detik (median, lihat [`tasks/acceptance-report-v0.2.0.md`](tasks/acceptance-report-v0.2.0.md)).
- **Materi evaluasi akurasi pengenalan bukan VTuber**: yang dipakai adalah percakapan umum di YouTube (YODAS), bacaan, dan suara televisi, ditambah campuran BGM; tidak ada data siaran bernyanyi, teriakan, atau kolaborasi banyak orang. Suara nyanyian bisa lolos dari deteksi suara manusia dan memunculkan subtitle yang aneh.
- **Label pembicara hanya diukur pada potongan YouTube yang disambung** (2 orang sekitar 92–100%, 3–4 orang sekitar 80–85% berlabel benar): tidak ada data saling menyela, sahutan singkat kurang dari 1 detik, maupun satu orang yang berganti suara, sehingga pada kolaborasi sungguhan hasilnya bisa lebih rendah; label tidak ditulis ke file teks OBS.
- Bahasa selain bahasa Inggris hanya memiliki tolok ukur publik dan uji halusinasi dengan audio non-ucapan, belum diukur pada ucapan nyata; daftar frasa halusinasi belum ditinjau penutur asli.
- Beberapa kalimat penutup baku (misalnya "チャンネル登録よろしくお願いします") dianggap sebagai halusinasi Whisper dan disaring seluruh kalimatnya, meskipun streamer benar-benar mengucapkannya.
- Ollama harus dipasang terpisah untuk menerjemahkan; untuk server kompatibel OpenAI selain Ollama, model harus dipasang sendiri.
- Uji tekanan memori untuk penggunaan lama sudah dilakukan di Windows (sesi engine 20 menit stabil, jendela utama 22 menit +0.7 MiB/menit); macOS dan Linux belum diuji.
- Hanya bisa ada satu sesi subtitle dalam satu waktu; setelah perangkat dicabut, tidak tersambung ulang otomatis.
- Tidak didukung: klik kata untuk mencari arti, menangkap suara dari satu aplikasi saja, mode siaran bernyanyi, pemisahan pembicara yang berganti di tengah kalimat atau berbicara bersamaan, sumber "Peramban" di OBS (alasannya lihat bagian "Belum dikerjakan untuk saat ini" (先不做) di dokumen berbahasa Mandarin [`docs/specs/competitive-features.md`](docs/specs/competitive-features.md) dan [`docs/adr/`](docs/adr/)).
- Penginstal tidak memiliki penandatanganan kode berbayar (Windows SmartScreen dan macOS Gatekeeper akan memberi peringatan).

## Lisensi

kikitori sendiri dirilis dengan lisensi MIT ([LICENSE](LICENSE)). Teks lengkap lisensi komponen pihak ketiga ada di direktori instalasi dan bisa dibuka dari halaman "Tentang".

Model tidak didistribusikan bersama penginstal; saat Anda mengunduhnya, lisensi masing-masing model berlaku:

| Model | Lisensi | Catatan |
|---|---|---|
| Whisper (versi konversi CTranslate2) | MIT | — |
| Silero VAD (disertakan dalam program) | MIT | — |
| 3D-Speaker CAM++ (label pembicara, diunduh saat dipilih) | Apache-2.0 | — |
| Qwen3.5 (model terjemahan default) | Apache-2.0 | — |
| Tencent HY-MT1.5 | Tencent HY Community License | **Tidak berlaku di Uni Eropa, Inggris, Korea Selatan**; kikitori tidak merekomendasikannya secara default, wilayah Anda perlu dikonfirmasi di halaman Pengaturan |
| Sakura-GalTransl | CC BY-NC-SA 4.0 | **Dilarang untuk penggunaan komersial**; tidak akan direkomendasikan, hanya bisa dipilih manual |
| NVIDIA cuBLAS | NVIDIA CUDA EULA | Harus disetujui sebelum diunduh |

## Untuk pengembang

- Arsitektur: [docs/architecture.md](docs/architecture.md); spesifikasi tiap modul: [docs/specs/](docs/specs/); panduan belajar: [docs/learning/](docs/learning/README.md)
- Evaluasi dan eksperimen: `experiments/*/README.md`; laporan riset: `reports/`

```text
uv sync                                       Pasang dependensi Python
uv run python -m unittest discover -s tests   Tes Python
uv run ruff check                             Lint
uv run kikitori                               Menu interaktif (menjalankan engine, menampilkan perangkat audio…)
cd app && npm ci && npm run menu              Menu pengembangan aplikasi desktop (butuh Node dan Rust)
cd app && npm test && npx tsc --noEmit        Tes frontend dan pemeriksaan tipe
```
