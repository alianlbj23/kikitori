#!/usr/bin/env bash
# Linux 驗收（Docker）：互動選單。Windows 的 Git Bash 與 Linux 都能用。
# 結果與截圖寫到 results/，本機音軌與下載的安裝檔放 data/（兩者都被 git 忽略）。
# 需要：docker；選項 2 需要已登入的 gh；引擎與 GUI 測試需要主機上的 Ollama（qwen3.5:4b）
# 與已下載的 ASR 模型（KLA_MODELS，預設是本機 kikitori 的模型快取）。
set -uo pipefail  # 不用 -e：某一項失敗時回到選單
export MSYS_NO_PATHCONV=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
# docker 的 bind mount 在 Windows 要用 D:/... 形式的路徑。
host_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
REPO_H="$(host_path "$REPO")"
DATA_H="$(host_path "$HERE/data")"
RESULTS_H="$(host_path "$HERE/results")"
ARTIFACTS="$HERE/data/artifacts"
if [ -n "${LOCALAPPDATA:-}" ]; then
  DEFAULT_MODELS="$(host_path "$LOCALAPPDATA")/kikitori/models"
else
  DEFAULT_MODELS="${XDG_CACHE_HOME:-$HOME/.cache}/kikitori/models"
fi
MODELS="${KLA_MODELS:-$DEFAULT_MODELS}"
AUDIO_IMG=kikitori-la-audio:local
GUI_IMG=kikitori-la-gui:local
CONTAINERS=(kla-audio kla-pw kla-gui)

mkdir -p "$HERE/data" "$HERE/results"

need_track() {
  if [ ! -f "$HERE/data/anime.wav" ]; then
    local src="$REPO/experiments/pipecat_vad/data/tracks/anime.wav"
    [ -f "$src" ] || { echo "缺少 $src（先跑 experiments/pipecat_vad 的 build_tracks.py）"; return 1; }
    cp "$src" "$HERE/data/"
  fi
  if [ ! -f "$HERE/data/anime_006.mp3" ]; then
    cp "$REPO/experiments/pipecat_vad/data/raw/anime_006.mp3" "$HERE/data/"
  fi
}

need_artifacts() {
  ls "$ARTIFACTS"/*.deb >/dev/null 2>&1 || { echo "先執行選項 2 下載安裝檔"; return 1; }
  DEB="$(ls "$ARTIFACTS"/*.deb | head -1)"
  APPIMAGE="$(ls "$ARTIFACTS"/*.AppImage | head -1)"
}

build_images() {
  docker build -f "$HERE/docker/audio.Dockerfile" -t "$AUDIO_IMG" "$HERE/docker"
  docker build -f "$HERE/docker/gui.Dockerfile" -t "$GUI_IMG" "$HERE/docker"
}

download_artifacts() {
  local run_id
  run_id="$(cd "$REPO" && gh run list --workflow release.yml --status success --limit 20 \
    --json databaseId,displayTitle -q '[.[] | select(.displayTitle | contains("[release-dry-run]"))][0].databaseId')"
  [ -n "$run_id" ] || { echo "找不到成功的 release 演練"; return 1; }
  echo "release run $run_id"
  rm -rf "$ARTIFACTS" && mkdir -p "$ARTIFACTS"
  (cd "$REPO" && gh run download "$run_id" -n bundle-linux -D "$ARTIFACTS")
  ls -la "$ARTIFACTS"
}

# 啟動（或沿用）擷取／引擎用的容器；$1 名稱，$2 = pulseaudio | pipewire
audio_container() {
  local name="$1" server="$2"
  if ! docker ps --format '{{.Names}}' | grep -qx "$name"; then
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run -d --name "$name" --add-host host.docker.internal:host-gateway \
      -e KLA_SERVER="$server" -e XDG_RUNTIME_DIR=/tmp/xdg \
      -v "$REPO_H:/src:ro" -v "$DATA_H:/data" -v "$RESULTS_H:/results" \
      -v "$MODELS:/models:ro" "$AUDIO_IMG" >/dev/null
    docker exec "$name" bash -lc 'mkdir -m 700 -p /tmp/xdg && cd /src && uv sync --locked -q'
  fi
}

in_audio() {  # $1 容器、$2 指令（在 /src/experiments/linux_acceptance、session D-Bus 內執行）
  docker exec "$1" bash -lc "cd /src/experiments/linux_acceptance && dbus-run-session -- bash -c '$2'"
}

capture_check() {
  need_track
  local name=kla-audio
  [ "$1" = pipewire ] && name=kla-pw
  audio_container "$name" "$1"
  in_audio "$name" "/opt/venv/bin/python scripts/capture_probe.py --all"
}

engine_e2e() {
  need_track
  audio_container kla-audio pulseaudio
  if [ "$1" = frozen ]; then
    need_artifacts
    docker cp "$DEB" kla-audio:/tmp/k.deb
    docker exec kla-audio bash -lc 'rm -rf /tmp/deb && dpkg-deb -x /tmp/k.deb /tmp/deb'
    in_audio kla-audio "KLA_ENGINE=/tmp/deb/usr/lib/kikitori/kikitori-engine/kikitori-engine KLA_RESULTS=/results/frozen /opt/venv/bin/python scripts/engine_e2e.py --all"
  else
    in_audio kla-audio "/opt/venv/bin/python scripts/engine_e2e.py --all"
  fi
}

installer_check() {
  need_artifacts
  docker run --rm -v "$(host_path "$ARTIFACTS"):/artifacts:ro" -v "$REPO_H:/src:ro" ubuntu:24.04 bash -c '
    set -e
    apt-get update -qq
    cp /artifacts/*.deb /tmp/k.deb
    echo "== deb 控制資訊"; dpkg-deb -f /tmp/k.deb Package Version Depends Recommends
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /tmp/k.deb python3 >/dev/null
    echo "== 執行權限"; ls -l /usr/bin/kikitori-app /usr/lib/kikitori/kikitori-engine/kikitori-engine
    echo "== deb：smoke_test --self-check"
    (cd /tmp && python3 -I /src/packaging/engine/smoke_test.py --binary /usr/lib/kikitori/kikitori-engine/kikitori-engine --self-check)
    python3 -I /src/packaging/app/licenses.py --check-bundle /usr/lib/kikitori
    cp /artifacts/*.AppImage /tmp/k.AppImage && chmod +x /tmp/k.AppImage
    echo "== AppImage 直接執行（容器沒有 /dev/fuse）"; /tmp/k.AppImage --appimage-version || true
    (/tmp/k.AppImage || true) 2>&1 | head -3
    (cd /tmp && /tmp/k.AppImage --appimage-extract >/dev/null)
    echo "== AppImage（解開）：smoke_test --self-check"
    (cd /tmp && python3 -I /src/packaging/engine/smoke_test.py --binary /tmp/squashfs-root/usr/lib/kikitori/kikitori-engine/kikitori-engine --self-check)
    python3 -I /src/packaging/app/licenses.py --check-bundle /tmp/squashfs-root/usr/lib/kikitori'
  echo "== AppImage 與 FUSE（一般使用者、有 /dev/fuse）"
  docker run --rm --device /dev/fuse --cap-add SYS_ADMIN --security-opt apparmor:unconfined \
    -v "$(host_path "$ARTIFACTS"):/artifacts:ro" ubuntu:24.04 bash -c '
    cp /artifacts/*.AppImage /tmp/k.AppImage && chmod 755 /tmp/k.AppImage && useradd -m u
    echo "-- 沒有 fusermount、沒有 libfuse2"; su u -c "timeout 5 /tmp/k.AppImage --appimage-mount" 2>&1 | head -2 || true
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fuse3 >/dev/null
    echo "-- 只裝 fuse3（沒有 libfuse2）：印出掛載點代表成功"; su u -c "timeout 5 /tmp/k.AppImage --appimage-mount" || true'
}

gui_container() {
  need_artifacts
  if ! docker ps --format '{{.Names}}' | grep -qx kla-gui; then
    docker rm -f kla-gui >/dev/null 2>&1 || true
    docker run -d --name kla-gui --add-host host.docker.internal:host-gateway --shm-size 512m \
      -v "$(host_path "$ARTIFACTS"):/artifacts:ro" -v "$REPO_H:/src:ro" -v "$DATA_H:/data:ro" \
      -v "$RESULTS_H:/results" -v "$MODELS:/models:ro" "$GUI_IMG" >/dev/null
    docker cp "$DEB" kla-gui:/tmp/k.deb
    docker cp "$APPIMAGE" kla-gui:/home/tester/k.AppImage
    docker exec kla-gui bash -lc 'apt-get update -qq && apt-get install -y -qq /tmp/k.deb >/dev/null \
      && chown tester /home/tester/k.AppImage && chmod +x /home/tester/k.AppImage'
    if [ "${KLA_CJK:-1}" = 1 ]; then
      docker exec kla-gui bash -lc 'apt-get install -y -qq --no-install-recommends fonts-noto-cjk >/dev/null'
    fi
  fi
  docker exec kla-gui bash -lc 'pkill -f kikitori-app || true; pkill -f kikitori-engine || true'
}

gui() {  # $1 = deb | appimage | compositor
  need_track
  gui_container
  local env="KLA_RESULTS=/results/gui-$1"
  case "$1" in
    appimage) env="$env APPIMAGE_EXTRACT_AND_RUN=1 KLA_APP=/home/tester/k.AppImage" ;;
    compositor) env="$env KLA_COMPOSITOR=1" ;;
  esac
  docker exec -u tester kla-gui bash -lc "mkdir -p /results/gui-$1 && cd /tmp && env $env timeout 600 dbus-launch --exit-with-session python3 -I /src/experiments/linux_acceptance/scripts/gui_webdriver.py"
}

unit_tests() {
  audio_container kla-audio pulseaudio
  # bind mount 在 Windows 上所有檔案都有執行位元（ruff EXE002）、而且唯讀；改用乾淨的 clone。
  docker exec kla-audio bash -lc 'git config --global --add safe.directory /src/.git
    git config --global --add safe.directory /src
    rm -rf /tmp/work && git clone -q /src /tmp/work && cd /tmp/work && git log --oneline -1
    export UV_PROJECT_ENVIRONMENT=/tmp/work/.venv
    uv sync --locked -q && uv run --locked python -m unittest discover -s tests && uv run --locked ruff check'
}

cleanup() {
  for c in "${CONTAINERS[@]}"; do docker rm -f "$c" >/dev/null 2>&1 && echo "removed $c" || true; done
}

options=(
  "建置映像（audio、gui）"
  "下載最新 release 演練的 Linux 安裝檔（gh）"
  "擷取檢查：PulseAudio"
  "擷取檢查：PipeWire（pipewire-pulse + WirePlumber）"
  "引擎端到端：原始碼的引擎"
  "引擎端到端：deb 內打包的引擎"
  "安裝檔檢查：deb、AppImage、FUSE"
  "GUI：deb（Xvfb + WebDriver，真的引擎）"
  "GUI：AppImage（extract-and-run）"
  "GUI：有合成器（picom）＋全域快捷鍵"
  "Linux 單元測試與 ruff（乾淨 clone）"
  "移除本工具建立的容器"
  "離開"
)
PS3="請輸入編號："
select choice in "${options[@]}"; do
  case "$REPLY" in
    1) build_images ;;
    2) download_artifacts ;;
    3) capture_check pulseaudio ;;
    4) capture_check pipewire ;;
    5) engine_e2e source ;;
    6) engine_e2e frozen ;;
    7) installer_check ;;
    8) gui deb ;;
    9) gui appimage ;;
    10) gui compositor ;;
    11) unit_tests ;;
    12) cleanup ;;
    13) break ;;
    *) echo "無效的選項：$REPLY" ;;
  esac
done
