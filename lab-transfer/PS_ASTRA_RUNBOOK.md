# Astra experiments on PS: cold start and subsequent trials

Applies to `/home/ps/workspace/jiaxin/yam-astra-experiments`, branch `stations/ps`.
Commands below are **Bash**. If your terminal uses fish, run `bash` first in each
terminal. Use three terminals: A = daemon, B = cameras, C = experiment/operator.
Keep A and B open while running trials.

## 1. What is ready, and what still needs checking

The merged software has passed 42 startup/safety/setup tests and 24 focused
experiment/replay tests with simulated hardware. Offline preflight on November 16
passed with the PS mapping, unused pick-place attempt 1, and no API key in the
checking session. This is readiness to perform an **attended startup**, not live
hardware acceptance. Live daemon health, physical arm/camera identity, clearances,
and RHOS API access must pass below before a trial. Historical tests requiring
unshipped recordings/helpers cannot all run in this checkout.

The PS startup correction holds measured arm positions while starting control and
calibrating grippers; excessive startup/calibration drift aborts initialization.
It has not been accepted by a new physical startup in this session. The upstream
runner now permits **0.80 rad per arm joint per call**, versus 0.45 previously.
Review that change and the approved gains with the station operator before use;
see `YAM/docs/PD_GAINS_20261112.md`. A passing preflight does not verify collision
clearance. `calibration_required: false` means the runner has no extra calibration
step; it does not establish that this robot's existing setup is correct.

## 2. Prepare the station and terminals

In **each terminal**:

```bash
cd /home/ps/workspace/jiaxin/yam-astra-experiments
export PYTHONPATH="$PWD/YAM/gello_software:$PWD/YAM/i2rt"
export PYTHONDONTWRITEBYTECODE=1
```

In C:

```bash
hostname -s
git branch --show-current
git status --short
ls -l profiles/shurui-mcap.yaml profiles/ps-cameras.yaml
ip -details link show type can
pgrep -af '^.*python[^ ]* .*([/]yam_safety_daemon[.]py|experiments[.]yam_safety_daemon|preview_cameras_ps[.]py|astra_run[.]py)'
ss -ltnp | rg ':6001|:6002|:6011|:6012|:8765'
```

Expect hostname `ps`, branch `stations/ps`, and the lab-approved CAN interfaces
`can_follower_l` / `can_follower_r` available. The process/port commands can print
nothing on a cold start. Also check for another teleop/control/camera application
or a service started by another operator: absence of these particular process
names alone does not prove exclusive hardware ownership. If inspection is denied,
resolve ownership with the station owner before startup.

Existing daemon or preview? Identify its checkout, owner and activity before
reusing or stopping it. Do not launch a second owner. To replace your own idle
daemon, use section 9 first. Do source updates only after controlled shutdown.

Local profile paths used here:

- Profile: `profiles/shurui-mcap.yaml` (compatibility filename).
- Left arm: `/home/ps/.config/yam/arms/yam_left.yaml`.
- Right arm: `/home/ps/.config/yam/arms/yam_right.yaml`.
- Camera rig: `profiles/ps-cameras.yaml`.
- Robot RPC: localhost 6001; safety RPC: localhost 6002; preview: localhost 8765.

Do not regenerate profiles for every trial. Review them if hardware changes.
Physically identify the right follower as `can_follower_r`. Support both arms using
the lab's approved arrangement before enable/disable or startup rollback. Gripper
initialization can move fingers; clear people, objects and cables from the arms
and grippers. Keep the station's established emergency stop accessible.

## 3. Cold-start the safety daemon — Terminal A

**This command enables/initializes both follower arms and may calibrate grippers.**
Only run it after section 2 and with an attending operator and physical support.
It reads this station's profile rather than borrowing the other YAM's configs.

```bash
mkdir -p .local/station-logs
set -o pipefail
YAM/gello_software/.venv/bin/python -u -c '
import os, sys, yaml
from pathlib import Path
p = yaml.safe_load(Path("profiles/shurui-mcap.yaml").read_text())
os.execv(sys.executable, [sys.executable, "-u", "-m", "experiments.yam_safety_daemon",
    "--left-config-path", p["arms"]["left"],
    "--right-config-path", p["arms"]["right"],
    "--hardware-port", str(p["network"]["hardware_port"]),
    "--safety-port", str(p["network"]["safety_port"])])
' 2>&1 | tee ".local/station-logs/safety-$(date +%Y%m%d-%H%M%S).log"
```

Wait for `Safety daemon ready: ... state=SAFE_HOLD`. In **C**:

```bash
YAM/gello_software/scripts/safety status --json
```

Require `state: safe_hold`, `hold_confirmed: true`, the correct follower mapping,
and fresh healthy feedback/control threads on both sides. Inspect startup pose
and drift logs. `INITIALIZING`, a listening port, or a ready banner alone is not
sufficient. Any drift, watchdog, stale-feedback or joint-limit fault must be
resolved before continuing; do not change limits/offsets to bypass it. On failed
initialization, support the arms and inspect the failure/rollback before retrying.

**Ctrl+C is a hold request for this daemon, not a shutdown command.** Use section
9 for controlled motor disable and exit. Never use force-kill to restart it.

### Optional gripper diagnostics (enable before cold start)

For the repeated right-gripper vibration, see [GRIPPER_DIAGNOSTICS.md](../YAM/docs/GRIPPER_DIAGNOSTICS.md).
After controlled shutdown, before the daemon startup command above, set:

```bash
export YAM_GRIPPER_TRACE_DIR="$PWD/.local/gripper-traces/$(date +%Y%m%d-%H%M%S)"
export YAM_GRIPPER_TRACE_HZ=250
mkdir -p "$YAM_GRIPPER_TRACE_DIR"
```

This records locally in the daemon, including model-wait intervals. Check the
per-channel `.status.json` files to confirm increasing sample counts, no errors
and no drops. Enable only for an attended diagnostic session initially; this does
not remedy vibration. The updated code requires a normal controlled daemon restart
because its fingerprint changes. No gripper gains, endpoints or guards are altered.

## 4. Cold-start the camera preview — Terminal B

With no other application owning these three cameras:

```bash
set -o pipefail
YAM/gello_software/.venv/bin/python -u preview_cameras_ps.py \
  2>&1 | tee ".local/station-logs/cameras-$(date +%Y%m%d-%H%M%S).log"
```

Open **http://127.0.0.1:8765/** on this computer. For a remote operator, forward
port 8765 over SSH to this computer; the localhost URL refers to the browser's
machine. Leave the preview running.

Verify fresh, correctly labelled views and adequate exposure:

| View | Local configured D405 serial |
| --- | --- |
| main | 262422270050 |
| left | 262422271903 |
| right | 262422272588 |

All three cameras use manual exposure at 10000 microseconds as a comparison
baseline. Recheck brightness after restart and adjust individual cameras if needed.
Confirm each label against the actual physical camera. Check the right wrist,
fingers, target and table are visible. `preview_cameras_ps.py` only opens cameras;
it does not connect to or enable the robot. A camera-busy error means finding the
actual camera owner before retrying, not restarting the safety daemon.

## 5. Choose the trial and enter credentials — Terminal C

Set task and attempt explicitly; do not rely on the runner's default attempt 2:

```bash
TASK=pick-place
ATTEMPT=1
```

| TASK | Goal | Prompt | Output directory relative to repo |
| --- | --- | --- | --- |
| `pick-place` | Pink block to black X on white paper; return right arm to initial pose | `integrations/inspect_robots/astra_exploration.txt` | `integrations/inspect_robots/astra-yam3-run-NNN/` |
| `usb-insertion` | Pick up loose USB-A cable, seat in a hub port, clear gripper | `integrations/inspect_robots/experiments/usb-insertion/prompt.txt` | `integrations/inspect_robots/experiments/usb-insertion/runs/run-NNN/` |

Both use the right arm and require the left arm to remain at its initial pose.
Set up the chosen physical scene with the robot stationary and a safe approach
for hands. Do not push held arms around to reset them. If repositioning is needed,
use the lab's approved attended reset procedure; then repeat readiness checks.

Enter an authorized RHOS API key without showing or saving it in shell history:

```bash
read -r -s -p 'RHOS API key: ' RHOS_API_KEY
printf '\n'
export RHOS_API_KEY
```

This runner uses `gpt-6-astra` via `https://api.rhos.cc/v1/responses`. A key being
present does not verify its validity, quota or network connectivity. The offline
and station checks below do not make a model request.

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --task "$TASK" --attempt "$ATTEMPT"
```

Inspect the printed task, prompt path, output path, mapping and motion ceiling.
Require `credential_present: true` and `attempt_already_consumed: false` for a
fresh trial. Offline preflight creates no episode and does not operate hardware.
Existing attempt directory? Choose an unused number; do not delete evidence.

## 6. Check the live station, then run — Terminal C

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --check-station --task "$TASK" --attempt "$ATTEMPT"
```

Require the final **`Station observations and daemon checks passed`** message.
This checks identity/fingerprints, confirmed hold, feedback and observations.
Without `--approach-target`, this check does not command motion. Initial JSON is
not a successful station check. Do not bypass mismatched identity or guard errors.

Before the next command, confirm physical clearance, stationary left arm, scene,
camera liveness and an attending operator. **The next command can move hardware.**

```bash
set -o pipefail
YAM/gello_software/.venv/bin/python -u integrations/inspect_robots/astra_run.py \
  --run --task "$TASK" --attempt "$ATTEMPT" \
  --right-channel-confirmed can_follower_r \
  2>&1 | tee ".local/station-logs/astra-${TASK}-$(printf '%03d' "$ATTEMPT")-$(date +%Y%m%d-%H%M%S).log"
```

One invocation runs one episode, capped at 270 model calls, 3600 seconds and 16200
steps (not a promise that every wait finishes at the exact time cap). Watch the
physical scene throughout. On trouble use the established stop procedure; Ctrl+C
in C interrupts the runner, whose motion context requests hold. Verify that hold
actually succeeded before approaching. For a deliberate hold from a separate
prepared operator terminal:

```bash
YAM/gello_software/scripts/safety hold
YAM/gello_software/scripts/safety status --json
```

Do not send a hold from a competing session during normal operation. If feedback
or hold confirmation fails, keep people clear, support the arms per the lab
procedure, and resolve the fault; process exit alone does not establish safety.

## 7. Finish, inspect and preserve the result

After the runner returns, in C:

```bash
YAM/gello_software/scripts/safety status --json
```

Require confirmed `safe_hold` with healthy feedback before scene handling. The
runner requests hold before replay generation, but confirm it independently.

Use the exact output path printed in section 5. For pick-place attempt 1:

```bash
RUN_DIR=integrations/inspect_robots/astra-yam3-run-001
ls -l "$RUN_DIR"
cat "$RUN_DIR/attempt-result.json"
```

For USB attempt 1 instead:

```bash
RUN_DIR=integrations/inspect_robots/experiments/usb-insertion/runs/run-001
```

Inspect `attempt.json`, saved `prompt.txt`, `attempt-result.json`, station events,
and generated `replay.html`. `evaluation_completed` is software completion;
`physical_success: unverified` needs your own scene/replay assessment. Record the
actual outcome, initial arrangement, source commit (`git rev-parse HEAD`), task,
attempt and changes in a local note alongside the run. Preserve failed attempts.

Optional video export after the run:

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/export_replay_mp4.py \
  --run-dir "$RUN_DIR" --speed 4 --fps 30 --title "PS $TASK attempt $ATTEMPT"
```

Video export requires Playwright/Chromium and its runtime dependencies; an export
failure does not justify rerunning a consumed trial. Rebuild the replay offline.

## 8. Prepare the following trial

Wait for C to return and check confirmed hold before changing the scene. Each
fresh invocation starts new model history. A repeated task needs a new attempt:

```bash
ATTEMPT=2
```

Or switch built-in tasks, choosing a number unused **within that task's folder**:

```bash
TASK=usb-insertion
ATTEMPT=1
```

Repeat scene setup, offline preflight, live station check, run, and result review
(sections 5–7). Keep the same daemon/preview when healthy and unchanged.

| Change between trials | Action before the next run |
| --- | --- |
| Same task, reset objects | Fresh unused attempt; keep services; repeat checks |
| Pick-place ↔ USB | Change TASK and scene; task namespaces are separate |
| Prompt experiment | Edit the selected prompt after the previous runner exits; new attempt; repeat checks; saved prompt/hash records the revision |
| New task | Add matching goal/instruction/prompt/output namespace in `task_configuration()` in `astra_run.py`; editing only a prompt leaves conflicting goals |
| Camera settings/serials | Stop B with Ctrl+C, review rig config, restart B and verify views; repeat station check |
| Robot source, gains, arm config, branch, or Git update | Controlled daemon shutdown (section 9), review changes, cold-start again and repeat checks |

For a new task, also inspect task-specific guidance in `StationArm` and tool
schemas; a new task ID requires extending CLI choices and resume validation/tests.
Avoid changing motion guards as a routine trial variable. The source task prompts
contain assumptions from YAM3; review their relevance to PS before adopting them.

A **resume** is for an interrupted episode on this same physical station and task,
with the saved pose still matching; it retains history and used budget. It is not
a fresh trial and must not use the other YAM's recordings. First assess offline:

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --task "$TASK" --resume "$RUN_DIR"
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --check-station --task "$TASK" --resume "$RUN_DIR"
```

Only after matching-pose checks and attended clearance, continue with:

```bash
YAM/gello_software/.venv/bin/python -u integrations/inspect_robots/astra_run.py \
  --run --task "$TASK" --resume "$RUN_DIR" \
  --right-channel-confirmed can_follower_r
```

Resume writes a new `resume-TIMESTAMP-ID/` segment under the source run. Do not
combine resume with an attended approach authorization or reposition the robot
just to defeat a pose mismatch. Use a fresh trial after scene/pose changes.

### Recover a wall-clock timeout during a movement

Ordinary resume refuses a `dispatching_or_finished` checkpoint. Explicit recovery
is available for a wall-clock timeout with confirmed final hold, a complete and
unmodified command-acceptance journal, the matching final model response, and
recorded feedback. It accounts for the accepted part of the move, records an
interrupted tool result in the model history, and never replays the old trajectory.
Live identity, hold, feedback, camera and pose checks remain required. This is not
recovery from hardware faults, unknown RPC outcomes or exhausted step/call budgets.
The original checkpoint and journal are never rewritten.

For the PS USB attempt 1, after the original runner has exited, keep the held
robot and scene unchanged and run this observation-only check:

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --check-station --task usb-insertion \
  --resume integrations/inspect_robots/experiments/usb-insertion/runs/run-001 \
  --recover-interrupted-motion
```

Require the final station-check pass, inspect live clearance and the held plug,
and ensure RHOS_API_KEY is still exported in the running terminal. Then:

```bash
YAM/gello_software/.venv/bin/python -u integrations/inspect_robots/astra_run.py \
  --run --task usb-insertion \
  --resume integrations/inspect_robots/experiments/usb-insertion/runs/run-001 \
  --recover-interrupted-motion --right-channel-confirmed can_follower_r
```

The new segment lives under `run-001/resume-TIMESTAMP-ID/`. The segment's wall-clock
allowance starts at 60 minutes again; the cumulative 16200-step and 270-request
limits remain. If a later segment needs recovery, use that segment's directory as
the resume source. Do not restart the safety daemon merely to load these runner
changes; its fingerprinted motor/control code has not changed.

## 9. End the session or restart the daemon safely

1. Let the runner finish or interrupt it in C. Wait for its exit.
2. Request and verify hold in C:

   ```bash
   YAM/gello_software/scripts/safety hold
   YAM/gello_software/scripts/safety status --json
   ```

3. Physically support both arms using the station's approved procedure before
   disabling motors. In an interactive terminal:

   ```bash
   YAM/gello_software/scripts/safety off
   ```

   Type exactly `off` when asked. Motors are disabled; the daemon should exit and
   Terminal A return to its prompt. Ctrl+C alone does not do this. If shutdown
   fails, keep support and diagnose the fault rather than starting another owner.
4. Stop the camera preview in B with Ctrl+C when finished (or keep it for a daemon
   restart if camera configuration is unchanged). Confirm old service ownership
   is gone before relaunching.
5. Remove the key from C when finished:

   ```bash
   unset RHOS_API_KEY
   ```

After source updates, use this checkout on `stations/ps` again. Follow
`lab-transfer/PS_STATION.md` for Git updates and repeat the cold-start checks.
Local profiles, station logs, credentials and recordings stay out of commits.

### Explicitly extend the cumulative step budget (historical trial 1 settings)

Add `--max-total-steps 10800` to the preflight and run commands to extend the
original 5400-step budget. This preserves the cumulative used-step count and
request count. For USB run-001 recovered at step 3143, 7657 steps remain.
These were the settings used during trial 1. As of November 17 the defaults are
16200 steps, 270 requests and 60 minutes per segment. An explicit
`--max-total-steps 10800` still overrides that default downward.
The running process cannot receive this setting retroactively.

### Tracking-stop recovery and nominal task limits

A `PolicyError: arm tracking residual exceeds 0.08 rad at decision boundary`
is a tracking-guard stop, not a budget expiry. Inspect the measured versus
commanded joints and visually check the wrist, plug, hub and cable for contact or
binding. Do not repeat the failed target or relax the tracking guard.

The SDK publishes limits padded outward by 0.15 rad. The Astra runner now restricts
new right-arm targets to the YAM Pro model limits instead. A held starting pose
at most 0.03 rad beyond a nominal limit can be retained for recovery and inward
movement; the task bound does not extend farther outward than that starting pose.
Farther out requires operator review and is rejected. Daemon limits and control
code are unchanged; no daemon restart is needed for this runner change.

For the recorded step-3578 stop on PS, confirmed hold and a complete command
journal allow explicit recovery after the operator found no visible contact:

```bash
YAM/gello_software/.venv/bin/python -u integrations/inspect_robots/astra_run.py \
  --run --task usb-insertion \
  --resume integrations/inspect_robots/experiments/usb-insertion/runs/run-001/resume-20261116-032515-213bce9d \
  --recover-tracking-stop --tracking-stop-clearance-reviewed \
  --max-total-steps 16200 --right-channel-confirmed can_follower_r
```

Use `--check-station` instead of `--run` for observation-only preflight. Tracking
recovery uses the recorded measured hold pose as its new reference, verifies live
pose and health, preserves the unsuccessful target in history, and supplies fresh
images. It does not complete or replay that trajectory. Hardware faults, missing
hold, unknown command outcomes, scene changes, or pose mismatch remain blockers.
Always resume the most recent segment; request/step counters remain cumulative.

### Waypoint rejection before any command was sent

An initial task bound must include both the validated live pose and the restored
command reference (within the existing 0.008 rad pose check). Otherwise clipping
the interpolation against a bound based only on live feedback can create a first
waypoint more than 0.003 rad from the restored reference. The runner now includes
both starting references while retaining nominal model bounds for new targets.

If the recorded error is exactly `EmbodimentFault: waypoint exceeds declared
per-step change`, final hold is confirmed, and there were zero command dispatches
after the saved decision boundary, use `--recover-rejected-motion`. It preserves
the rejected call and explains that nothing executed; the used request still
counts. Any dispatch or acceptance makes this recovery mode reject the source.

For the PS rejected segment, use:

```bash
YAM/gello_software/.venv/bin/python -u integrations/inspect_robots/astra_run.py \
  --run --task usb-insertion \
  --resume integrations/inspect_robots/experiments/usb-insertion/runs/run-001/resume-20261116-032515-213bce9d/resume-20261116-033551-37961473 \
  --recover-rejected-motion --max-total-steps 16200 \
  --right-channel-confirmed can_follower_r
```

Use `--check-station` instead of `--run` to repeat observation-only readiness checks.
Do not combine different recovery mode flags.

## Completed USB run: replays and export

The final segment completed with `give_up`; final hold was confirmed. Every resume
has its own `replay.html`. The original run-001 viewer contains only the first
segment. An index for this recorded run is:
`integrations/inspect_robots/experiments/usb-insertion/runs/run-001/replays.html`.
All four viewers have been regenerated with 1×, 2×, 4×, 8×, 16× and 32× playback.

`.local/latest-usb-run` is a local shortcut to this run's final segment; it is not
a service that automatically follows future experiments. Export this segment at
32× with:

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/export_replay_mp4.py \
  --run-dir .local/latest-usb-run --speed 32 --fps 30
```

Output: `.local/latest-usb-run/replay_32x.mp4` and `replay_32x.json` (stored in the
actual segment directory). Use `--speed 8`, `--speed 16`, `--speed 2.5`, or any
positive multiplier. To export another segment, replace `--run-dir` with its path
from the replay index. These commands export one segment at a time, not the whole
chain concatenated. Full-resolution encoding may take several minutes; it operates
offline without robot access. Short H.264 sample exports were verified at 8×,
16×, 32× and 2.5×; no full-length MP4 was generated during those checks.

## Combine all resume segments into one replay and MP4

Use the original run directory and `--combine-resumes`:

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/export_replay_mp4.py \
  --run-dir integrations/inspect_robots/experiments/usb-insertion/runs/run-001 \
  --combine-resumes --speed 32 --fps 30
```

The combined HTML and MP4 are in `run-001/combined/`. Choose another positive
`--speed` for a different export, or use `--html-only` to skip video encoding.
The combined clock omits unrecorded downtime between segments. Resume boundaries
remain labelled; recorded motion and model-wait intervals inside segments stay.
The HTML needs the source run's frame folders; the MP4 can be shared by itself.

## Current experiment budgets (November 17)

Defaults are 60 minutes (3600 seconds), 16200 cumulative motion steps and 270
cumulative model requests, including retry attempts. A resumed segment starts a
fresh 60-minute timer, while used steps and requests remain charged. Explicit
`--max-total-steps` overrides the default, so remove an old `10800` override or
set `--max-total-steps 16200` to use the new allowance. Limits are shared between
the runner, transport, model policy and resume validation. Existing saved results
and manifests retain their original budget values. A process already running
keeps its loaded budgets; these defaults apply at the next launch.

## Startup failure: unexpected watchdog values on November 17

The PS startup log reported timeout-register values 404352759, 454900248 and
656666873 rather than 8000. Read-only host inspection found another daemon:
PID 1674865, `/home/ps/tianxi/YAM/gello_software/experiments/yam_safety_daemon.py`,
using `can_follower_l` and `can_follower_r` on ports 6011/6012. Its status was
`fault`, `hold_confirmed: false`, with disabled motors and stopped control threads.
These observations are a timestamped diagnosis, not a current-state guarantee.

Different RPC ports do not isolate ownership of the physical CAN buses. The
preflight process search above now catches both module and Python-file launches.
Establish ownership and coordinate the existing controller's supported recovery
or shutdown before another startup. Keep the arms physically supported under the
lab procedure if hold is unconfirmed; do not force-kill, blindly restart, or run
the PS daemon alongside it.

A separate reader defect exists: `_receive_message` in
`YAM/i2rt/i2rt/motor_config_tool/utils.py` returns the first received CAN frame
without matching its motor/register response. Decoding bytes 4–7 from an unrelated
frame can therefore yield a bogus watchdog value. The failed readings do not prove
that persistent motor settings changed. No watchdog settings were written during
this diagnosis. Re-read with a validated response parser after ownership is safe;
do not bypass the verification or write 8000 based on these readings alone.
