# PS plug-socket active-perception trial

Repository: `/home/ps/workspace/jiaxin/yam-astra-experiments`.
Use the upright, unplugged, braced strip and the exact task prompt at
`integrations/inspect_robots/experiments/plug-socket/prompt-active-perception.txt`.
This is a fresh 13-control trial: left camera arm plus right manipulation arm.
Left gripper remains locked at its initial opening. All three views are used.

## Preparation already checked

On November 20 the existing PS daemon on ports 6001/6002 was in confirmed
SAFE HOLD with healthy feedback on both followers. The original three-camera
`preview_cameras_ps.py` was running on 8765. A live, read-only
`--active-perception --check-station` passed without activating a session or
dispatching joint commands. This describes that check, not a perpetual health
guarantee. Keep healthy services running; do not replace them with leader teleop
or its two-camera-only configuration.

The initial preparation check reported `motion_ready: false` before base
registration. Registration is now configured from the operator-confirmed 55 cm
spacing, matching forward axes, level mounts and equal height/depth. The spacing
accuracy was confirmed within 1–2 cm. The current guard uses a 0.16 m minimum plus
0.02 m measurement margin (0.18 m nominal threshold). Its provenance is an
operator-attested rail measurement, not an independently surveyed or camera-based
calibration. A subsequent live read-only check passed with `motion_ready: true`
and about 0.556 m nominal current wrist/grasp-point separation. No session was
activated and no joint command was sent by these checks.

## Registered base measurement

Measure the distance between the vertical rotation axes at the bottoms of the
arms, where they attach to the rail. Do not measure gripper separation or the gap
between the outer edges of the mounts.

```text
Left base pivot                         Right base pivot
      o <------ center-to-center ------> o
```

For future mount changes, report the distance in centimetres, plus whether both
mounts are level, at the same height, aligned front-to-back, and face the same
forward direction at J0=0.
Do not move the arms merely to obtain this information. Unknown mounting
directions/offsets must be resolved rather than silently assumed.

Geometry is saved in
`integrations/inspect_robots/experiments/plug-socket/ps-observer-geometry.json`.
Both model bases must have proper 4x4 base-to-common-frame transforms with
translations in metres, measurement provenance, and verified registration.
For confirmed identical parallel mounts at the same height/depth, a frame fixed
to the right model base can use identity for the right base and the measured
leftward translation for the left base. Other layouts need the actual rotations
and translation offsets. No other YAM's geometry may be substituted.

The minimum wrist/gripper-reference separation is 0.16 m plus a 0.02 m
measurement margin, giving an enforced nominal threshold of 0.18 m. This guard
does not cover every elbow, camera, cable, fixture or table surface; keep the
left observer outside the right manipulation corridor. Geometry identity is
pinned for resumes. Do not edit geometry while a trial is running.

## Check and launch after geometry is registered

Use a separate operator terminal. Keep the daemon and original three-view
preview terminals open. If those services have since stopped, follow the cold
startup instructions in [PS_ASTRA_RUNBOOK.md](PS_ASTRA_RUNBOOK.md) first.

```bash
cd /home/ps/workspace/jiaxin/yam-astra-experiments
ATTEMPT=5

if [ -z "${RHOS_API_KEY:-}" ]; then
  read -rsp "RHOS_API_KEY: " RHOS_API_KEY
  echo
  export RHOS_API_KEY
fi

YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --task plug-socket --attempt "$ATTEMPT" --active-perception
```

Require `motion_ready: true`, `metric_separation_verified: true`, 13 controls,
`mask_left_view: false`, and `attempt_already_consumed: false`. Do not use the
right-only `--mask-left-view` flag or a previous right-only resume directory.
If attempt 5 was already consumed, select a new unused number; preserve its data.

Then run the live read-only check:

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --task plug-socket --attempt "$ATTEMPT" --active-perception \
  --check-station --right-channel-confirmed can_follower_r
```

Require `Station observations and daemon checks passed`. Open
<http://127.0.0.1:8765/> and confirm main, left and right labels and live views.
The initial left view may show little of the target until Astra positions the
observer. Check clearance for both arms before starting.

The following command starts the experiment and can move both arms:

```bash
mkdir -p .local/station-logs
set -o pipefail
YAM/gello_software/.venv/bin/python -u integrations/inspect_robots/astra_run.py \
  --run --task plug-socket --attempt "$ATTEMPT" --active-perception \
  --right-channel-confirmed can_follower_r \
  2>&1 | tee ".local/station-logs/astra-plug-socket-observer-$(printf '%03d' "$ATTEMPT")-$(date +%Y%m%d-%H%M%S).log"
```

Fresh runs use Astra 6.2 with medium reasoning, a 60-minute wall-clock budget,
16,200 motion steps and 270 API-attempt budget. Existing joint, tracking,
interpolation, daemon and separation guards remain in force. Command permission
is not a whole-scene collision certificate.

## Stop and results

Ctrl+C in the experiment terminal exits through the supervised hold context.
Wait for exit/hold acknowledgement before handling the scene. Verify separately:

```bash
YAM/gello_software/scripts/safety status --port 6002 --json
```

The task output for attempt 5 is
`integrations/inspect_robots/experiments/plug-socket/runs/run-005/`.
It contains the prompt snapshot, command/attention journal, camera frames,
result/hold status, checkpoint and replay. The source prompt and recordings are
separate; later prompt edits do not rewrite the saved trial prompt.

## Resume trial 6 after its separation-certificate stop

Leave both arms, the held brick and the scene in their stopped positions. Keep
the existing confirmed-hold daemon and camera preview running. The following
check reads feedback and views without acquiring control:

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --check-station --task plug-socket --active-perception \
  --resume integrations/inspect_robots/experiments/plug-socket/runs/run-006 \
  --recover-separation-stop --right-channel-confirmed can_follower_r
```

After it passes, this command can move both arms:

```bash
mkdir -p .local/station-logs
set -o pipefail
YAM/gello_software/.venv/bin/python -u integrations/inspect_robots/astra_run.py \
  --run --task plug-socket --active-perception \
  --resume integrations/inspect_robots/experiments/plug-socket/runs/run-006 \
  --recover-separation-stop --right-channel-confirmed can_follower_r \
  2>&1 | tee ".local/station-logs/astra-plug-socket-006-resume-$(date +%Y%m%d-%H%M%S).log"
```

Use the same exported `RHOS_API_KEY` as for the original trial. Recovery accounts
for step 2028 (including 66 waypoints after checkpoint 1962) and 18 prior model
calls. It gives Astra fresh observations before any new motion; it never
completes the interrupted chunk automatically. The resumed segment has a fresh
60-minute clock and retains the cumulative 16,200-step/270-request budgets.
Output, three-view replay and checkpoint go under `run-006/resume-<timestamp>-<id>/`.
The original trial files remain unchanged. A pose/identity/geometry mismatch
stops preflight; do not override it or restart the arms to force a match.
