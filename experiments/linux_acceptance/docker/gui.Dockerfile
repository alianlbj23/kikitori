# GUI 驗收用的映像：Xvfb + WebKitWebDriver（webkit2gtk-driver）+ tauri-driver。
# 安裝檔（deb）與 CJK 字型在容器啟動後由 run.sh 以 apt 安裝，所以同一個映像可以測不同版本的 deb，
# 也能先看沒有 CJK 字型時的畫面。picom 用來比較有／沒有合成器時的透明視窗，xdotool 送全域快捷鍵。
FROM rust:1.90-bookworm AS driver
# tauri-driver 只有原始碼發行；固定版本並用 --locked 照它的 Cargo.lock 建置。
RUN cargo install tauri-driver --version 2.0.6 --locked --root /out

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUTF8=1 PYTHONIOENCODING=utf-8 TZ=UTC
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      xvfb xauth x11-utils imagemagick webkit2gtk-driver picom xdotool \
      pulseaudio pulseaudio-utils python3 ca-certificates curl procps dbus-x11 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=driver /out/bin/tauri-driver /usr/local/bin/tauri-driver
RUN useradd -m -s /bin/bash tester
CMD ["sleep", "infinity"]
