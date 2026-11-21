# Human plug-socket demonstration

Use the terminal controller first to compare a human with Astra using the same
right-arm joint controls and main/right camera views. Physical leader-arm teleop
is an alternative for finding a feasible grasp/path, but changes the control
interface and does not isolate Astra's perception/reasoning.

## Camera-only terminal control (recommended)

Prerequisites: the previous Astra run has ended, the existing safety daemon is in
confirmed SAFE HOLD, and `preview_cameras_ps.py` is running on 8765. Leave the
daemon and preview running. The strip must remain unplugged and braced. The
controller does not home or open either arm automatically. Starting from the
previous held pose is allowed; it is saved as the new demonstration's initial pose.

From the repository root:

```bash
cd /home/ps/workspace/jiaxin/yam-astra-experiments

# Live read-only station check: no activation or joint command.
YAM/gello_software/.venv/bin/python integrations/inspect_robots/human_demo.py \
  --check-station --attempt 1

# Start a separate human demonstration; no RHOS_API_KEY needed.
mkdir -p .local/station-logs
set -o pipefail
YAM/gello_software/.venv/bin/python -u integrations/inspect_robots/human_demo.py \
  --run --attempt 1 --right-channel-confirmed can_follower_r \
  2>&1 | tee ".local/station-logs/human-plug-socket-001-$(date +%Y%m%d-%H%M%S).log"
```

Open the **TWO-VIEW CAMERA PAGE** URL printed by the controller (127.0.0.1:8766
plus a random path). It relays the existing 8765 preview and shows only `main`
and `right`; it cannot send motions. Keep the full three-camera page closed for
the comparison. Observe the real work area only for safety; if you use that
additional information or another person moves an object, record `mark ...`
and treat the session as assisted rather than strictly camera-only.

Type commands at `human>`, one at a time. **Pressing Enter executes a move.**
Examples below illustrate syntax, not a prescribed path or safe target pose:

```text
state
j4=-1.10 ; square the brick face
j1+=0.02 j2+=0.02 ; small forward correction
g=0.90 ; open the gripper
{"targets":{"right_j4":-1.10},"note":"square face","phase":"alignment"}
mark Both tips now clearly visible; upper slot selected
done Plug remains seated after release and withdrawal
```

`j0`..`j5` abbreviate `right_j0`..`right_j5`; `g` is `right_gripper`.
`=` sets an absolute target; `+=`/`-=` resolve against the last accepted target,
not measured tracking sag. Unspecified joints retain their commanded targets.
Use `help`, `state`/`observe`, `mark ...`, `give_up <reason>`, or `quit` as needed.
`done` is an operator-reported outcome; it does not verify seating or release for
you. `quit`, EOF, Ctrl+C, or an exception exits through confirmed SAFE HOLD;
there is no automatic retreat or homing. A guard failure ends the session rather
than offering an override. Do not run this concurrently with Astra/leader teleop.

The controller reuses Astra's **actual ToolAdapter interpolation, 30-Hz steps,
0.80-rad per-call cap, nominal bounds, 0.08-rad tracking checks and daemon
acceptance checks**. Left joints/gripper remain at their initial targets. It also
respects the current prompt limits: gripper 0..0.90, J3 >= -1.65, J4 within
[-1.35, 1.35]. These numeric guards are not collision detection. Retain the
prompt's small observed insertion steps and 0.015-rad contact/lag cue. Abort on
the previously observed vibration; this interface cannot fix mechanical chatter.

Human runs go to:

```text
integrations/inspect_robots/experiments/plug-socket/human-runs/demo-001/
```

- `station-events.jsonl`: typed/resolved commands and notes, dispatched/accepted
  waypoints, measured joint feedback, timestamps, camera references and stops.
- `replay-frames/`: main/right JPEGs polled at 5 Hz, plus decision and waypoint
  observations. Left policy frames are black; no real left image is fetched.
- `trajectory.csv`: measured right joints and gripper versus accepted setpoints.
- `attempt.json`, `prompt.txt`, `attempt-result.json`: configuration fingerprints,
  initial full pose, prompt snapshot, outcome and final hold acknowledgement.
- `replay.html`: offline replay, generated after hold. It can be exported with
  the existing MP4 exporter using this human run directory.

These are timestamped samples, not a mathematically exact continuous path. Moving
feedback is sampled by the existing waypoint/observation loop (nominal 30 Hz);
idle feedback is polled at nominal 10 Hz. Camera exposure times remain unknown.
Targets, acceptance and measured positions are distinct. Human attention is not
fabricated: manual decisions store `attention: null`; optional notes document
what you observed. Camera-only compliance is operator-declared, not enforced
against looking directly at the workspace. Continuous preview and lack of API
latency also distinguish this demonstration from an Astra trial.

Optional offline Cartesian export (never connects to the robot):

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/human_demo.py \
  --export-only integrations/inspect_robots/experiments/plug-socket/human-runs/demo-001 \
  --nominal-fk
```

This writes `trajectory-nominal-fk.csv` with nominal grasp-site XYZ in metres in
the **right-base frame** and a 3x3 rotation matrix. It uses model FK, not measured
Cartesian pose, calibrated fingertip/prong coordinates, or table/camera axes.
It is for comparison/analysis, not an automatically executable path.

For the next human demonstration, use `--attempt 2`. Existing folders are never
overwritten; these numbers are independent of Astra's `runs/run-00N` trials.

## Physical control arms with MCAP recording

The local `profiles/ps-leader-teleop.yaml` binds connected leader FTC4Q0SX to
left and FTB3KO66 to right, using copied saved leader calibration in
`profiles/ps-leader-arms/`. Only leader USB identity and Dynamixel calibration
were copied; follower configuration and daemon identity match the original PS
profile. Tianxi and shared configuration files were read only. Source hashes and
bindings are recorded in `.local/connected-leaders/provenance.json`.

This is the existing bimanual leader teleop collector, not the right-only terminal
adapter. It can command both followers and uses its own leader/gripper mapping;
it **does not inherit Astra's 0.90 gripper cap**. Stop if the previously observed
vibration returns. Keep the left leader still if it is intended to remain parked;
that is not the same software lock as the terminal controller.

First exit any manual/Astra runner and verify it has reached confirmed SAFE HOLD.
Stop **only** the standalone `preview_cameras_ps.py` with Ctrl+C in its own terminal,
because this collector opens the same RealSense cameras itself. Leave the daemon
running; do not start a second camera owner or bypass leader alignment checks.

```bash
YAM/gello_software/scripts/teleop --lang en \
  --profile "$PWD/profiles/ps-leader-teleop.yaml" \
  --camera-config "$PWD/profiles/ps-main-right-teleop.yaml" \
  --recording-format mcap --control-hz 30 --state-hz 200 --check

YAM/gello_software/scripts/teleop --lang en \
  --profile "$PWD/profiles/ps-leader-teleop.yaml" \
  --camera-config "$PWD/profiles/ps-main-right-teleop.yaml" \
  --recording-format mcap --control-hz 30 --state-hz 200 \
  --task-name plug-socket-human-leaders
```

Follow the arming prompts. At READY position the leaders and press Enter for
synchronization, then follow the trigger sequence shown: release all, fully
squeeze all, and release all. Wait for RECORDING before demonstrating with the
right leader. Press Enter to finish, then `s` for success or `f` for fail (`d`
discards). Escape/Ctrl+C requests safe stop. A ready preview alone is not yet a
recorded episode.

The selected profile stores recordings under
`/nas/data/yam/plug-socket-human-leaders/mcap/{success,fail}/`. During recording
they are under `.in_progress/`. MCAP contains requested leader action targets,
accepted follower targets, native timestamped follower position/velocity/effort
feedback, and camera streams; 200 Hz is a quality target, not a guarantee of
every motor's actual rate. The separate `ps-main-right-teleop.yaml` config records
and previews only main/right without changing Astra's original three-camera
config. Leader control is a separate experimental condition. Its commands were checked against the local
CLI; no live leader alignment, camera acquisition or motor test was performed
while implementing the terminal controller.
