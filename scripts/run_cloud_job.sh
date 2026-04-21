#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

bash scripts/bootstrap_linux.sh

CONFIG_PATH="config/accounts.example.yaml"
if [[ -f "config/accounts.yaml" ]]; then
  CONFIG_PATH="config/accounts.yaml"
fi

python3 src/pipeline.py --config "$CONFIG_PATH" --data-root data
