# Linux 擷取與引擎端到端驗收用的映像：PulseAudio（null sink）+ uv 管理的 Python 3.12。
# PipeWire（pipewire-pulse + WirePlumber）也裝好，啟動哪一個由 capture_probe 的 KLA_SERVER 決定。
# repo 以 bind mount 掛在 /src（唯讀），虛擬環境放在容器內 /opt/venv，不碰 Windows 的 .venv。
FROM ghcr.io/astral-sh/uv:0.13.4 AS uv

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    TZ=UTC
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      pulseaudio pulseaudio-utils pipewire pipewire-pulse wireplumber dbus \
      ffmpeg ca-certificates curl procps git \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /usr/local/bin/
# PulseAudio 以一般使用者身分跑（root 會進 system mode）；HOME 給 XDG 快取用。
RUN useradd -m -s /bin/bash tester && mkdir -p /opt/venv /opt/uv-python \
 && chown tester:tester /opt/venv /opt/uv-python
USER tester
WORKDIR /src
CMD ["sleep", "infinity"]
