#!/usr/bin/env bash

set -uo pipefail

readonly BITRATE=1000000
readonly LEFT_CHANNEL="can_follower_l"
readonly RIGHT_CHANNEL="can_follower_r"

channel="all"
child_pids=()

usage() {
    cat <<'EOF'
Usage: scripts/run_bimanual_gravity_comp.sh [--channel CHANNEL]

Start YAM follower arm(s) in gravity compensation mode.

CHANNEL:
  all              Start both follower arms (default)
  can_follower_l   Start only the left follower arm
  can_follower_r   Start only the right follower arm
EOF
}

die() {
    echo "Error: $*" >&2
    exit 1
}

cleanup() {
    local status=$?
    trap - EXIT INT TERM HUP

    if ((${#child_pids[@]} > 0)); then
        for pid in "${child_pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                kill -TERM "$pid" 2>/dev/null || true
            fi
        done
        for pid in "${child_pids[@]}"; do
            wait "$pid" 2>/dev/null || true
        done
    fi

    exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT TERM HUP

while (($# > 0)); do
    case "$1" in
        --channel)
            (($# >= 2)) || die "--channel requires a value"
            channel=$2
            shift 2
            ;;
        --channel=*)
            channel=${1#*=}
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

case "$channel" in
    all)
        selected_channels=("$LEFT_CHANNEL" "$RIGHT_CHANNEL")
        ;;
    "$LEFT_CHANNEL"|"$RIGHT_CHANNEL")
        selected_channels=("$channel")
        ;;
    *)
        die "unsupported channel '$channel' (expected all, $LEFT_CHANNEL, or $RIGHT_CHANNEL)"
        ;;
esac

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)
robot_script="$repo_root/i2rt/robots/motor_chain_robot.py"
python_bin=${PYTHON_BIN:-python}

command -v ip >/dev/null 2>&1 || die "the 'ip' command is not available"
command -v "$python_bin" >/dev/null 2>&1 || die "Python executable '$python_bin' is not available"
[[ -f "$robot_script" ]] || die "robot entry point not found: $robot_script"

for iface in "${selected_channels[@]}"; do
    ip link show "$iface" >/dev/null 2>&1 || die "CAN interface '$iface' does not exist"
done

interface_is_ready() {
    local iface=$1
    local details

    details=$(ip -details link show "$iface") || return 1
    grep -q "state UP" <<<"$details" && grep -Eq "bitrate $BITRATE([[:space:]]|$)" <<<"$details"
}

configure_interface() {
    local iface=$1
    local -a elevate=()

    if ((EUID != 0)); then
        command -v sudo >/dev/null 2>&1 || die "sudo is required to configure '$iface'"
        elevate=(sudo)
    fi

    echo "Configuring $iface at $BITRATE bit/s..."
    "${elevate[@]}" ip link set "$iface" down || die "failed to bring '$iface' down"
    "${elevate[@]}" ip link set "$iface" type can bitrate "$BITRATE" || die "failed to configure '$iface'"
    "${elevate[@]}" ip link set "$iface" up || die "failed to bring '$iface' up"

    interface_is_ready "$iface" || die "'$iface' is not UP at $BITRATE bit/s after configuration"
}

for iface in "${selected_channels[@]}"; do
    if interface_is_ready "$iface"; then
        echo "$iface is already UP at $BITRATE bit/s."
    else
        configure_interface "$iface"
    fi
done

echo "Selected channel(s): ${selected_channels[*]}"
echo "Safety: support the arm(s) and keep both workspaces clear before continuing."

for iface in "${selected_channels[@]}"; do
    echo "Starting gravity compensation on $iface..."
    "$python_bin" "$robot_script" \
        --channel "$iface" \
        --gripper linear_4310 \
        --operation_mode gravity_comp &
    child_pids+=("$!")
done

echo "Gravity compensation started. Press Ctrl+C to stop."

if ((${#child_pids[@]} == 1)); then
    wait "${child_pids[0]}"
    status=$?
else
    wait -n "${child_pids[@]}"
    status=$?
fi

if ((status != 0)); then
    echo "A gravity compensation process exited with status $status; stopping the remaining process(es)." >&2
fi

exit "$status"
