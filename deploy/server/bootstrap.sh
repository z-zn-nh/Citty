#!/usr/bin/env bash
# Citty remote inference server bootstrap.
# Target: Ubuntu 22.04 / 24.04, with or without an NVIDIA GPU.
#
# What it does:
#   1. installs build tools + python venv
#   2. builds llama.cpp  -> serves the MT model (OpenAI compatible /v1/chat/completions)
#   3. sets up a python venv with funasr -> serves SenseVoice (OpenAI compatible /v1/audio/transcriptions)
#   4. installs two systemd units, both bound to 127.0.0.1 (access via SSH tunnel only)
#
# Usage:
#   sudo ./bootstrap.sh                 # full install (compiles llama.cpp, downloads MT model)
#   sudo ./bootstrap.sh --cpu           # CPU only (skip CUDA build)
#   sudo ./bootstrap.sh --mt-only       # skip ASR venv (use a cloud ASR instead)
#   sudo ./bootstrap.sh --no-models     # install runtimes only, do not download weights
#
# All placeholders can be overridden from deploy/server/.env (see .env.example).

set -euo pipefail

ROOT="${CITTY_ROOT:-/opt/citty-server}"
MT_PORT="${CITTY_MT_PORT:-8002}"
ASR_PORT="${CITTY_ASR_PORT:-8001}"
MT_REPO="${CITTY_MT_REPO:-tencent/Hy-MT2-1.8B-GGUF}"
MT_FILE="${CITTY_MT_FILE:-Hy-MT2-1.8B-Q6_K.gguf}"
MT_CTX="${CITTY_MT_CTX:-4096}"
HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
CITTY_MT_KEY="${CITTY_MT_KEY:-change-me-mt-key}"
CITTY_ASR_KEY="${CITTY_ASR_KEY:-change-me-asr-key}"

USE_CUDA=1
DO_MODELS=1
DO_ASR=1
for arg in "$@"; do
  case "$arg" in
    --cpu)       USE_CUDA=0 ;;
    --no-models) DO_MODELS=0 ;;
    --mt-only)   DO_ASR=0 ;;
    *) echo "unknown arg: $arg"; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  # shellcheck disable=SC1091
  set -a; . "$SCRIPT_DIR/.env"; set +a
fi

log() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

log "apt packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq build-essential cmake git curl python3-venv python3-pip ffmpeg >/dev/null

mkdir -p "$ROOT"/{models,bin,log}
cd "$ROOT"

# ---------------------------------------------------------------- llama.cpp
log "build llama.cpp (CUDA=$USE_CUDA)"
if [[ ! -d "$ROOT/llama.cpp/.git" ]]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp "$ROOT/llama.cpp"
fi
cd "$ROOT/llama.cpp"
if [[ "$USE_CUDA" == "1" ]]; then
  cmake -B build -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=ON >/dev/null
else
  cmake -B build -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=ON >/dev/null
fi
cmake --build build --config Release -j"$(nproc)" --target llama-server
cp -f build/bin/llama-server "$ROOT/bin/llama-server"
ln -sf "$ROOT/bin/llama-server" /usr/local/bin/llama-server

# ---------------------------------------------------------------- MT weights
if [[ "$DO_MODELS" == "1" ]]; then
  log "download MT weights: $MT_REPO/$MT_FILE"
  if [[ ! -s "$ROOT/models/$MT_FILE" ]]; then
    pip install -q --break-system-packages "huggingface_hub[cli]" 2>/dev/null || pip install -q "huggingface_hub[cli]"
    HF_ENDPOINT="$HF_ENDPOINT" huggingface-cli download "$MT_REPO" "$MT_FILE" \
      --local-dir "$ROOT/models" --local-dir-use-symlinks False
  fi
  # ModelScope alternative (native in China):
  #   pip install modelscope && modelscope download --model "$MT_REPO" "$MT_FILE" --local_dir "$ROOT/models"
fi

# ---------------------------------------------------------------- ASR (SenseVoice)
if [[ "$DO_ASR" == "1" ]]; then
  log "create ASR venv + funasr (this pulls torch, can be a few GB)"
  [[ -d "$ROOT/venv-asr" ]] || python3 -m venv "$ROOT/venv-asr"
  "$ROOT/venv-asr/bin/pip" install -q --upgrade pip
  # torch first: pick the CUDA wheel matching your driver, or plain CPU wheel.
  if [[ "$USE_CUDA" == "1" ]]; then
    "$ROOT/venv-asr/bin/pip" install -q torch torchaudio --index-url https://download.pytorch.org/whl/cu126
  else
    "$ROOT/venv-asr/bin/pip" install -q torch torchaudio --index-url https://download.pytorch.org/whl/cpu
  fi
  "$ROOT/venv-asr/bin/pip" install -q fastapi uvicorn python-multipart funasr modelscope
fi

# ---------------------------------------------------------------- systemd
log "install systemd units"

cat >/etc/systemd/system/citty-mt.service <<EOF
[Unit]
Description=Citty MT (llama.cpp OpenAI-compatible)
After=network-online.target

[Service]
ExecStart=$ROOT/bin/llama-server -m $ROOT/models/$MT_FILE \\
  --host 127.0.0.1 --port $MT_PORT -c $MT_CTX -ngl 99 -np 4 --api-key $CITTY_MT_KEY
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

if [[ "$DO_ASR" == "1" ]]; then
cat >/etc/systemd/system/citty-asr.service <<EOF
[Unit]
Description=Citty ASR (FunASR SenseVoice, OpenAI-compatible)
After=network-online.target

[Service]
Environment=CITTY_ASR_KEY=$CITTY_ASR_KEY
ExecStart=$ROOT/venv-asr/bin/python $SCRIPT_DIR/asr_openai_server.py \\
  --host 127.0.0.1 --port $ASR_PORT --device auto
WorkingDirectory=$SCRIPT_DIR
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
fi

systemctl daemon-reload
systemctl enable --now citty-mt.service
[[ "$DO_ASR" == "1" ]] && systemctl enable --now citty-asr.service

cat <<EOF

============================================================
 done.  两个服务都**只监听 127.0.0.1**，公网看不到它们。

 检查：
   systemctl status citty-mt  --no-pager
   curl -s http://127.0.0.1:$MT_PORT/v1/models -H "Authorization: Bearer $CITTY_MT_KEY"
   curl -s http://127.0.0.1:$ASR_PORT/v1/models -H "Authorization: Bearer $CITTY_ASR_KEY"
   ss -ltnp | grep -E ':(8001|8002)'

 本地（Windows）建隧道，然后在 D:\\Citty 用远端配置跑：
   ssh -N -L $ASR_PORT:127.0.0.1:$ASR_PORT -L $MT_PORT:127.0.0.1:$MT_PORT root@THIS_SERVER_IP
   .\\run.ps1 -Config D:\\Citty\\config.remote.yaml file out\\samples\\official_en.wav 30

 别忘了把 config.remote.yaml / .env 里的 key 换成上面这两个：
   CITTY_MT_KEY=$CITTY_MT_KEY
   CITTY_ASR_KEY=$CITTY_ASR_KEY
============================================================
EOF
