#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

CONFIG_PATH="config/accounts.example.yaml"
if [[ -f "config/accounts.yaml" ]]; then
  CONFIG_PATH="config/accounts.yaml"
fi

python3 src/pipeline.py --config "$CONFIG_PATH" --data-root data
