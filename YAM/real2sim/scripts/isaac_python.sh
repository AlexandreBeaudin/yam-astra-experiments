#!/usr/bin/env bash
set -euo pipefail
REAL2SIM_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REAL2SIM_ICD="${REAL2SIM_VULKAN_ICD:-/usr/share/vulkan/icd.d/nvidia_icd.json}"
REAL2SIM_PYTHON="$REAL2SIM_ROOT/.runtime/isaacsim-5.1/bin/python"
if [[ ! -x "$REAL2SIM_PYTHON" ]]; then
  echo "Missing Isaac Sim 5.1 runtime. Run scripts/install_isaac.sh; see docs/ISAACSIM_5_1_MIGRATION.md." >&2
  exit 1
fi
export PYTHONNOUSERSITE=1
# Do not inherit a collector/ROS/older Isaac environment's Python imports.
unset PYTHONPATH PYTHONHOME
export PYTHONPATH="$REAL2SIM_ROOT/src"
"$REAL2SIM_PYTHON" -c 'from real2sim.simulation_runtime import runtime_info; runtime_info()'
exec bash "$REAL2SIM_ROOT/scripts/no_proxy.sh" env OMNI_KIT_ACCEPT_EULA=YES \
  VK_ICD_FILENAMES="$REAL2SIM_ICD" VK_DRIVER_FILES="$REAL2SIM_ICD" \
  "$REAL2SIM_PYTHON" "$@"
