# Astra pick-and-place startup guide

Reconstructed November 12, 2026 from the saved sessions and current source.

## Where things stand

The exploratory runner is ready, but live station readiness remains unverified.
The previous station check timed out contacting the safety daemon on localhost:6002,
before reaching the cameras. A later launch also lacked the API key or required
arm-confirmation argument.

Offline preflight currently passes. The attempt directory is absent:
`integrations/inspect_robots/astra-yam3-run-001/`.

Use `integrations/inspect_robots/astra_run.py`. Older Trial 004/005 mock workflows
are historical. This exploratory experiment does not require a grasp demonstration
or verified camera calibration.

Task: use the right arm to place the pink block fully inside the white paper
marked X, release it, and withdraw the gripper.

## 1. Work on the NUC

Run commands in Bash on `rhos-nuc-1`:

```bash
cd /home/rhos/workspace/jiaxin/shurui
hostname
```

Clear the workspace, arrange the block and paper in view, and keep the physical
stop accessible. Physically confirm the right follower is `can_yam_a`.
The configured left follower is `can_yam_b`.

## 2. Run offline preflight

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python -u integrations/inspect_robots/astra_run.py
```

This starts no services and makes no robot/API calls.

Expect `attempt_already_consumed: false`. A missing credential is okay at this
stage. If the attempt is consumed, preserve and review its output before
planning another episode.

## 3. Check the station services

```bash
pgrep -af '[y]am_safety_daemon'
ss -ltnp 'sport = :6002'
ss -ltnp 'sport = :6001'
YAM/gello_software/scripts/safety status --json
```

Require `state: safe_hold` and `hold_confirmed: true`.

If a daemon exists but does not respond, resolve that condition before proceeding.
Do not start another CAN owner or kill an existing daemon while the arms depend
on it for support. End active teleoperation through its normal hold/exit procedure.

### Cold start: only if no hardware owner is running

Prefer the established station startup procedure. The following existing entry
point was checked against source, but was not live-tested for this guide.

It initializes/enables BOTH followers and can initialize/calibrate the grippers.
Support the arms during startup and keep clear of the grippers.

In a dedicated terminal, from the workspace root:

```fish
env PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$PWD/YAM/gello_software:$PWD/YAM/i2rt" \
    YAM/gello_software/.venv/bin/python -u -m experiments.yam_safety_daemon \
    --left-config-path /home/rhos/.config/yam/setup/version-0218d55f1295b4c9/left.yaml \
    --right-config-path /home/rhos/.config/yam/setup/version-0218d55f1295b4c9/right.yaml \
    --hardware-port 6001 --safety-port 6002
```

Leave this terminal open. Wait for `Safety daemon ready: ... state=SAFE_HOLD`,
then verify `scripts/safety status --json` from another terminal.

## 4. Keep camera preview running

Astra needs preview on `127.0.0.1:8765`, with live `main` and `right` images.
It does not start cameras.

Important startup trap: `scripts/teleop --check` checks and exits; it does not
start the persistent services. Exiting normal teleoperation also closes its
camera preview.

Reuse an existing independent preview if healthy. Otherwise, with no other
camera owner running, this camera-only recipe uses the existing camera classes.
It was source-checked but not live-tested for this guide.

Run in a separate terminal from the workspace root:

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD/YAM/gello_software${PYTHONPATH:+:$PYTHONPATH}" \
YAM/gello_software/.venv/bin/python -u - <<'PY'
import time
from pathlib import Path
import yaml
from gello.cameras.realsense_rig import CameraRig, load_camera_rig_config
from gello.cameras.preview import CameraPreview

profile = yaml.safe_load(Path('profiles/shurui-mcap.yaml').read_text())
config = load_camera_rig_config(Path(profile['cameras']['config']))
rig = CameraRig(config, depth_enabled=False)
preview = None
try:
    rig.start()
    preview = CameraPreview(rig, port=8765, lang='en')
    preview.start()
    print('Camera preview: http://127.0.0.1:8765/', flush=True)
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    if preview is not None:
        preview.close()
    rig.close()
PY
```

But actually, you can paste the Python code above into its own .py file and then run it. Once again, use fish instead cuz bash is having issues.

```fish
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=(string join : "$PWD/YAM/gello_software" $PYTHONPATH) YAM/gello_software/.venv/bin/python -u preview_cameras.py
```


Leave it running throughout the experiment. Open `http://127.0.0.1:8765/`
in a browser on the NUC and confirm both views update when the scene changes.

For a browser on another computer, forward the port first:

```bash
ssh -N -L 8765:127.0.0.1:8765 rhos@rhos-nuc-1
```

Use your normal station SSH address if different.

## 5. Pass the read-only station check

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python -u integrations/inspect_robots/astra_run.py --check-station
```

Require the explicit final message:

`Station observations and daemon checks passed. No calibration prerequisite.`

The opening JSON alone is not a pass. Its `mode: offline_preflight` label
also appears before station operations.

This check verifies daemon identity/protocol, channels/watchdogs, confirmed
hold, measured state and preview observations. It does not test Astra's API
or certify collision clearance. Preview JPEGs lack exposure timestamps and
frame IDs, so visually confirm that the views are live.

## 6. Enter the API key

In the SAME terminal you will use to launch Astra:

```bash
read -r -s -p 'RHOS API key: ' RHOS_API_KEY
echo
export RHOS_API_KEY
if [ -n "${RHOS_API_KEY:-}" ]; then echo 'Key is set'; else echo 'Key is missing'; fi
```

```fish
read -s -P 'RHOS API key: ' RHOS_API_KEY
set -gx RHOS_API_KEY "$RHOS_API_KEY"
if test -n "$RHOS_API_KEY"
    echo 'Key is set'
else
    echo 'Key is missing'
end
```


Paste the key at the hidden prompt and press Enter. Do not print it or put it
in chat/logs. A new terminal needs its own exported environment.

This runner requires `RHOS_API_KEY`; `OPENAI_API_KEY` alone does not satisfy it.
Bash `set RHOS_API_KEY ...` does not export the required variable.

## 7. Start one attended episode

After the station check passes and you physically confirm the right arm:

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python -u integrations/inspect_robots/astra_run.py --run --right-channel-confirmed can_yam_a
```

THIS CALLS ASTRA AND CAN MOVE HARDWARE.

It uses `gpt-6-astra`, medium effort, through the RHOS Responses gateway.
Live tool-call compatibility of this prepared runner remains unverified.

The runner exposes the right joints/gripper and holds the left target at its
initial readback. Its reset does not home, park or calibrate.

Limits: one episode, up to 60 model calls, zero HTTP retries, 1800 control steps,
and a nominal 900-second deadline. An in-flight HTTP request may take up to
120 seconds; no subsequent motion is allowed beyond the episode deadline.
The printed `max_retries: 1` means one attempt per request.

Moves are bounded and interpolated, but there is no calibrated table/collision
model. Remain present and monitor the physical experiment throughout.

## 8. Stop and confirm hold

For unexpected motion, use the physical stop immediately.

Software hold, from another terminal:

```bash
/home/rhos/workspace/jiaxin/shurui/YAM/gello_software/scripts/safety hold
/home/rhos/workspace/jiaxin/shurui/YAM/gello_software/scripts/safety status --json
```

Require `safe_hold` and `hold_confirmed: true` after stopping and after normal
completion. When aborting, also interrupt the Astra process and recheck hold;
a hold request alone does not establish that the model process has ended.

Inspect updated post-release images: the entire block footprint must be inside
the paper boundary, with the gripper withdrawn. Model “done” or framework
completion alone does not establish physical success.

## 9. Review and preserve the output

Actual output location:

`/home/rhos/workspace/jiaxin/shurui/integrations/inspect_robots/astra-yam3-run-001/`

Look for:

- `attempt.json` and `prompt.txt`
- `attempt-result.json`, including `final_hold_confirmed`
- `station-events.jsonl`
- `framework-log.json` and wire/frame/action/record sidecars

### Watch the replay

Step 7 now automatically records both preview views throughout the episode,
including model waits (target 5 samples/s per view, plus policy observations).
After the motion session exits and requests SAFE HOLD, it builds:

`integrations/inspect_robots/astra-yam3-run-001/replay.html`

Open that file in your browser on the NUC. Press Play, scrub the timeline, or
change playback speed. Keep the `replay-frames/` directory alongside the HTML;
copy the entire run directory if watching on another computer. The page shows
both camera views, sample ages, station observations, command dispatch/acceptance,
returned Astra responses/tool calls, and the final hold result.

This is sampled preview playback, not full-rate synchronized camera video.
Actual receive timing and gaps are preserved; exposure times remain unknown.
Physical success still requires reviewing the images.

Camera frames and station events are written incrementally, with synchronous
file flush/sync. Dispatch is saved before each command; acceptance afterward.
A recording failure stops further actions at the next adapter check. Ctrl+C
attempts normal hold/cleanup and replay export. A hard kill cannot run cleanup;
recover the saved prefix offline without another API call or robot connection:

```fish
YAM/gello_software/.venv/bin/python integrations/inspect_robots/hardware_replay.py --run-dir integrations/inspect_robots/astra-yam3-run-001
```

The recovery exporter does not require `framework-log.json` or a final result.
It reports damaged journal lines and missing/corrupt images; it cannot restore
evidence that never reached disk. The previous `replay.py` remains the mock
exporter; use `hardware_replay.py` for this experiment.

Early failures can leave partial artifacts. Physical success is intentionally
recorded as unverified.

This directory is also the durable attempt latch, created after station preflight.
Do not delete it or change the output path to silently retry a failed/uncertain
attempt. Review the evidence before arranging another experiment.

Once Astra has ended and hold is confirmed, stop a separately started camera
preview with Ctrl+C. Leave the daemon supporting the arms until normal station
shutdown is appropriate.

## Troubleshooting

| Symptom | Action |
|---|---|
| Safety daemon timeout | Resolve the 6002 service first; an API key cannot fix it. |
| Missing key/confirmation error | Export the key in the launch terminal and include the arm flag after physical identification. |
| Wrong-host error | Run on `rhos-nuc-1`. |
| Preview unavailable/503 | Check camera ownership, live streams and port 8765. |
| Another session ACTIVE | End teleoperation normally and verify hold. |
| Identity/protocol/watchdog mismatch | Resolve the station fault; do not bypass validation. |
| Motor 7 unreadable | Check follower power, gripper CAN/power cable and CAN ID 7. |
| Consumed attempt/FileExistsError | Preserve and inspect the previous attempt. |
| Missing httpx/websockets | Use the exact runner and station Python above; bundled runtime-deps are loaded by the runner. |

## Reference material

- `integrations/inspect_robots/README.md`
- `integrations/inspect_robots/astra-preparation.json`
- `codex-astra-gradual-reason.log`
- `codex-astra-experimental-run-prep-steps.log`
- `codex-daemon-startup-audit.log`


## November 12 larger-motion configuration update

The current runner still points to the consumed run-002 latch; preserve both
saved attempts before preparing another attended episode. Older run-001 names
and budgets above describe the earlier startup recipe; current limits are 180
model calls, 5400 environment steps and 2400 seconds.

Initial clear free-space probes are now 0.08–0.12 rad, up to a 0.15 rad tool-move
cap. Interpolation remains 0.003 rad per step at at most 30 Hz. Near contact the
prompt requests 0.01–0.03 rad or smaller. Experimental follower Kp is now
`[300,450,450,120,30,30,30]`, Kd `[6,6,6,1.8,1.8,1.8,1.8]`.
Changes require normal daemon shutdown/startup and verification of loaded gains;
they do not hot-reload. No hardware trial has validated these values.

After the experiment and normal hardware-owner shutdown, restore initial gains
from the workspace root:

```bash
python3 integrations/inspect_robots/restore_experiment_gains.py --restore
```

Use `--baseline prior --restore` to restore the immediately preceding supplied
gains instead. Without `--restore`, the command only checks backup integrity.
Restart normally after restoration. Details: `YAM/docs/PD_GAINS_20261112.md`.
