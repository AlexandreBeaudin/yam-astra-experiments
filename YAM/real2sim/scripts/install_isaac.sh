#!/usr/bin/env bash
set -euo pipefail
REAL2SIM_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REAL2SIM_UV="${REAL2SIM_UV:-$(command -v uv || true)}"
REAL2SIM_UV="${REAL2SIM_UV:-$REAL2SIM_ROOT/.venv/bin/uv}"
if [[ ! -x "$REAL2SIM_UV" ]]; then
  echo 'Install uv in the utility environment first: .venv/bin/python -m pip install uv' >&2
  exit 1
fi
if [[ ! -x "$REAL2SIM_ROOT/.runtime/isaacsim-5.1/bin/python" ]]; then
  bash "$REAL2SIM_ROOT/scripts/no_proxy.sh" "$REAL2SIM_UV" venv --python 3.11 "$REAL2SIM_ROOT/.runtime/isaacsim-5.1"
fi
bash "$REAL2SIM_ROOT/scripts/no_proxy.sh" "$REAL2SIM_UV" pip install \
  --python "$REAL2SIM_ROOT/.runtime/isaacsim-5.1/bin/python" \
  --index-strategy unsafe-best-match --extra-index-url https://pypi.nvidia.com \
  -r "$REAL2SIM_ROOT/requirements-isaacsim.txt" \
  -c "$REAL2SIM_ROOT/requirements-isaacsim-linux-x86_64.lock"
