# kikitori（聞き取り）

[繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md) | [English](README.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md) | [Русский](README.ru.md) | **Tiếng Việt** | [ไทย](README.th.md) | [Bahasa Indonesia](README.id.md)

*Đây là bản dịch của README.zh-TW.md tiếng Trung phồn thể; nếu có chỗ khác nhau, README.zh-TW.md là bản có giá trị. Bản dịch được thực hiện với sự hỗ trợ của AI; rất hoan nghênh góp ý sửa lỗi qua [issues](https://github.com/alianlbj23/kikitori/issues).*

kikitori nghe âm thanh máy tính của bạn đang phát (hoặc micrô), nhận dạng thành văn bản theo thời gian thực, dịch sang ngôn ngữ bạn chọn và hiển thị bằng phụ đề song ngữ nổi trên màn hình.
Nhận dạng giọng nói (Whisper) và dịch (Ollama cục bộ) đều chạy trên chính máy tính của bạn: âm thanh và văn bản không được tải lên bất kỳ dịch vụ đám mây nào.

Tình huống sử dụng chính là **xem livestream của VTuber tiếng Nhật** (có BGM, hiệu ứng âm thanh game, từ lóng livestream); mô hình đề xuất và các tham số đều đã được điều chỉnh theo kết quả đo thực tế của dự án này;
mọi cặp kết hợp khác giữa 20 ngôn ngữ cũng dùng được.

- Nền tảng: Windows 10/11 x64 (nền tảng kiểm chứng chính), Linux x64. macOS (Apple Silicon) chưa được thử trên máy Mac thật nên tạm thời chưa có bộ cài cho macOS.
- Giấy phép: MIT (mỗi mô hình có giấy phép riêng, xem "Giấy phép")
- Phiên bản hiện tại: 0.3.1 ([nội dung cập nhật](docs/release-notes/v0.3.1.md))

## Tính năng

| Tính năng | Mô tả |
|---|---|
| Dịch video | Thu âm thanh hệ thống đang phát (Windows WASAPI loopback / Linux PulseAudio monitor / macOS ScreenCaptureKit), dịch từ ngôn ngữ nghe sang ngôn ngữ phụ đề |
| Phiên dịch trực tiếp | Thu micrô; khi bạn nói "Ngôn ngữ của tôi" thì dịch sang ngôn ngữ của người kia, khi người kia nói thì dịch sang ngôn ngữ của bạn |
| Phụ đề hai luồng: nhanh trước, chính xác sau | Khi câu vẫn đang được nói, dùng mô hình nhanh hơn để hiện "phụ đề tức thời"; nói xong thì dùng mô hình chính xác hơn tính lại "phụ đề hoàn chỉnh" để thay thế |
| Phụ đề tức thời ổn định | Trong bản gốc và bản dịch của phụ đề tức thời, phần đầu đã không còn thay đổi được hiển thị bình thường, phần đuôi còn đang thay đổi hiển thị màu nhạt; chữ mới hiện ra từ từ, cả khối không còn nhảy lên xuống |
| Nhãn người nói | Khi collab nhiều người, ghi "Người nói 1, 2…" trước mỗi câu; có thể tự động xác định số người hoặc chỉ định 2–6 người, nhấp vào nhãn để đổi tên; ở chế độ phiên dịch ghi "Bạn / Đối phương" |
| Kết hợp tùy ý 20 ngôn ngữ | Ngôn ngữ nghe (có thể chọn "Tự động phát hiện") và ngôn ngữ phụ đề chọn tùy ý trong 20 ngôn ngữ, xem "Ngôn ngữ được hỗ trợ" |
| 13 ngôn ngữ giao diện | Mặc định theo hệ điều hành, có thể đổi ở trang Cài đặt |
| Chỉ hiển thị bản gốc | Tắt "Hiển thị bản dịch" để chỉ xem bản gốc: không cần Ollama, không chiếm GPU; có thể chuyển cả khi đang chạy |
| Furigana | Ghi cách đọc trên chữ Hán của bản gốc tiếng Nhật (SudachiDict, chỉ tải khoảng 69 MiB khi bật lần đầu) |
| Đề xuất mô hình theo phần cứng | Phát hiện CPU, bộ nhớ, card đồ họa NVIDIA và Ollama, tự chọn tổ hợp mô hình; chỉ tải các mô hình còn thiếu khi bạn nhấn "Bắt đầu" lần đầu |
| Bảng thuật ngữ | Có sẵn 53 từ lóng livestream tiếng Nhật (赤スパ, 推し, メン限…) và các từ Trung–Nhật dễ nhầm khi dịch Trung sang Nhật; cũng có thể tự thêm mục, nhập/xuất CSV |
| Sửa kết quả nhận dạng và thông tin buổi phát | Tự động sửa những chữ mà nhận dạng hay sai; điền tên streamer, tên game hoặc phần mô tả buổi phát để tên được dịch thống nhất |
| Xuất bản ghi phụ đề | SRT, VTT, TXT, CSV; có thể đặt tự động lưu mỗi khi kết thúc |
| Tệp văn bản cho OBS | Ghi phụ đề hiện tại vào tệp văn bản để nguồn "Văn bản (GDI+)" của OBS dùng qua tùy chọn "Đọc từ tệp"; không mở cổng mạng |
| Cửa sổ phụ đề | Trong suốt, luôn nằm trên cùng, cho chuột xuyên qua được; cỡ chữ, nền, vị trí, số câu, thời gian hiển thị tối thiểu của mỗi câu, thời gian không hoạt động trước khi mờ dần, cỡ và vị trí bản gốc, độ rộng mỗi dòng đều chỉnh được; có thể tạm dừng và xem lại phụ đề gần đây |
| Thanh trạng thái | Đồng hồ âm lượng, độ trễ (trung vị từ lúc nói xong đến khi có phụ đề hoàn chỉnh); cảnh báo khi nhận dạng không theo kịp, 15 giây không có âm thanh hoặc âm thanh nền quá to |
| Quản lý mô hình | Tải, xem dung lượng đĩa, xóa mô hình nhận dạng; tải mô hình dịch trong Ollama giúp bạn |
| Tự động cập nhật | Kiểm tra GitHub Releases khi khởi động, tải về và xác minh chữ ký rồi cài đặt |

## Ngôn ngữ được hỗ trợ

Ngôn ngữ nghe và ngôn ngữ phụ đề (20 ngôn ngữ, kết hợp tùy ý):

| | | | | |
|---|---|---|---|---|
| 日本語 | English | 繁體中文（台灣） | 简体中文 | 한국어 |
| Español | Français | Deutsch | Português | Русский |
| Italiano | العربية | हिन्दी | Bahasa Indonesia | Tiếng Việt |
| ไทย | Türkçe | Polski | Nederlands | Українська |

- Phụ đề tiếng Trung phồn thể được OpenCC `s2twp` chuyển sang chữ phồn thể chuẩn Đài Loan và cách dùng từ của Đài Loan (ví dụ 视频 → 影片); chỉ chuyển khi bản dịch có chữ giản thể, và không đổi "台灣" thành "臺灣".
- Khi ngôn ngữ nghe và ngôn ngữ phụ đề giống nhau thì không dịch (giữa tiếng Trung phồn thể và giản thể chỉ chuyển đổi dạng chữ).
- Chất lượng nhận dạng giọng nói khác nhau tùy ngôn ngữ. Khi tỷ lệ lỗi của mô hình với một ngôn ngữ vượt quá 20% (ví dụ tiếng Thái, tiếng Ả Rập, tiếng Hindi với large-v3-turbo; tiếng Việt với large-v3), phần giải thích đề xuất sẽ cảnh báo chất lượng nhận dạng thấp; với tiếng Thái, tiếng Ả Rập, tiếng Hindi trên card đồ họa từ 8 GB trở lên, phụ đề tức thời cũng chuyển sang dùng large-v3.

Ngôn ngữ giao diện (13): 繁體中文（台灣）, 简体中文, English, 日本語, 한국어, Español, Français, Deutsch, Português (Brasil), Русский, Tiếng Việt, ไทย, Bahasa Indonesia.
Tiếng Ả Rập có thể dùng làm ngôn ngữ nghe hoặc ngôn ngữ phụ đề, nhưng không có giao diện tiếng Ả Rập (giao diện không hỗ trợ bố cục từ phải sang trái).

## Tổ hợp đề xuất (theo phần cứng)

kikitori tự phát hiện và áp dụng; bảng dưới đây chỉ để bạn biết mình sẽ nhận được gì. Căn cứ là các đánh giá của dự án này (`experiments/vtuber_eval/`, `experiments/translate_eval/`); quy tắc và công thức xem [`docs/specs/hardware.md`](docs/specs/hardware.md).

| Máy tính của bạn | Phụ đề tức thời | Phụ đề hoàn chỉnh | Dịch (Ollama) |
|---|---|---|---|
| NVIDIA từ 16 GB trở lên | large-v3-turbo (float16) | large-v3 (float16) | qwen3.5:9b |
| NVIDIA 12 GB (ví dụ RTX 5070) | large-v3-turbo (int8_float16) | large-v3 (int8_float16) | qwen3.5:9b |
| NVIDIA 8–11 GB | large-v3-turbo (chuyển sang small khi bộ nhớ đồ họa vừa đúng mức tối thiểu 8 GB) | large-v3 (chọn precision theo bộ nhớ đồ họa) | qwen3.5:4b |
| NVIDIA 4–8 GB | base | large-v3-turbo | qwen3.5:4b |
| NVIDIA dưới 4 GB | base | small | qwen3.5:4b |
| Không có GPU NVIDIA, RAM ≥ 16 GB, từ 5 nhân trở lên | base (CPU) | small (CPU) | qwen3.5:4b |
| Không có GPU NVIDIA, RAM ít hơn hoặc từ 4 nhân trở xuống | tiny (CPU) | base (CPU) | qwen3.5:4b |
| Mac (Apple Silicon) | base (CPU) | small (CPU) | Tùy dung lượng bộ nhớ hợp nhất (16 GB dùng qwen3.5:9b) |

- Với precision int8_float16 và float16, chất lượng nhận dạng đo thực tế như nhau (khác biệt không đáng kể), chỉ là bộ nhớ đồ họa giảm khoảng một nửa; khi dùng chung card đồ họa với mô hình dịch, kikitori ưu tiên cho mô hình dịch dùng bản lớn hơn một cấp.
- Dung lượng tải mô hình nhận dạng: large-v3-turbo 1.62 GB, large-v3 3.09 GB, small 487 MB, base 148 MB, tiny 78 MB. Mô hình dịch: qwen3.5:9b khoảng 6.6 GB, qwen3.5:4b khoảng 3.3 GB.
- GPU NVIDIA cần cuBLAS 12 (khoảng 570 MB). kikitori không đóng gói nó trong bộ cài; sau khi đồng ý NVIDIA EULA ở trang Mô hình, có thể tải bằng một lần nhấn. Nếu chưa cài thì dùng CPU.
- Khi máy chủ dịch nằm trên máy tính khác (từ xa), mô hình dịch sẽ không được tự chọn; hãy tự chọn ở trang Mô hình.
- Về dịch, luôn ưu tiên đề xuất Qwen3.5 (Apache-2.0): đủ bộ nhớ đồ họa thì dùng 9B, nếu không thì 4B. Khi card đồ họa từ 4 GB trở xuống hoặc không có GPU NVIDIA cũng dùng qwen3.5:4b; Ollama sẽ đưa phần không vừa sang chạy trên CPU, tốc độ chậm hơn (độ trễ dịch trong trường hợp này chưa được đo thực tế).
- Tencent HY-MT có giới hạn khu vực, chỉ có thể được đề xuất sau khi bạn đánh dấu "Tôi xác nhận rằng tôi không sử dụng kikitori tại EU, Vương quốc Anh hoặc Hàn Quốc…" ở trang Cài đặt; Sakura (giấy phép phi thương mại) không bao giờ được đề xuất, chỉ có thể chọn thủ công.
- Tốc độ nhận dạng trên macOS và Linux, cũng như tốc độ dịch trên CPU đều chưa được đo thực tế (xem "Hạn chế đã biết").

## Bắt đầu sử dụng

1. Tải bộ cài từ [GitHub Releases](https://github.com/alianlbj23/kikitori/releases) (lưu ý cài đặt cho từng nền tảng có trên trang Release: cảnh báo SmartScreen / Gatekeeper do chưa ký, fuse3 và phông chữ trên Linux).
2. Nếu muốn dịch, hãy cài [Ollama](https://ollama.com/download) và để nó chạy nền. Khi không phát hiện được Ollama, kikitori sẽ gợi ý "Cài Ollama" hoặc chuyển sang "Chỉ hiển thị bản gốc".
3. Mở kikitori. Lần khởi động đầu tiên sẽ tự áp dụng tổ hợp đề xuất theo phần cứng.
4. Ở mục "Ngôn ngữ" của trang Cài đặt, chọn ngôn ngữ nghe và ngôn ngữ phụ đề. Sau khi chọn, ứng dụng chỉ hiển thị tổ hợp đề xuất và dung lượng tải, **không tải ngay**.
5. Chọn nguồn âm thanh, nhấn "Bắt đầu". Các mô hình nhận dạng và mô hình dịch (khi dùng Ollama) còn thiếu sẽ được tải trước, xong sẽ tự bắt đầu.

### Chỉ hiển thị bản gốc

Công tắc "Hiển thị bản dịch" trên thanh công cụ của trang Bản ghi và trong mục "Phụ đề" của trang Cài đặt (hoặc phím tắt Ctrl+Alt+J). Khi tắt:

- Engine hoàn toàn không kết nối với máy chủ dịch, không cần cài Ollama, cũng không chiếm GPU cho mô hình dịch;
- Cửa sổ phụ đề hiển thị bản gốc bằng cỡ chữ và màu của phụ đề chính;
- Chuyển khi đang chạy có hiệu lực ngay, các câu đã hiển thị sẽ không được dịch bù.

### Furigana

"Hiển thị furigana trên bản gốc tiếng Nhật" ở trang Cài đặt: khi ngôn ngữ nghe là tiếng Nhật, ghi cách đọc bằng hiragana phía trên chữ Hán của bản gốc (không ghi lặp okurigana). Lần bật đầu tiên sẽ tải SudachiDict core (Apache-2.0, khoảng 69 MiB); có thể xem dung lượng và xóa ở trang Mô hình. Chỉ ảnh hưởng đến phần hiển thị, phụ đề xuất ra không thay đổi. Thiết kế xem [`docs/specs/furigana.md`](docs/specs/furigana.md).

### Bảng thuật ngữ

"Bảng thuật ngữ" ở trang Cài đặt:

- **Bảng từ lóng livestream có sẵn** (bật theo mặc định): dùng khi bản gốc là tiếng Nhật và phụ đề là tiếng Trung phồn thể / tiếng Trung giản thể / tiếng Anh / tiếng Hàn, ví dụ với phụ đề tiếng Anh: スパチャ → Super Chat, メン限 → members-only, 切り抜き → clip. Chỉ những từ thực sự xuất hiện trong câu gốc đó mới được đưa vào prompt dịch (tối đa 8 mục mỗi câu).
- **Từ Trung–Nhật dễ nhầm** (bật/tắt theo bảng có sẵn): khi bản gốc là tiếng Trung và phụ đề là tiếng Nhật, nhắc mô hình về những từ viết giống hoặc gần giống nhau giữa tiếng Trung và tiếng Nhật nhưng khác nghĩa, ví dụ 便當 → 弁当, 節目 → 番組, 老婆 → 妻; khi dịch Trung sang Nhật, chỉ dẫn cho mô hình cũng được viết bằng tiếng Trung, để mô hình ít hiểu tiếng Trung theo nghĩa tiếng Nhật hơn.
- **Mục tự định nghĩa**: bản gốc, bản dịch, ngôn ngữ phụ đề áp dụng (hoặc "Mọi ngôn ngữ"), tối đa 500 mục, áp dụng cho mọi ngôn ngữ nghe; cùng một cách viết thì mục tự định nghĩa được ưu tiên hơn bảng có sẵn. So khớp không phân biệt chữ hoa/thường và toàn độ/bán độ (full-width/half-width).
- Có thể nhập/xuất CSV (UTF-8, phân tách bằng dấu phẩy, dấu chấm phẩy hoặc Tab; đọc được cả tệp do Excel lưu). Sau khi nhấn "Lưu bảng thuật ngữ", thay đổi có hiệu lực từ lần bắt đầu phụ đề tiếp theo.

### Xuất bản ghi phụ đề

"Xuất" ở trang Bản ghi: SRT, VTT, TXT, CSV; nội dung có thể chọn "Bản gốc và bản dịch", "Chỉ bản gốc", "Chỉ bản dịch". Chỉ xuất phụ đề hoàn chỉnh (phụ đề tức thời vẫn còn thay đổi).
Tệp được lưu tại `Tài liệu/kikitori/transcripts/YYYY-MM-DD_HHMMSS_<chế độ>.<phần mở rộng>`, mã thời gian tính từ lúc bắt đầu phụ đề.
Ở trang Cài đặt có thể bật "Tự động lưu mỗi khi kết thúc (SRT và TXT)"; sau khi nhấn "Dừng", ứng dụng sẽ đợi phụ đề hoàn chỉnh của câu cuối cùng được gửi đến (tối đa 2 giây) rồi mới lưu.

### Tệp văn bản cho OBS

"Bản ghi và OBS" → "Ghi phụ đề ra tệp văn bản (cho OBS)" ở trang Cài đặt: bản gốc, bản dịch hoặc cả hai.
kikitori ghi đè các câu đang có trong cửa sổ phụ đề vào `Tài liệu/kikitori/obs/subtitle.txt` (UTF-8, ghi nguyên tử, tối đa 10 lần mỗi giây);
trong OBS, thêm nguồn "Văn bản (GDI+)", đánh dấu "Đọc từ tệp" và trỏ tới tệp này. Tệp sẽ được xóa trống khi phụ đề kết thúc, khi mờ dần do không hoạt động hoặc khi xóa phụ đề.
Không mở bất kỳ cổng mạng nào, vì vậy không có cách dùng nguồn "Trình duyệt" của OBS.

### Phím tắt toàn cục

| Windows / Linux | macOS | Thao tác |
|---|---|---|
| Ctrl+Alt+K | ⌘+Option+K | Bật/tắt chuột xuyên qua cửa sổ phụ đề |
| Ctrl+Alt+J | ⌘+Option+J | Bật/tắt "Hiển thị bản dịch" |
| Ctrl+Alt+U | ⌘+Option+U | Xóa cửa sổ phụ đề (và tệp văn bản OBS); bản ghi phụ đề không bị ảnh hưởng |
| Ctrl+Alt+P | ⌘+Option+P | Tạm dừng / tiếp tục cửa sổ phụ đề; bản ghi phụ đề vẫn ghi như thường |
| Ctrl+Alt+H | ⌘+Option+H | Tạm hiện 10 câu gần nhất trên cửa sổ phụ đề |
| Ctrl+Alt+R | ⌘+Option+R | Khi phiên dịch, hiện "Xin hãy nói lại" bằng ngôn ngữ của đối phương trên cửa sổ phụ đề |

Phím tắt bị chương trình khác chiếm sẽ được đánh dấu là không dùng được trên trang Bản ghi, không ảnh hưởng các phím tắt khác. Trên Linux Wayland có thể hoàn toàn không đăng ký được phím tắt toàn cục.

## Quyền riêng tư và ngoại tuyến

- Âm thanh, kết quả nhận dạng và bản dịch chỉ được xử lý trên máy tính của bạn. Theo mặc định, nội dung dịch được gửi đến Ollama cục bộ (`http://localhost:11434/v1`); nếu bạn đổi dịch vụ dịch sang địa chỉ khác, văn bản phụ đề sẽ được gửi đến đó.
- Chỉ những việc sau mới kết nối mạng: tải mô hình (mô hình nhận dạng từ Hugging Face, mô hình dịch qua Ollama, thành phần GPU từ PyPI, đều chỉ xảy ra khi bạn nhấn bắt đầu hoặc tải), kiểm tra cập nhật khi khởi động (GitHub Releases). Sau khi đã tải xong các mô hình, bản thân phụ đề có thể dùng hoàn toàn ngoại tuyến.
- API key chỉ được đọc từ biến môi trường (cài đặt chỉ lưu tên biến, mặc định `KIKITORI_TRANSLATE_API_KEY`), không được ghi vào bất kỳ tệp nào.
- "Sao chép thông tin chẩn đoán" ở trang "Giới thiệu" chỉ sao chép vào clipboard, che khóa API, thông tin đăng nhập trong URL và tên người dùng; bảng thuật ngữ chỉ liệt kê số mục.
- Engine và ứng dụng desktop chỉ giao tiếp qua đầu vào/đầu ra chuẩn, không mở cổng mạng.

## Tính năng mới của 0.3.0

Tóm tắt (mô tả đầy đủ và lưu ý khi nâng cấp xem [docs/release-notes/v0.3.0.md](docs/release-notes/v0.3.0.md); 0.2.0 xem [docs/release-notes/v0.2.0.md](docs/release-notes/v0.2.0.md)):

- Nhãn người nói: khi collab nhiều người, mỗi câu được ghi "Người nói 1, 2…"; có thể tự động xác định số người hoặc chỉ định 2–6 người, nhấp vào nhãn để đổi tên, đổi thành cùng tên thì gộp lại (mô hình khoảng 27 MiB, tải khi chọn lần đầu)
- Bản dịch tức thời chia thành hai phần ổn định và đang thay đổi, không còn nhấp nháy cả câu; chữ mới hiện ra từ từ, phụ đề không nhảy lên xuống
- Chế độ phiên dịch ghi "Bạn / Đối phương", Ctrl+Alt+R hiện "Xin hãy nói lại" bằng ngôn ngữ của đối phương
- Quy tắc sửa kết quả nhận dạng, chọn chữ trong bản ghi phụ đề để thêm vào bảng thuật ngữ, thông tin buổi phát (tên streamer, tên game, phần mô tả buổi phát)
- Bố cục đọc: cỡ và vị trí bản gốc, độ rộng mỗi dòng, kéo dài thời gian hiển thị theo số chữ, furigana chỉ ghi lần đầu
- Phím tắt mới Ctrl+Alt+P tạm dừng phụ đề, Ctrl+Alt+H xem lại phụ đề gần đây; đặt lại vị trí cửa sổ phụ đề; cảnh báo khi âm thanh nền quá to

## Hạn chế đã biết

- **Chỉ được kiểm thử đầy đủ trên Windows (RTX 5070)**. Việc thu âm trên máy thật macOS và Linux, tốc độ dịch trên Apple Silicon và CPU đều chưa được đo; Linux chỉ được nghiệm thu trong container Docker.
- **Tổ hợp đề xuất mới chỉ được đo đầu cuối trên RTX 5070 (12 GB)**: khi turbo + large-v3 (int8_float16) và qwen3.5:9b chạy đồng thời, toàn bộ card dùng tối đa 10.8 GB, độ trễ phụ đề tức thời khoảng 0.31 giây, từ lúc nói xong đến phụ đề hoàn chỉnh khoảng 0.92 giây (trung vị, xem [`tasks/acceptance-report-v0.2.0.md`](tasks/acceptance-report-v0.2.0.md)).
- **Dữ liệu đánh giá độ chính xác nhận dạng không phải là VTuber**: dùng lời nói thông thường trên YouTube (YODAS), giọng đọc và giọng truyền hình, có trộn thêm BGM; không có dữ liệu buổi hát, tiếng hét hay collab nhiều người. Giọng hát sẽ lọt qua bộ phát hiện giọng nói, có thể sinh ra phụ đề kỳ lạ.
- **Nhãn người nói chỉ được đo trên các đoạn YouTube ghép lại** (2 người gắn nhãn đúng khoảng 92–100%, 3–4 người khoảng 80–85%): không có dữ liệu nói chen, tiếng đệm ngắn dưới 1 giây và một người đổi giọng, nên trong collab thật có thể thấp hơn; không ghi vào tệp văn bản OBS.
- Các ngôn ngữ ngoài tiếng Anh chỉ có benchmark công khai và bài kiểm tra ảo giác trên âm thanh không phải giọng nói, chưa được đo trên giọng nói thật; danh sách cụm từ ảo giác chưa được người bản ngữ rà soát.
- Một số câu chào cuối video (ví dụ "チャンネル登録よろしくお願いします") bị coi là ảo giác của Whisper và bị lọc bỏ cả câu, ngay cả khi streamer thực sự nói.
- Cần cài thêm Ollama mới dịch được; với các máy chủ tương thích OpenAI khác ngoài Ollama, bạn phải tự cài mô hình.
- Kiểm tra áp lực bộ nhớ khi chạy lâu đã được làm trên Windows (session của engine 20 phút giữ ổn định, cửa sổ chính 22 phút +0.7 MiB/phút); chưa kiểm tra trên macOS, Linux.
- Mỗi lúc chỉ có một session phụ đề; sau khi thiết bị bị rút ra sẽ không tự kết nối lại.
- Không hỗ trợ: nhấp vào từ để tra, chỉ thu âm thanh của một ứng dụng, chế độ buổi hát, phân tách khi đổi người nói giữa câu hoặc nhiều người nói cùng lúc, nguồn "Trình duyệt" của OBS (lý do xem mục "Tạm chưa làm" (先不做) trong tài liệu tiếng Trung [`docs/specs/competitive-features.md`](docs/specs/competitive-features.md) và [`docs/adr/`](docs/adr/)).
- Bộ cài không có chữ ký mã trả phí (Windows SmartScreen, macOS Gatekeeper sẽ cảnh báo).

## Giấy phép

kikitori được phát hành theo giấy phép MIT ([LICENSE](LICENSE)). Toàn văn giấy phép của các thành phần bên thứ ba nằm trong thư mục cài đặt, có thể mở từ trang "Giới thiệu".

Các mô hình không được phân phối kèm bộ cài; khi bạn tải về, giấy phép riêng của từng mô hình sẽ được áp dụng:

| Mô hình | Giấy phép | Lưu ý |
|---|---|---|
| Whisper (bản chuyển đổi CTranslate2) | MIT | — |
| Silero VAD (đi kèm chương trình) | MIT | — |
| 3D-Speaker CAM++ (nhãn người nói, tải khi chọn dùng) | Apache-2.0 | — |
| Qwen3.5 (mô hình dịch mặc định) | Apache-2.0 | — |
| Tencent HY-MT1.5 | Tencent HY Community License | **Không áp dụng tại EU, Vương quốc Anh, Hàn Quốc**; kikitori mặc định không đề xuất, cần xác nhận khu vực của bạn ở trang Cài đặt |
| Sakura-GalTransl | CC BY-NC-SA 4.0 | **Cấm sử dụng thương mại**; không được đề xuất, chỉ có thể chọn thủ công |
| NVIDIA cuBLAS | NVIDIA CUDA EULA | Cần đồng ý trước khi tải |

## Dành cho nhà phát triển

- Kiến trúc: [docs/architecture.md](docs/architecture.md); đặc tả từng mô-đun: [docs/specs/](docs/specs/); hướng dẫn học: [docs/learning/](docs/learning/README.md)
- Đánh giá và thử nghiệm: `experiments/*/README.md`; báo cáo khảo sát: `reports/`

```text
uv sync                                       Cài phụ thuộc Python
uv run python -m unittest discover -s tests   Kiểm thử Python
uv run ruff check                             Lint
uv run kikitori                               Menu tương tác (khởi động engine, liệt kê thiết bị âm thanh…)
cd app && npm ci && npm run menu              Menu phát triển của ứng dụng desktop (cần Node và Rust)
cd app && npm test && npx tsc --noEmit        Kiểm thử frontend và kiểm tra kiểu
```
