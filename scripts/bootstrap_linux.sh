#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

log() {
  echo "[bootstrap] $*"
}

SUDO=""
if [[ "${EUID:-$(id -u)}" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
fi

install_system_deps() {
  if command -v apt-get >/dev/null 2>&1; then
    log "Using apt-get to install system dependencies"
    $SUDO apt-get update
    $SUDO apt-get install -y ffmpeg python3 python3-pip python3-venv
    return
  fi

  if command -v dnf >/dev/null 2>&1; then
    log "Using dnf to install system dependencies"
    $SUDO dnf install -y ffmpeg python3 python3-pip
    return
  fi

  if command -v yum >/dev/null 2>&1; then
    log "Using yum to install system dependencies"
    $SUDO yum install -y ffmpeg python3 python3-pip
    return
  fi

  if command -v apk >/dev/null 2>&1; then
    log "Using apk to install system dependencies"
    $SUDO apk add --no-cache ffmpeg python3 py3-pip
    return
  fi

  log "No supported package manager detected; skip system dependencies"
}

if [[ "${SKIP_SYSTEM_DEPS:-0}" != "1" ]]; then
  install_system_deps
else
  log "SKIP_SYSTEM_DEPS=1, skip system package installation"
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "[bootstrap][error] python3 not found"
  exit 1
fi

log "Installing Python dependencies from requirements.txt"
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[bootstrap][error] ffmpeg not found after bootstrap"
  exit 1
fi

log "Environment ready: ffmpeg=$(ffmpeg -version | head -n 1)"
log "Environment ready: yt-dlp=$(python3 -m yt_dlp --version 2>/dev/null || true)"
log "Bootstrap completed"
