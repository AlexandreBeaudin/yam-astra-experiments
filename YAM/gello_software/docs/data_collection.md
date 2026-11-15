# YAM Two-Arm and Four-Arm Data Collection

Start or resume a task with:

```bash
scripts/teleop --task-name pick_cube
```

If `--task-name` is omitted, the launcher asks for it before enabling hardware.
Task names may contain letters, numbers, `_`, and `-`.

## Episode workflow

1. Check the device table, task name, and next episode number. Clear the workspace, keep the emergency stop ready, then press Enter once to arm.
2. The followers move slowly toward the current GELLO leader poses. The terminal shows `SYNC · NOT RECORDING`; recording starts automatically after every active pair remains aligned for three control samples.
3. During recording, the terminal shows a red `REC`, elapsed time, the three camera FPS values, current synchronization skew, and a scrolling green oscilloscope-style waveform. Press Enter at task completion.
4. Press `s` for success, `f` for failure, or `d` to discard. The followers hold their current pose; there is no automatic return-home motion.
5. Place every active GELLO leader at the next start pose and press Enter once. The followers synchronize slowly and recording starts automatically; synchronization motion is never saved.
6. Esc or Ctrl+C from any live phase latches `SAFE HOLD` and never disables motors.
7. At the exit menu, press Enter to leave SAFE HOLD active, `g` for gravity compensation, or `o` twice to disable motors. Physically support both arms before `o`.

If the collector exits just as Esc or Ctrl+C is pressed, its command pipe may
already be closed. The launcher detects that race and requests `SAFE HOLD`
directly from the safety daemon; wait for the terminal confirmation before
approaching or releasing either follower.

After SAFE HOLD is confirmed, the launcher reports the number, frames, usable
duration, and recorded timestamp span of success/fail episodes committed during
that teleop process. Historical and discarded episodes are excluded.

All live phases share one dynamic terminal line: cyan for initialization, synchronization,
ready, and SAFE HOLD; red for recording, failures, and exit keys; green for Enter and
successful data; yellow for review and discard; magenta for gravity compensation. Only
save results, errors, and final exit status remain as separate lines.

## Safety daemon

`scripts/teleop` starts (or reconnects to) a separate process that exclusively owns every active follower CAN channel. The recorder and terminal UI never instantiate real follower robots. The daemon remains alive and in `SAFE HOLD` if either process exits.

Before enabling any motor, the daemon reads the persistent `timeout` register from motors 1 through 7 on every active follower. All 14 values in two-arm mode or all 28 values in four-arm mode must be `8000`, the configured 400 ms command watchdog. A zero, mismatch, or unreadable register prevents arming and identifies the side, CAN channel, and motor ID. Startup never writes this register.

While teleoperation is active, the daemon requires independent heartbeats from both the terminal parent and collection child every 200 ms. If either heartbeat is older than one second, or an ordinary robot RPC/control exception occurs, the daemon captures actual follower feedback and latches `SAFE HOLD`. Fresh heartbeats do not automatically resume motion; a new session must explicitly activate control.

Commands are validated before reaching CAN. NaN/Inf and wrong-sized commands immediately latch HOLD. Arm joints remain limited to 0.20 rad per 30 Hz frame; up to five consecutive limited frames let the followers catch up at that bounded rate, while the sixth consecutive limited frame is rejected and latches HOLD. Any non-limited arm frame resets this counter. Gripper commands have no slew limit, but remain clamped to the normalized `[0,1]` range and do not contribute to the consecutive arm-limit counter. A live ACTIVE session cannot be replaced by a second terminal.

The daemon also supervises every CAN control thread, robot server thread, and feedback freshness. A lost low-level thread enters latched `FAULT`; the UI does not claim active hold in that state. It publishes the actual arm limits loaded by each I2RT hardware model, plus the normalized `[0,1]` gripper limits, so replay can reject an unsafe file before motion. Temperature and control-loop frequency are reported for diagnosis, without an unverified automatic temperature cutoff. Inspect or control the persistent daemon without GELLO devices using:

```bash
scripts/safety status
scripts/safety hold
scripts/safety gravity
scripts/safety off       # requires typing the exact word off interactively
```

The safety layers are:

1. `SAFE HOLD`: active position hold plus gravity compensation at the captured current pose.
2. Persistent daemon: continues servicing the low-level control loops when the UI or recorder dies.
3. Motor watchdog: if the daemon, Python runtime, or host dies, each motor falls back to hardware damping after 400 ms.

The third layer is damping, not active position hold. It reduces an uncontrolled fall but cannot guarantee that a loaded arm will maintain its pose. Keep a physical emergency stop within reach and use mechanical support where a fall could cause injury or equipment damage.

Logs are stored separately under `~/.local/state/yam-teleop/` as `teleop-*.log` and `safety-*.log`. If a previous daemon is still healthy, a new `scripts/teleop` run attaches to it instead of treating port 6001 as an error.

After a daemon protocol or arm-topology upgrade, physically support every follower,
run `scripts/safety off`, type `off`, and wait for the old daemon to exit. The next
`scripts/teleop` invocation starts a daemon with the current two- or four-arm topology.
Do not kill a daemon that may still be actively supporting an arm.

## RealSense cameras

Normal teleoperation always records RGB from this fixed rig at 640×480 and 30 FPS:

| View | Model | Serial |
|---|---|---|
| `main` | D435 | `<main-serial>` |
| `left` | D405 | `<left-serial>` |
| `right` | D405 | `<right-serial>` |

To keep the three views comparable, configure each camera's exposure explicitly
in the local camera-rig YAML. The current rig uses automatic exposure for the
D435 `main` view and `10000` microseconds for both D405 side views:

```yaml
main:
  auto_exposure: true
right:
  auto_exposure: false
  exposure_us: 10000
```

`exposure_us` is valid only with `auto_exposure: false`. The launcher enables
automatic exposure before the first frame, then applies a configured manual
exposure after that frame. This avoids a D435 startup stall observed when manual
exposure is set before the first frame. If lighting or framing changes, confirm
that the D435 automatic exposure is suitable for the task before collecting.

The launcher binds cameras by serial number, not `/dev/video*`, so reconnecting or
rebooting does not exchange views. All three links must enumerate as USB 3.x. Check
identity and sustained streaming without starting the safety daemon or commanding the
followers:

```bash
scripts/teleop --check
scripts/teleop --check --depth
```

Every camera-enabled `scripts/teleop` run performs this stream gate before its
arming prompt or safety-daemon session. If a camera enumerates but returns no
frames, collection is blocked before the followers are armed; restore the camera
stream and rerun `scripts/teleop --check`.

For four-arm collection without the D435 main view, select the dedicated four-wrist
configuration. It records `left`, `right`, `third`, and `fourth` D405 RGB streams:

```bash
scripts/teleop --arm-count 4 \
  --camera-config configs/cameras/realsense_four_wrist.yaml --check
scripts/teleop --arm-count 4 \
  --camera-config configs/cameras/realsense_four_wrist.yaml \
  --task-name pick_cube
```

This mode does not create an `observations/images/main` dataset. Confirm the
physical `third` and `fourth` views before collecting and swap their serials in
the configuration if the labels are reversed.

RGB is mandatory whenever cameras are enabled. Depth is off by default; enable
aligned `uint16` depth from every view with:

```bash
scripts/teleop --task-name pick_cube --depth
```

Use `--no-cameras` only for explicit robot-only debugging. It cannot be combined
with `--depth`:

```bash
scripts/teleop --task-name debug_robot --no-cameras
```

A saved timestep must contain robot data and one fresh frame from all three cameras.
The maximum cross-camera skew is 25 ms and maximum frame age is 100 ms. A transient
sync miss turns the camera status yellow and drops that entire timestep; it never
writes partial or duplicated camera data. Ten consecutive misses, a disconnect, an
acquisition error, or a full asynchronous writer queue ends the episode, requests
SAFE HOLD, and leaves an incomplete file for recovery. On the next launch, classify
that file with `f` to preserve it as failure or `d` to discard it.

Uncompressed bandwidth is approximately 83 MB/s (5.0 GB/min) for three RGB streams;
depth adds approximately 55 MB/s (3.3 GB/min). HDF5 uses per-frame LZF compression,
so actual storage depends strongly on the scene and depth content. Check free space
and measure representative episodes before a long collection session.

## Storage

The launcher requires this exact link and refuses to arm if its target is unavailable or unwritable:

```text
${YAM_WORKSPACE}/data -> /mnt/sdb/YAM/data
```

Episodes are stored as:

```text
data/<task_name>/success/0001.hdf5
data/<task_name>/fail/0002.hdf5
data/<task_name>/.in_progress/<temporary-file>.hdf5
```

Numbering continues across `success` and `fail` when a task is reopened. Discarded episodes do not consume a number. Interrupted temporary episodes must be classified as failure or discarded before hardware starts.

The ACT-compatible datasets are:

```text
/observations/qpos       float32 [T,14 or 28]
/observations/qvel       float32 [T,14 or 28]
/observations/effort     float32 [T,14 or 28]
/action                  float32 [T,14 or 28]
/leader_action           float32 [T,14 or 28]
/timestamp               float64 [T]
/observations/images/main        uint8 [T,480,640,3]
/observations/images/left        uint8 [T,480,640,3]
/observations/images/right       uint8 [T,480,640,3]
/observations/depth/<view>       uint16 [T,480,640]  # only with --depth
/observations/camera_timestamp/<view>    float64 [T]
/observations/camera_frame_number/<view> uint64 [T]
/camera_sync_skew                float32 [T]
```

New recordings use schema 1.4 at a fixed 30 Hz, with or without cameras. Schema 1.4
stores 14 values for two arms or 28 values for four arms and requires actual follower
position, velocity, and effort feedback for every DOF. It stores the actual post-limiter follower command in `/action` and the raw
GELLO command in `/leader_action`. Camera order and serial/profile metadata are stored
in the root attributes, together with the sync policy and observed miss/skew statistics.
Replay remains backward-compatible with schema 1.1 through 1.3 recordings.

Before an episode can be marked complete or moved into `success`/`fail`, the shared
episode validator checks schema metadata, 30 Hz configuration, non-empty and equal
dataset lengths, finite robot values, normalized grippers, strictly increasing
timestamps, and complete synchronized camera rows. A validation failure leaves the
file under `.in_progress` for recovery instead of publishing invalid training data.
Recovery of an interrupted recording truncates every robot, image, depth, timestamp,
frame-number, and sync-skew dataset to the last common complete timestep and records
recovery metadata in the file attributes.

## Duration statistics

Recursively summarize one task, the complete local data root, or a copied server
directory:

```bash
scripts/data_duration.sh /path/to/task
scripts/data_duration.sh /path/to/data --details
```

The command is read-only and supports completed schema 1.1, 1.2, 1.3, and 1.4
episodes. `USABLE` is `num_steps / control_hz`; `recorded span` is the final
timestamp minus the first timestamp, summed independently per episode. Invalid,
incomplete, unreadable, and non-success/fail HDF5 metadata are reported but
excluded from the totals. The fast scan reads only attributes and timestamp
endpoints; it is not a replacement for full episode quality validation. The
command exits with status 1 if any invalid files were found.

## Replay a recorded episode

Browse the episodes in one task:

```bash
scripts/replay.sh pick_cube --speed 0.5
```

Or browse every task under `YAM/data` first:

```bash
scripts/replay.sh --speed 0.5
```

Replay accepts speed factors from `0.1` through `1.0`; the default is `1.0`.
Task selection accepts a displayed row number or exact task name. Episode selection
shows success and fail data together, newest first, with 20 rows per page. Use `n/p`
to change pages, a row number to select a visible row, or a zero-padded number such
as `0007` to select that episode directly.

Selection and HDF5 validation happen before hardware startup. The script rejects
incomplete data, schema/path metadata mismatches, invalid timestamps, non-finite
actions or qpos, gripper values outside `[0,1]`, and arm command jumps beyond the
daemon limit. After connecting, it also checks every recorded action and qpos against
the actual joint limits published by the safety daemon. A daemon started with older
code must be restarted once before replay. It then moves slowly to the
first recorded `/observations/qpos`, verifies that measured start pose, and then
replays every `/action` using the recorded timestamps. GELLO devices and cameras
are not required. Replay start-pose verification allows up to `0.08 rad` arm
tracking error for three consecutive samples. The optional return-to-zero check
uses `0.05 rad` for three consecutive samples, while still commanding the exact
configured zero target.

During replay, each measured arm pose is compared with the matching recorded
`/observations/qpos` frame. An arm-joint error above `0.25 rad` for five consecutive
frames immediately enters SAFE HOLD; grippers are excluded because contact can create
normal tracking error. The scheduler never sends overdue frames back-to-back. If it
falls behind the original scaled timeline by more than `max(0.10 s, two times the
median recorded sample interval / speed)`, it enters SAFE HOLD without skipping frames or trying to
catch up.

After the final frame, the followers enter SAFE HOLD before asking what to do next:

- Press Enter to keep the final pose in SAFE HOLD.
- Press `z` to return every follower in the replayed episode to `[0,0,0,0,0,0,1]`, verify the zero pose, and remain in SAFE HOLD.
- Press Ctrl+C at any time to stop motion and retain SAFE HOLD.

## Return both followers to mechanical zero

Clear the workspace and keep the emergency stop accessible, then run:

```bash
scripts/return_zero.sh
```

The target for each side is `[0, 0, 0, 0, 0, 0, 1]`; the last value opens the
gripper. GELLO devices are not required. The command displays current feedback
and waits for Enter before moving both followers together at a limited rate.

After verifying the final joint feedback, the command leaves the followers in
confirmed `SAFE HOLD`. It never enters gravity compensation and never disables
the motors. Ctrl+C and command failures also request `SAFE HOLD`.

## Visualize a recorded episode

Browse every task and then choose an episode interactively:

```bash
scripts/visualize.sh
```

Limit browsing to one task, or select an episode without a menu:

```bash
scripts/visualize.sh pick_cube
scripts/visualize.sh pick_cube --episode 0007
```

An HDF5 file outside the standard data layout can also be rendered directly.
In that case an explicit output path is required:

```bash
scripts/visualize.sh \
  --input /path/to/episode.hdf5 \
  --output /path/to/episode.mp4
```

The generated 1920x1080 H.264 MP4 synchronizes three RGB camera views with
two or four follower joint timelines. Each timeline shows all six arm joints,
the gripper, current values, and a moving time cursor. Camera datasets remain
lazy-loaded so the complete video is never held in memory. The production camera
order is always `main`, `left`, `right`, taken from the HDF5
attribute rather than alphabetically sorted. If an imported episode has more than
three cameras, choose its display order explicitly:

```bash
scripts/visualize.sh pick_cube --episode 0007 \
  --cameras main left right
```

Episodes recorded before cameras were connected are valid and produce a
joint-only MP4. By default the frame rate comes from the HDF5 `control_hz`
attribute; use `--fps 30` to override it. Existing output is protected unless
`--force` is passed. Standard-layout output is written separately from source
data as:

```text
data/<task_name>/visualizations/success/0001.mp4
data/<task_name>/visualizations/fail/0002.mp4
```

Rendering requires `ffmpeg`, but never connects to GELLO, CAN, or the safety
daemon and never commands either follower.

## Independent-rate MCAP recording

The HDF5 row-aligned behavior described above remains the default. For independent camera, state, and action streams, see [multirate_mcap.md](multirate_mcap.md). MCAP uses a separate task subdirectory and can be causally exported to the existing HDF5 schema.
