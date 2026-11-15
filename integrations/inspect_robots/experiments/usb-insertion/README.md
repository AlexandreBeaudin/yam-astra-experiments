# USB-A insertion experiments on YAM3

All commands are **fish**, on `rhos-nuc-1`. Use the working experiment checkout
`/home/rhos/workspace/jiaxin/shurui`, not the GitHub transfer snapshot.

The task prompt is `experiments/usb-insertion/prompt.txt` relative to the integration
folder. `astra_run.py --task usb-insertion` selects it together with the USB scene
goal, tool guidance, task name, replay title and separate output namespace.
No change to the block prompt is needed. Omitting `--task` retains pick-and-place.

## Files and recording layout

```text
integrations/inspect_robots/
  astra_run.py                         shared runner and unchanged hardware guards
  astra_exploration.txt                pick-and-place prompt, preserved
  astra-yam3-run-001/ ...              existing pick-and-place episodes, preserved
  experiments/usb-insertion/
    prompt.txt                        USB prompt supplied by the operator
    README.md                         this guide
    console-logs/                     optional tee logs outside episode latches
    runs/
      run-001/                        created only when the first live run starts
      run-002/ ...                    subsequent USB experiments
```

Each started episode records:

- `attempt.json`: task ID, goal, prompt/runner hashes, budgets and replay title.
- `prompt.txt`: a snapshot of the USB prompt used for that episode.
- `station-events.jsonl`: camera receipt times, measured joints, command dispatch
  and acceptance, decision observations, API round trips and motion-step timing.
- `replay-frames/`: journal camera images, including continuous preview samples
  during model waits (target 5 Hz; actual sample timestamps preserved).
- `wire/`, `transcripts/`, `records/`, `actions/`, `frames/`: framework/model/tool
  evidence as produced by the existing recording path.
- `checkpoint.json`: recovery state at model boundaries, when available.
- `attempt-result.json`: termination, evaluation status and final hold outcome.
- `framework-log.json`: framework export when evaluation reaches that export;
  hard interruptions can leave only incremental journal/checkpoint evidence.
- `replay.html`, `replay_assets/`: generated in normal cleanup after the motion
  session ends. If cleanup is interrupted, regenerate offline from the journal.
- `replay_4x.mp4` and companion JSON: created by the separate exporter command.

This is the existing preview/JPEG and execution recording, not raw camera video,
force/torque sensing or electrical USB enumeration. Visual `done` is not independent
proof of electrical connection; note any manually verified connection separately.
Keep the whole run directory to preserve all evidence and the HTML's local images.

## 1. Select a fresh USB episode and run offline preflight

```fish
cd /home/rhos/workspace/jiaxin/shurui
set usb_attempt 1
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --task usb-insertion --attempt $usb_attempt
```

Check `task_id: usb-insertion`, the USB `prompt_path`, `effective_cap_rad: 0.8`,
`max_gripper_delta: 1.0`, and output ending in `usb-insertion/runs/run-001`.
`attempt_already_consumed` must be false. This command does not connect to the
robot or model. It does not create/consume a run directory. **Do not mkdir the
individual run-001 folder yourself**: existence is the one-episode latch.

## 2. Reuse the YAM3 services; check them before the run

If the existing safety daemon and preview used for pick-and-place are still running
from this checkout, keep them running. Switch task objects while the station is in
confirmed SAFE HOLD and the workspace is clear for the new episode. Do not run a
pick-and-place episode at the same time: both tasks control the same robot and
share the same exclusive lock.

```fish
pgrep -af '[y]am_safety_daemon'
pgrep -af '[p]review_cameras.py'
ss -ltnp 'sport = :6002'
ss -ltnp 'sport = :8765'
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --task usb-insertion --attempt $usb_attempt --check-station
```

Require the final message `Station observations and daemon checks passed`, not
just the initial JSON. Open the existing preview in a browser on the NUC:

```fish
xdg-open http://127.0.0.1:8765/
```

Confirm `main` sees the workspace and `right` sees the physical right gripper.
Arrange the loose cable/hub so the tip and port faces can be observed; keep the
insertion approach and withdrawal path clear. The 0.80 cap is a ceiling, not an
insertion increment. The USB tool guidance asks for small observed moves near ports.

### Only if services are absent: attended cold startup

Do not launch another CAN owner or camera owner. If a daemon exists but is unhealthy,
resolve it through the normal station hold/shutdown procedure; do not kill it while
it supports the arms. These startup commands are provided for the established YAM3
hardware/configuration and do not adapt another robot.

Terminal A — **initializes/enables both followers**, potentially calibrating grippers;
use the lab's normal physical support and supervised startup procedure:

```fish
cd /home/rhos/workspace/jiaxin/shurui
set -gx PYTHONPATH "$PWD/YAM/gello_software:$PWD/YAM/i2rt"
YAM/gello_software/.venv/bin/python -u -c '
import os, sys, yaml
from pathlib import Path
p = yaml.safe_load(Path("profiles/shurui-mcap.yaml").read_text())
os.execv(sys.executable, [sys.executable, "-u", "-m", "experiments.yam_safety_daemon",
    "--left-config-path", p["arms"]["left"],
    "--right-config-path", p["arms"]["right"],
    "--hardware-port", str(p["network"]["hardware_port"]),
    "--safety-port", str(p["network"]["safety_port"])])
'
```

Wait for SAFE_HOLD. Terminal B — camera-only service, if no preview owner exists:

```fish
cd /home/rhos/workspace/jiaxin/shurui
set -gx PYTHONPATH "$PWD/YAM/gello_software:$PWD/YAM/i2rt"
YAM/gello_software/.venv/bin/python -u preview_cameras.py
```

Leave both service terminals open. In the experiment terminal, repeat the read-only
`--check-station` command above. Do not restart services merely to change tasks.

## 3. Run one USB episode

Use the same experiment terminal as step 1; re-enter `set usb_attempt 1` if you open
a fresh terminal. Physically confirm the logical right follower is `can_yam_a`.
Enter the API key only if it is not already exported in this terminal:

```fish
if not set -q RHOS_API_KEY; or test -z "$RHOS_API_KEY"
    read -s -P 'RHOS API key: ' RHOS_API_KEY
    echo
end
set -gx RHOS_API_KEY "$RHOS_API_KEY"
```

Run, optionally keeping a readable console log as well as the automatic evidence:

```fish
set usb_log integrations/inspect_robots/experiments/usb-insertion/console-logs/attempt-$usb_attempt-(date +%Y%m%d-%H%M%S).log
YAM/gello_software/.venv/bin/python -u integrations/inspect_robots/astra_run.py \
  --task usb-insertion --attempt $usb_attempt --run \
  --right-channel-confirmed can_yam_a 2>&1 | tee $usb_log
set usb_status $pipestatus[1]
echo "Runner exit status: $usb_status"
```

Live motion occurs only with `--run`. The left arm remains fixed by the existing
adapter. Caps, per-step interpolation, bounds, watchdog/feedback checks, 0.08 rad
lag guard and reasoning effort=medium are unchanged. The task ends after release
and withdrawal with the plug seated; it does **not** inherit block-task return-to-rest.

For the next episode:

```fish
set usb_attempt 2
```

Repeat preflight, station check and run with that number. A failed/aborted run still
consumes its number; never delete its folder to reuse the attempt. USB run-001 and
pick-and-place astra-yam3-run-001 are independent records.

## 4. Open the replay and export an MP4

For USB episode 1:

```fish
set usb_run integrations/inspect_robots/experiments/usb-insertion/runs/run-001
xdg-open $usb_run/replay.html
```

The normal cleanup creates the interactive HTML. To regenerate it offline, including
after an interrupted cleanup:

```fish
YAM/gello_software/.venv/bin/python integrations/inspect_robots/export_replay_mp4.py \
  --run-dir $usb_run --html-only --title 'USB-A cable insertion'
```

Export the full synchronized 4×, 30 fps video (this **actually runs the exporter**):

```fish
YAM/gello_software/.venv/bin/python integrations/inspect_robots/export_replay_mp4.py \
  --run-dir $usb_run --speed 4 --fps 30 --title 'USB-A cable insertion'
```

Wait for rendering to finish. The final file appears as
`experiments/usb-insertion/runs/run-001/replay_4x.mp4`; encoding uses a temporary
folder until completion. Then:

```fish
ls -lh $usb_run/replay_4x.mp4
ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate \
  -show_entries format=duration,size -of json $usb_run/replay_4x.mp4
xdg-open $usb_run/replay_4x.mp4
```

Change `usb_run` to `run-002` for episode 2. A video alone is shareable; for interactive
HTML copy the complete run folder so JPEG/font paths still resolve. MP4 export is
offline and requires no API key or robot services.

## 5. Resume and return to the block task

Prefer a new attempt after rearranging the scene. Resume is for a saved unanswered
model boundary with the same robot/pose/scene; it is not replaying an old trajectory.
If you need to assess a USB checkpoint, start with offline assessment:

```fish
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --task usb-insertion --resume $usb_run
```

Do not add `--run` without verifying that recovery is appropriate and live pose checks
pass. The runner rejects cross-task resume. Legacy checkpoints lacking a task ID
are treated as pick-and-place. Resumed model histories retain their original prompt;
editing the USB prompt affects new episodes, not already saved histories.

To switch back to pick-and-place, use the original command with an unused block
attempt number. Choose the next available one; it has its own sequence:

```fish
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --task pick-place --attempt 16
```

This example is offline preflight only. Existing prompts and run directories were
not moved or overwritten by USB setup. Source modifications are backed up with
`.usb-setup-<timestamp>.bak` names alongside the original files.
