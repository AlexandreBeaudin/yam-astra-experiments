#!/usr/bin/env bash
# Shared by repository wrappers; installed `yam` uses its installation environment.
yam_run() {
    local entry="$1"
    shift
    local repo_dir python_bin
    local language="en" next_language=false argument
    if [[ "$entry" == "run_bimanual_teleop.py" ]]; then
        language="zh"
        for argument in "$@"; do
            if $next_language; then
                language="$argument"
                next_language=false
            elif [[ "$argument" == "--lang" ]]; then
                next_language=true
            elif [[ "$argument" == --lang=* ]]; then
                language="${argument#--lang=}"
            fi
        done
    fi
    repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    if [[ -e "$repo_dir/.venv" || -L "$repo_dir/.venv" ]]; then
        python_bin="$repo_dir/.venv/bin/python"
    elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
        python_bin="$VIRTUAL_ENV/bin/python"
    else
        python_bin=""
    fi
    if [[ -z "$python_bin" || ! -x "$python_bin" ]]; then
        if [[ "$language" == "zh" ]]; then
            echo "YAM 环境缺失或从其他位置复制后失效：$repo_dir/.venv" >&2
            echo "请先移走旧 .venv，再执行：cd '$repo_dir' && uv sync --frozen --extra yam --extra realsense" >&2
        else
            echo "YAM environment missing or copied from another location: $repo_dir/.venv" >&2
            echo "Move the old .venv aside, then run: cd '$repo_dir' && uv sync --frozen --extra yam --extra realsense" >&2
        fi
        return 2
    fi
    # Both source roots precede inherited paths, including paths from old checkouts.
    export PYTHONPATH="$repo_dir:$repo_dir/../i2rt${PYTHONPATH:+:$PYTHONPATH}"
    if ! "$python_bin" - "$entry" "$language" <<'PY'
import importlib
import sys

if sys.version_info[:2] != (3, 11):
    message = "YAM 需要 Python 3.11，当前版本：" if sys.argv[2] == "zh" else "YAM requires Python 3.11; found "
    print(message + sys.version.split()[0], file=sys.stderr)
    sys.exit(2)
modules = ["yaml", "numpy", "h5py", "zmq", "rich", "PIL"]
if sys.argv[1] in {"run_bimanual_teleop.py", "replay.py", "return_zero.py"}:
    modules += ["dynamixel_sdk", "i2rt", "can", "omegaconf", "mujoco"]
try:
    for module in modules:
        importlib.import_module(module)
except (ImportError, OSError) as exc:
    message = "YAM 环境依赖不完整：" if sys.argv[2] == "zh" else "YAM environment is incomplete: "
    print(f"{message}{exc}", file=sys.stderr)
    sys.exit(2)
PY
    then
        if [[ "$language" == "zh" ]]; then
            echo "请重建环境：cd '$repo_dir' && uv sync --frozen --extra yam --extra realsense" >&2
        else
            echo "Rebuild with: cd '$repo_dir' && uv sync --frozen --extra yam --extra realsense" >&2
        fi
        return 2
    fi
    exec "$python_bin" "$repo_dir/scripts/$entry" "$@"
}
